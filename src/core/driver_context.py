import uuid

from clock import IST, Clock

from ..models import (
    ActiveDockEvent,
    ActiveEscalation,
    AppointmentStatus,
    Cancelled,
    Checkin,
    CurrentAppointment,
    DriverContext,
    LatestEta,
    PendingNotification,
    Recorded,
    Rejected,
    Released,
    ShipmentInfo,
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
    if not rows:
        # No live shipment — fall back to recently cancelled ones so the
        # agent has something to resolve get_shipment_state against and can
        # tell the driver what happened, instead of finding nothing at all.
        rows = con.execute(
            """
            SELECT s.shipment_id, s.order_reference, s.current_status, s.priority_code, f.city
            FROM shipments s
            JOIN facilities f ON f.facility_id = s.destination_facility_id
            WHERE s.driver_id = %s AND s.current_status = 'CANCELLED'
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


def get_shipment_state(con, shipment_id: str, clock: Clock) -> ShipmentState:
    """Complete operational context for a shipment — every field the agent
    needs to reason about what to ask/tell a driver without scenario-specific
    logic: shipment identity/requirements, its current appointment (if any),
    latest known ETA, gate/queue check-in state, any active events on its
    assigned dock, and pending warehouse-confirmation status."""
    row = con.execute(
        """
        SELECT
            s.current_status, s.required_dock_type, s.temperature_control_required,
            s.load_weight_kg, s.expected_unload_min, s.priority_code,
            a.appointment_id, a.appointment_status, d.dock_code, d.dock_id,
            a.span_start_ts, a.span_end_ts,
            COALESCE(e.effective_eta_ts, s.original_eta_ts) AS effective_eta_ts,
            COALESCE(e.eta_confidence, 'MEDIUM') AS eta_confidence,
            COALESCE(e.eta_source, 'ORIGINAL_PLAN') AS eta_source
        FROM shipments s
        LEFT JOIN appointments a ON a.shipment_id = s.shipment_id AND a.is_current
        LEFT JOIN docks d ON d.dock_id = a.dock_id
        LEFT JOIN v_latest_eta e ON e.shipment_id = s.shipment_id
        WHERE s.shipment_id = %s
        """,
        (shipment_id,),
    ).fetchone()
    (
        current_status,
        required_dock_type,
        temperature_control_required,
        load_weight_kg,
        expected_unload_min,
        priority_code,
        appointment_id,
        appointment_status,
        dock_code,
        dock_id,
        span_start,
        span_end,
        eta,
        eta_confidence,
        eta_source,
    ) = row

    current_appointment = None
    if appointment_id is not None:
        current_appointment = CurrentAppointment(
            appointment_id=appointment_id,
            appointment_status=appointment_status,
            dock_code=dock_code,
            span_start_ts=span_start.astimezone(IST).isoformat() if span_start else None,
            span_end_ts=span_end.astimezone(IST).isoformat() if span_end else None,
        )

    latest_eta = (
        LatestEta(
            effective_eta_ts=eta.astimezone(IST).isoformat(),
            eta_confidence=eta_confidence,
            eta_source=eta_source,
        )
        if eta is not None
        else None
    )

    checkin_row = con.execute(
        "SELECT gate_in_ts, queue_state, actual_dock_id FROM facility_checkins WHERE shipment_id = %s",
        (shipment_id,),
    ).fetchone()
    checkin = None
    if checkin_row is not None:
        gate_in_ts, queue_state, actual_dock_id = checkin_row
        checkin = Checkin(
            gate_in_ts=gate_in_ts.astimezone(IST).isoformat() if gate_in_ts else None,
            queue_state=queue_state,
            actual_dock_id=actual_dock_id,
        )

    active_dock_events: list[ActiveDockEvent] = []
    if dock_id is not None:
        now = clock.now()
        event_rows = con.execute(
            """
            SELECT event_type, reason, event_start_ts, event_end_ts
            FROM dock_status_events
            WHERE dock_id = %s AND event_start_ts <= %s
              AND (event_end_ts IS NULL OR event_end_ts > %s)
            ORDER BY event_start_ts
            """,
            (dock_id, now, now),
        ).fetchall()
        active_dock_events = [
            ActiveDockEvent(
                event_type=r[0],
                reason=r[1],
                event_start_ts=r[2].astimezone(IST).isoformat(),
                event_end_ts=r[3].astimezone(IST).isoformat() if r[3] else None,
            )
            for r in event_rows
        ]

    pending_notification = None
    if appointment_status == "PENDING_CONFIRMATION" and appointment_id is not None:
        notif_row = con.execute(
            """
            SELECT delivery_status, sent_at FROM operational_messages
            WHERE appointment_id = %s ORDER BY sent_at DESC LIMIT 1
            """,
            (appointment_id,),
        ).fetchone()
        if notif_row is not None:
            pending_notification = PendingNotification(
                delivery_status=notif_row[0],
                sent_at=notif_row[1].astimezone(IST).isoformat(),
            )

    esc_row = con.execute(
        """
        SELECT escalation_id, reason_code, status, created_at, assigned_contact_id
        FROM escalations
        WHERE shipment_id = %s AND status <> 'RESOLVED'
        ORDER BY created_at DESC LIMIT 1
        """,
        (shipment_id,),
    ).fetchone()
    active_escalation = (
        ActiveEscalation(
            escalation_id=esc_row[0],
            reason_code=esc_row[1],
            status=esc_row[2],
            created_at=esc_row[3].astimezone(IST).isoformat(),
            assigned_contact_id=esc_row[4],
        )
        if esc_row is not None
        else None
    )

    return ShipmentState(
        status="shipment_state",
        shipment=ShipmentInfo(
            shipment_id=shipment_id,
            current_status=current_status,
            required_dock_type=required_dock_type,
            temperature_control_required=temperature_control_required,
            load_weight_kg=load_weight_kg,
            expected_unload_min=expected_unload_min,
            priority_code=priority_code,
        ),
        current_appointment=current_appointment,
        latest_eta=latest_eta,
        checkin=checkin,
        active_dock_events=active_dock_events,
        pending_notification=pending_notification,
        active_escalation=active_escalation,
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
