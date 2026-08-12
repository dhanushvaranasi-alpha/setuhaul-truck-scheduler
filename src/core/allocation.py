from dataclasses import dataclass

from clock import Clock
from ..config import get_allocation_policy
from ..models import AllocationPolicy


@dataclass(frozen=True)
class ScoreBreakdown:
    shipment_id: str
    importance: float
    waiting: float
    facility_fault: float
    perishable: float
    past_slot: float
    eta_uncertainty: float
    fairness: float

    @property
    def total(self) -> float:
        return (
            self.importance
            + self.waiting
            + self.facility_fault
            + self.perishable
            + self.past_slot
            + self.eta_uncertainty
            + self.fairness
        )


def score_shipment(row: dict, now, wins: dict[str, int], policy: AllocationPolicy) -> ScoreBreakdown:
    """Port of score_final.py. row keys: shipment_id, priority_code,
    eta_confidence, temperature_control_required, carrier_id, gate_in_ts,
    queue_state, span_start_ts."""
    # Waiting counts from max(gate_in_ts, span_start_ts): INVOLUNTARY waiting
    # only (I9). Measuring from gate-in pays a truck for arriving early.
    if row["gate_in_ts"]:
        start = row["gate_in_ts"]
        if row["span_start_ts"]:
            start = max(start, row["span_start_ts"])
        hours_waiting = max(0.0, (now - start).total_seconds() / 3600)
    else:
        hours_waiting = 0.0

    fault = row["queue_state"] == "WAITING_DOCK_UNAVAILABLE"

    past = (
        max(0.0, (now - row["span_start_ts"]).total_seconds() / 3600)
        if row["span_start_ts"]
        else 0.0
    )

    return ScoreBreakdown(
        shipment_id=row["shipment_id"],
        importance=policy.importance_points[row["priority_code"]],
        waiting=policy.waiting_linear * hours_waiting
        + policy.waiting_quadratic * hours_waiting * hours_waiting,
        facility_fault=policy.facility_at_fault * fault,
        perishable=policy.perishable * (1 if row["temperature_control_required"] else 0),
        past_slot=policy.hours_past_booked_slot * past,
        eta_uncertainty=policy.eta_uncertainty.get(row["eta_confidence"], 0.0),
        fairness=0.0
        if fault
        else policy.carrier_fairness_per_contested_win * wins.get(row["carrier_id"], 0),
    )


def get_contested_wins(con) -> dict[str, int]:
    rows = con.execute(
        """
        SELECT s.carrier_id, COUNT(*) FROM appointments a
        JOIN shipments s ON s.shipment_id = a.shipment_id
        WHERE a.is_current = TRUE AND a.appointment_status IN ('CONFIRMED','IN_PROGRESS')
        GROUP BY s.carrier_id
        """
    ).fetchall()
    return {carrier_id: n for carrier_id, n in rows}


def get_competing_shipments(con, facility_id: str) -> list[dict]:
    # Ported directly from score_final.py's query shape: v_latest_eta (not
    # v_inbound_operational_state) so a shipment with no eta_updates row
    # carries a NULL confidence rather than the view's COALESCE-to-MEDIUM
    # default, and appointment_slots.slot_start_ts (the assigned anchor slot)
    # rather than appointments.span_start_ts, matching what the reference
    # table was computed against.
    rows = con.execute(
        """
        SELECT s.shipment_id, s.priority_code, e.eta_confidence,
               s.temperature_control_required, s.carrier_id,
               c.gate_in_ts, c.queue_state, sl.slot_start_ts
        FROM shipments s
        LEFT JOIN v_latest_eta e ON e.shipment_id = s.shipment_id
        LEFT JOIN facility_checkins c ON c.shipment_id = s.shipment_id
        LEFT JOIN appointments a ON a.shipment_id = s.shipment_id AND a.is_current
        LEFT JOIN appointment_slots sl ON sl.slot_id = a.slot_id
        WHERE s.destination_facility_id = %s
          AND (c.queue_state LIKE 'WAITING%%' OR s.current_status IN ('IN_TRANSIT','ASSIGNED'))
          AND s.required_dock_type IN ('STANDARD','ANY')
        """,
        (facility_id,),
    ).fetchall()
    columns = [
        "shipment_id",
        "priority_code",
        "eta_confidence",
        "temperature_control_required",
        "carrier_id",
        "gate_in_ts",
        "queue_state",
        "span_start_ts",
    ]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def rank_shipments(con, facility_id: str, clock: Clock) -> list[ScoreBreakdown]:
    policy = get_allocation_policy()
    wins = get_contested_wins(con)
    rows = get_competing_shipments(con, facility_id)
    scored = [score_shipment(row, clock.now(), wins, policy) for row in rows]
    scored.sort(key=lambda b: -b.total)
    return scored
