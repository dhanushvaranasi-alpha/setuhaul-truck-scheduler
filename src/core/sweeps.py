import uuid
from datetime import timedelta

from clock import Clock

from ..config import get_pending_confirmation_policy
from .escalation import escalate_to_human
from .feasibility import (
    DockCandidate,
    SpanCandidate,
    evaluate_span,
    get_shipment_context,
)

# --------------------------------------------------------------------------
# Four cron sweeps (§14 / RUNBOOK Step 14). Each opens its own connection —
# no shared state between invocations (I13). Every timestamp write goes
# through the injected Clock (I4).
# --------------------------------------------------------------------------


def _notify_driver(con, shipment_id: str, text: str, clock: Clock) -> None:
    thread_id = _thread_for_shipment(con, shipment_id, clock)
    con.execute(
        """
        INSERT INTO chat_messages (chat_message_id, thread_id, sender_type, sender_reference, message_text, message_ts)
        VALUES (%s, %s, 'AGENT', 'agent', %s, %s)
        """,
        (f"MSG-{uuid.uuid4().hex[:12]}", thread_id, text, clock.now()),
    )


def _thread_for_shipment(con, shipment_id: str, clock: Clock) -> str:
    """Find the shipment driver's most recent chat thread, or open one on
    the fly. Looked up via the shipment's driver_id, not chat_threads.
    shipment_id — the live agent flow (agent.py's _ensure_chat_thread) never
    sets that column, so a lookup keyed on it silently finds nothing for any
    thread that started from a real driver conversation. A sweep-driven
    event (expiry, escalation) has no chat message of its own to piggyback
    on, so it must be able to open a thread rather than skip the driver
    notification/escalation when the driver hasn't texted yet today."""
    row = con.execute(
        """
        SELECT ct.thread_id
        FROM chat_threads ct
        JOIN shipments s ON s.driver_id = ct.driver_id
        WHERE s.shipment_id = %s
        ORDER BY ct.opened_at DESC LIMIT 1
        """,
        (shipment_id,),
    ).fetchone()
    if row:
        return row[0]

    driver_id = con.execute(
        "SELECT driver_id FROM shipments WHERE shipment_id = %s", (shipment_id,)
    ).fetchone()[0]
    thread_id = f"SWEEP-{shipment_id}"
    con.execute(
        """
        INSERT INTO chat_threads (thread_id, driver_id, shipment_id, opened_at, thread_status, thread_intent)
        VALUES (%s, %s, %s, %s, 'OPEN', 'UNKNOWN')
        ON CONFLICT (thread_id) DO NOTHING
        """,
        (thread_id, driver_id, shipment_id, clock.now()),
    )
    return thread_id


def sweep_holds(clock: Clock) -> dict:
    """Global expiry sweep (all docks), distinct from the in-transaction,
    single-dock sweep inside booking (I8). Announces expiry proactively (§5)."""
    from .. import db

    with db.get_conn() as con:
        rows = con.execute(
            """
            UPDATE slot_holds SET hold_status = 'EXPIRED'
            WHERE hold_status = 'ACTIVE' AND expires_at < %s
            RETURNING shipment_id
            """,
            (clock.now(),),
        ).fetchall()
        expired_shipments = sorted({r[0] for r in rows})
        for shipment_id in expired_shipments:
            _notify_driver(
                con,
                shipment_id,
                "Your held slot expired before booking completed. Let me know if you'd like new options.",
                clock,
            )
        con.commit()
    return {"expired_shipment_count": len(expired_shipments)}


def sweep_pending_confirmations(clock: Clock) -> dict:
    """§7's expiry policy for PENDING_CONFIRMATION appointments. A QUEUED or
    FAILED notification never reached the warehouse, so nobody is going to
    reply to it — that's treated as expired immediately, no retry, no wait.
    A SENT/DELIVERED one gets a real window (delivered_expiry_minutes,
    capped at pre_span_buffer_minutes before span_start) before being
    treated as unconfirmed. Does NOT handle REPLIED — that's
    deliver_warehouse_replies's job, since only the warehouse stub knows a
    reply landed."""
    from .. import db

    policy = get_pending_confirmation_policy()
    expired = 0

    with db.get_conn() as con:
        rows = con.execute(
            """
            SELECT appointment_id, shipment_id, booked_at, span_start_ts
            FROM appointments
            WHERE appointment_status = 'PENDING_CONFIRMATION' AND is_current
            """
        ).fetchall()

        for appointment_id, shipment_id, booked_at, span_start in rows:
            msg_row = con.execute(
                """
                SELECT delivery_status, sent_at FROM operational_messages
                WHERE appointment_id = %s ORDER BY sent_at DESC LIMIT 1
                """,
                (appointment_id,),
            ).fetchone()
            if msg_row is None:
                continue
            delivery_status, _sent_at = msg_row

            if delivery_status in ("QUEUED", "FAILED"):
                _expire_pending(con, appointment_id, shipment_id, clock)
                expired += 1
                continue

            cap = span_start - timedelta(minutes=policy.pre_span_buffer_minutes)
            expiry = min(booked_at + timedelta(minutes=policy.delivered_expiry_minutes), cap)
            if clock.now() >= expiry:
                _expire_pending(con, appointment_id, shipment_id, clock)
                expired += 1

        con.commit()
    return {"expired": expired}


