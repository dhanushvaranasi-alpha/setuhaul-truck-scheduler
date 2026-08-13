import uuid

from clock import IST, Clock

from ..models import (
    AppointmentStatus,
    Cancelled,
    DriverContext,
    Recorded,
    Rejected,
    Released,
    ShipmentState,
    ShipmentSummary,
)
from .holds import find_active_hold_group
from .holds import release_hold as _release_hold_rows

CANCELLABLE_STATUSES = ("PENDING_CONFIRMATION", "CONFIRMED")

# Plain-English phrasing for shipments.current_status, driver-facing (never
# expose the raw enum or a guessed phrasing to the LLM — compute it here so
# every caller sees the same wording).
STATUS_LABELS = {
    "PLANNED": "not dispatched yet",
    "ASSIGNED": "scheduled",
    "IN_TRANSIT": "on the way",
    "AT_GATE": "checked in at the gate",
    "WAITING": "at the gate",
    "IN_DOCK": "unloading now",
    "COMPLETED": "completed",
    "CANCELLED": "cancelled",
}


def owns_shipment(con, shipment_id: str, driver_id: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM shipments WHERE shipment_id = %s AND driver_id = %s",
        (shipment_id, driver_id),
    ).fetchone()
    return row is not None


def resolve_driver_context(con, driver_id: str) -> DriverContext:
    rows = con.execute(
        """
        SELECT s.shipment_id, s.order_reference, s.current_status, s.priority_code, f.city
        FROM shipments s
        JOIN facilities f ON f.facility_id = s.destination_facility_id
        WHERE s.driver_id = %s AND s.current_status NOT IN ('COMPLETED', 'CANCELLED')
        ORDER BY s.created_at
        """,
        (driver_id,),
    ).fetchall()
    return DriverContext(
        status="driver_context",
        driver_id=driver_id,
        active_shipments=[
            ShipmentSummary(
                shipment_id=r[0],
                order_reference=r[1],
                current_status=r[2],
                status_label=STATUS_LABELS.get(r[2], r[2].replace("_", " ").lower()),
                destination_city=r[4],
                priority_code=r[3],
            )
            for r in rows
        ],
    )


def get_shipment_state(con, shipment_id: str) -> ShipmentState:
    row = con.execute(
        """
        SELECT current_status, appointment_status, planned_dock_code, span_start_ts, span_end_ts,
               effective_eta_ts, eta_confidence
        FROM v_inbound_operational_state
        WHERE shipment_id = %s
        """,
        (shipment_id,),
    ).fetchone()
    current_status, appointment_status, dock_code, span_start, span_end, eta, confidence = row
    return ShipmentState(
        status="shipment_state",
        shipment_id=shipment_id,
        current_status=current_status,
        appointment_status=appointment_status,
        dock_code=dock_code,
        span_start=span_start.astimezone(IST).isoformat() if span_start else None,
        span_end=span_end.astimezone(IST).isoformat() if span_end else None,
        effective_eta=eta.astimezone(IST).isoformat(),
        eta_confidence=confidence,
    )


def record_eta_update(
    con, shipment_id: str, declared_eta: str, confidence: str, source: str, clock: Clock
) -> Recorded:
    eta_update_id = f"ETA-{uuid.uuid4().hex[:12]}"
    con.execute(
        """
        INSERT INTO eta_updates
            (eta_update_id, shipment_id, source_type, declared_eta_ts, confidence_code, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (eta_update_id, shipment_id, source, declared_eta, confidence, clock.now()),
    )
    return Recorded(status="recorded", record_id=eta_update_id)


def record_driver_constraint(
    con, shipment_id: str, constraint_type: str, value: str, clock: Clock
) -> Recorded:
    constraint_id = f"CONS-{uuid.uuid4().hex[:12]}"
    con.execute(
        """
        INSERT INTO driver_constraints
            (constraint_id, shipment_id, constraint_type, constraint_value, is_active, created_at)
        VALUES (%s, %s, %s, %s, TRUE, %s)
        """,
        (constraint_id, shipment_id, constraint_type, value, clock.now()),
    )
    return Recorded(status="recorded", record_id=constraint_id)


def get_appointment_status(con, appointment_id: str) -> AppointmentStatus | Rejected:
    row = con.execute(
        """
        SELECT a.appointment_status, d.dock_code, a.span_start_ts, a.span_end_ts
        FROM appointments a LEFT JOIN docks d ON d.dock_id = a.dock_id
        WHERE a.appointment_id = %s
        """,
        (appointment_id,),
    ).fetchone()
    if row is None:
        return Rejected(status="rejected", reason="NOT_FOUND", detail="No such appointment.")
    appointment_status, dock_code, span_start, span_end = row
    return AppointmentStatus(
        status="appointment_status",
        appointment_id=appointment_id,
        appointment_status=appointment_status,
        dock_code=dock_code,
        span_start=span_start.astimezone(IST).isoformat() if span_start else None,
        span_end=span_end.astimezone(IST).isoformat() if span_end else None,
    )


def cancel_appointment(con, appointment_id: str, reason: str, clock: Clock) -> Cancelled | Rejected:
    row = con.execute(
        "SELECT appointment_status FROM appointments WHERE appointment_id = %s",
        (appointment_id,),
    ).fetchone()
    if row is None:
        return Rejected(status="rejected", reason="NOT_FOUND", detail="No such appointment.")
    if row[0] not in CANCELLABLE_STATUSES:
        return Rejected(
            status="rejected",
            reason="NOT_CANCELLABLE",
            detail=f"Appointment is {row[0]}, not cancellable.",
        )
    con.execute(
        """
        UPDATE appointments
        SET appointment_status = 'CANCELLED', cancelled_at = %s, cancellation_reason = %s,
            is_current = FALSE, updated_at = %s
        WHERE appointment_id = %s
        """,
        (clock.now(), reason, clock.now(), appointment_id),
    )
    return Cancelled(status="cancelled", appointment_id=appointment_id)


def release_hold(con, shipment_id: str, reason: str) -> Released | Rejected:
    hold_group_id = find_active_hold_group(con, shipment_id)
    if hold_group_id is None:
        return Rejected(
            status="rejected", reason="NO_ACTIVE_HOLD", detail="No hold found for this shipment."
        )
    _release_hold_rows(con, hold_group_id, reason)
    return Released(status="released", hold_group_id=hold_group_id)