def _expire_pending(con, appointment_id: str, shipment_id: str, clock: Clock) -> None:
    con.execute(
        """
        UPDATE appointments
        SET appointment_status = 'CANCELLED', cancelled_at = %s,
            cancellation_reason = 'PENDING_EXPIRED', is_current = FALSE, updated_at = %s
        WHERE appointment_id = %s
        """,
        (clock.now(), clock.now(), appointment_id),
    )
    thread_id = _thread_for_shipment(con, shipment_id, clock)
    escalate_to_human(con, thread_id, "PENDING_EXPIRED", {"appointment_id": appointment_id}, clock)
    _notify_driver(
        con,
        shipment_id,
        "The warehouse didn't confirm in time, so that slot was released. I'm finding you a new option.",
        clock,
    )


def sweep_escalations(clock: Clock) -> dict:
    """Advance the contact ladder on ack-deadline breach. Never touches
    slot_holds/appointments (§10)."""
    from .. import db

    advanced = 0
    with db.get_conn() as con:
        rows = con.execute(
            """
            SELECT escalation_id, shipment_id, contact_ladder_position
            FROM escalations WHERE status = 'OPEN' AND acknowledge_due_at < %s
            """,
            (clock.now(),),
        ).fetchall()

        for escalation_id, shipment_id, position in rows:
            facility_id = None
            if shipment_id:
                facility_row = con.execute(
                    "SELECT destination_facility_id FROM shipments WHERE shipment_id = %s", (shipment_id,)
                ).fetchone()
                facility_id = facility_row[0] if facility_row else None

            next_contact_id = None
            if facility_id:
                contact_row = con.execute(
                    """
                    SELECT contact_id FROM facility_contacts
                    WHERE facility_id = %s AND active_flag = TRUE
                    ORDER BY contact_id OFFSET %s LIMIT 1
                    """,
                    (facility_id, position + 1),
                ).fetchone()
                next_contact_id = contact_row[0] if contact_row else None

            new_due = clock.now() + timedelta(minutes=10)
            con.execute(
                """
                UPDATE escalations
                SET contact_ladder_position = contact_ladder_position + 1,
                    assigned_contact_id = COALESCE(%s, assigned_contact_id),
                    acknowledge_due_at = %s
                WHERE escalation_id = %s
                """,
                (next_contact_id, new_due, escalation_id),
            )
            advanced += 1

        con.commit()
    return {"advanced": advanced}


def deliver_warehouse_replies(clock: Clock) -> dict:
    """Runs warehouse_stub.deliver_due_replies(), then acts on each reply:
    affirmative -> re-validate feasibility (I6, the slot may have been lost
    to a race while the reply was in flight) -> CONFIRMED, or escalate if no
    longer feasible. Negative -> release, driver can ask for new options."""
    import warehouse_stub as ws

    from .. import db
    from . import notifications

    confirmed, released, escalated = 0, 0, 0
    with db.get_conn() as con:
        store = notifications.PostgresWarehouseStore(con, clock)
        warehouse = ws.StandInWarehouse(store, clock)
        landed = warehouse.deliver_due_replies()

        for appointment_id, affirmative in landed:
            appt_row = con.execute(
                "SELECT shipment_id, dock_id, span_start_ts, span_end_ts FROM appointments WHERE appointment_id = %s",
                (appointment_id,),
            ).fetchone()
            if appt_row is None:
                continue
            shipment_id, dock_id, span_start, span_end = appt_row

            if not affirmative:
                con.execute(
                    """
                    UPDATE appointments SET appointment_status = 'CANCELLED', cancelled_at = %s,
                        cancellation_reason = 'DECLINED_BY_WAREHOUSE', is_current = FALSE, updated_at = %s
                    WHERE appointment_id = %s
                    """,
                    (clock.now(), clock.now(), appointment_id),
                )
                _notify_driver(
                    con, shipment_id, "The warehouse couldn't accept that slot. Let's find another.", clock
                )
                released += 1
                continue

            slot_ids = [
                r[0]
                for r in con.execute(
                    "SELECT slot_id FROM appointment_slot_allocations WHERE appointment_id = %s ORDER BY slot_sequence",
                    (appointment_id,),
                ).fetchall()
            ]
            dock_row = con.execute(
                "SELECT dock_code, dock_type, supports_refrigerated, max_vehicle_weight_kg FROM docks WHERE dock_id = %s",
                (dock_id,),
            ).fetchone()
            ctx = get_shipment_context(con, shipment_id)
            span = SpanCandidate(
                dock=DockCandidate(
                    dock_id=dock_id,
                    dock_code=dock_row[0],
                    dock_type=dock_row[1],
                    supports_refrigerated=dock_row[2],
                    max_vehicle_weight_kg=dock_row[3],
                ),
                slot_ids=slot_ids,
                span_start=span_start,
                span_end=span_end,
            )
            if evaluate_span(con, ctx, span, clock, exclude_appointment_id=appointment_id):
                thread_id = _thread_for_shipment(con, shipment_id, clock)
                escalate_to_human(
                    con, thread_id, "CONTRADICTORY_STATE", {"appointment_id": appointment_id}, clock
                )
                escalated += 1
                continue

            con.execute(
                "UPDATE appointments SET appointment_status = 'CONFIRMED', confirmed_at = %s, updated_at = %s WHERE appointment_id = %s",
                (clock.now(), clock.now(), appointment_id),
            )
            _notify_driver(con, shipment_id, f"Confirmed: {appointment_id}.", clock)
            confirmed += 1

        con.commit()
    return {"confirmed": confirmed, "released": released, "escalated": escalated}
