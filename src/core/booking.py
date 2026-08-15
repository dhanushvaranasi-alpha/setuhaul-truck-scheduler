import uuid

import psycopg.errors
from psycopg.types.json import Json

from clock import IST, Clock

from ..config import get_hold_policy
from ..models import Booked, LostRace, Rejected, ToolResult
from .driver_context import CANCELLABLE_STATUSES
from .feasibility import (
    DockCandidate,
    SpanCandidate,
    evaluate_span,
    find_feasible_slots_impl,
    get_shipment_context,
)
from .holds import find_active_hold_group, sweep_expired_holds
from .notifications import notify_warehouse

RESULT_CLASSES = {"booked": Booked, "rejected": Rejected, "lost_race": LostRace}
RETRYABLE_INSERT_ERRORS = (psycopg.errors.ExclusionViolation, psycopg.errors.DeadlockDetected)


def _deserialize_result(data: dict) -> ToolResult:
    return RESULT_CLASSES[data["status"]].model_validate(data)


def lookup_action(con, idempotency_key: str) -> ToolResult | None:
    row = con.execute(
        "SELECT result_json FROM agent_actions WHERE idempotency_key = %s",
        (idempotency_key,),
    ).fetchone()
    if row is None:
        return None
    return _deserialize_result(row[0])


def log_action(con, idempotency_key: str, tool_name: str, arguments: dict, result: ToolResult, status: str) -> None:
    con.execute(
        """
        INSERT INTO agent_actions
            (action_id, idempotency_key, tool_name, arguments_json, result_json, status)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (idempotency_key) DO NOTHING
        """,
        (
            f"ACT-{uuid.uuid4().hex[:12]}",
            idempotency_key,
            tool_name,
            Json(arguments),
            Json(result.model_dump()),
            status,
        ),
    )


def log_decision(con, shipment_id: str, outcome: str, slot_id: str | None = None) -> None:
    con.execute(
        """
        INSERT INTO allocation_decisions (decision_id, shipment_id, slot_id, outcome, policy_version)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (f"DEC-{uuid.uuid4().hex[:12]}", shipment_id, slot_id, outcome, get_hold_policy().version),
    )


def _load_active_holds(con, hold_group_id: str) -> list[dict]:
    rows = con.execute(
        """
        SELECT slot_id, shipment_id, dock_id, span_start_ts, span_end_ts
        FROM slot_holds
        WHERE hold_group_id = %s AND hold_status = 'ACTIVE'
        ORDER BY span_start_ts
        """,
        (hold_group_id,),
    ).fetchall()
    columns = ["slot_id", "shipment_id", "dock_id", "span_start_ts", "span_end_ts"]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _consume_holds(con, hold_group_id: str) -> None:
    con.execute(
        "UPDATE slot_holds SET hold_status = 'CONSUMED' WHERE hold_group_id = %s AND hold_status = 'ACTIVE'",
        (hold_group_id,),
    )


def request_booking(
    shipment_id: str, idempotency_key: str, clock: Clock, reschedule: bool = False
) -> ToolResult:
    from .. import db

    tool_name = "reschedule_appointment" if reschedule else "request_booking"

    with db.get_conn() as con:
        with con.transaction():
            # 1. Idempotency check — duplicate retry, no-op.
            if (prior := lookup_action(con, idempotency_key)) is not None:
                return prior

            hold_group_id = find_active_hold_group(con, shipment_id)
            if hold_group_id is None:
                result = Rejected(
                    status="rejected",
                    reason="NO_ACTIVE_HOLD",
                    detail="No slot is currently held for this shipment.",
                )
                log_action(con, idempotency_key, tool_name, {"shipment_id": shipment_id}, result, "REJECTED")
                return result

            # A hold group's dock_id is fixed at creation and survives expiry,
            # so this lookup works whether or not sweep is about to expire it.
            dock_id = con.execute(
                "SELECT DISTINCT dock_id FROM slot_holds WHERE hold_group_id = %s",
                (hold_group_id,),
            ).fetchone()[0]

            # 2. Sweep expired holds — MUST be inside this transaction (I8).
            sweep_expired_holds(con, dock_id, clock)

            # 3. Load holds.
            holds = _load_active_holds(con, hold_group_id)
            if not holds:
                result = Rejected(status="rejected", reason="HOLD_EXPIRED", detail="Hold timed out.")
                log_action(con, idempotency_key, tool_name, {"shipment_id": shipment_id}, result, "REJECTED")
                return result

            slot_ids = [h["slot_id"] for h in holds]
            span_start = min(h["span_start_ts"] for h in holds)
            span_end = max(h["span_end_ts"] for h in holds)

            # 3b. Reschedule: the shipment's current active appointment is the
            # one being replaced — looked up here (not passed by the caller)
            # so the LLM never has to track a second ID. Must exist and be
            # cancellable, or this isn't a reschedule at all.
            replaced_appointment_id = None
            if reschedule:
                old_row = con.execute(
                    """
                    SELECT appointment_id FROM appointments
                    WHERE shipment_id = %s AND is_current AND appointment_status = ANY(%s)
                    """,
                    (shipment_id, list(CANCELLABLE_STATUSES)),
                ).fetchone()
                if old_row is None:
                    result = Rejected(
                        status="rejected",
                        reason="NOT_RESCHEDULABLE",
                        detail="No existing appointment to reschedule for this shipment.",
                    )
                    log_action(con, idempotency_key, tool_name, {"shipment_id": shipment_id}, result, "REJECTED")
                    return result
                replaced_appointment_id = old_row[0]

            # 4. Re-validate feasibility across the full span — the slot may
            # have been lost to a race while the hold sat idle.
            ctx = get_shipment_context(con, shipment_id)
            dock_row = con.execute(
                "SELECT dock_code, dock_type, supports_refrigerated, max_vehicle_weight_kg FROM docks WHERE dock_id = %s",
                (dock_id,),
            ).fetchone()
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
            failures = evaluate_span(con, ctx, span, clock)
            if failures:
                result = Rejected(
                    status="rejected", reason=failures[0], detail="No longer feasible at booking time."
                )
                log_action(con, idempotency_key, tool_name, {"shipment_id": shipment_id}, result, "REJECTED")
                log_decision(con, shipment_id, "REJECTED_INFEASIBLE", slot_ids[0])
                return result

            # 5. Cancel the replaced appointment (if any) and insert the new
            # one in the SAME savepoint — the database catches any race (I2),
            # not this code, and a lost race here rolls back the cancellation
            # too, so the driver never ends up with neither appointment.
            appointment_id = f"APT-{uuid.uuid4().hex[:12]}"
            try:
                with con.transaction():  # savepoint: roll back only this batch on conflict
                    if replaced_appointment_id is not None:
                        con.execute(
                            """
                            UPDATE appointments
                            SET appointment_status = 'CANCELLED',
                                cancelled_at = %s,
                                cancellation_reason = 'DRIVER_RESCHEDULED',
                                is_current = FALSE,
                                updated_at = %s
                            WHERE appointment_id = %s
                            """,
                            (clock.now(), clock.now(), replaced_appointment_id),
                        )
                    con.execute(
                        """
                        INSERT INTO appointments
                            (appointment_id, shipment_id, slot_id, dock_id,
                             span_start_ts, span_end_ts, appointment_status,
                             booking_source, is_current, booked_at, updated_at,
                             replaced_appointment_id)
                        VALUES (%s, %s, %s, %s, %s, %s, 'PENDING_CONFIRMATION',
                                'DRIVER_CHAT', TRUE, %s, %s, %s)
                        """,
                        (
                            appointment_id,
                            shipment_id,
                            slot_ids[0],
                            dock_id,
                            span_start,
                            span_end,
                            clock.now(),
                            clock.now(),
                            replaced_appointment_id,
                        ),
                    )
                    for i, slot_id in enumerate(slot_ids):
                        con.execute(
                            "INSERT INTO appointment_slot_allocations VALUES (%s, %s, %s)",
                            (appointment_id, slot_id, i),
                        )
            except RETRYABLE_INSERT_ERRORS:
                # ExclusionViolation (23P01, I2) or DeadlockDetected (40P01,
                # a documented GiST EXCLUDE behavior under concurrent
                # contention — the victim's transaction is fully rolled
                # back, so it's an equally valid lost-race signal). The
                # savepoint rollback also undoes the cancellation above, if
                # any — the driver keeps their original appointment.
                log_decision(con, shipment_id, "LOST_RACE", slot_ids[0])
                alternatives = find_feasible_slots_impl(shipment_id, None, clock)
                result = LostRace(
                    status="lost_race",
                    message="that slot went just now",
                    alternatives=alternatives.spans if alternatives.status == "options" else [],
                )
                log_action(con, idempotency_key, tool_name, {"shipment_id": shipment_id}, result, "REJECTED")
                return result

            # 6. Consume holds, notify the warehouse, done.
            _consume_holds(con, hold_group_id)
            notified = notify_warehouse(con, appointment_id, shipment_id, dock_id, clock)

            result = Booked(
                status="booked",
                appointment_id=appointment_id,
                dock_code=span.dock.dock_code,
                span_start=span_start.astimezone(IST).isoformat(),
                span_end=span_end.astimezone(IST).isoformat(),
                warehouse_notified=notified,
            )
            log_decision(con, shipment_id, "BOOKED", slot_ids[0])
            log_action(con, idempotency_key, tool_name, {"shipment_id": shipment_id}, result, "SUCCESS")
            return result


def reschedule_appointment(shipment_id: str, clock: Clock) -> ToolResult:
    """Cancel the shipment's current appointment and book its active hold as
    the replacement, atomically. Takes only shipment_id — the hold and the
    appointment being replaced are both looked up from the database, never
    passed in, so neither can be a stale or invented ID.

    Each call gets a fresh idempotency key rather than one supplied by the
    caller: the hold's single-use nature (it flips to CONSUMED on success,
    so find_active_hold_group finds nothing on a retry) is what actually
    protects against a duplicate reschedule, not key matching. A genuine
    concurrent double-call is still resolved by the no_dock_overlap EXCLUDE
    constraint (I2), same as any other booking race."""
    idempotency_key = f"RESCHED-{shipment_id}-{uuid.uuid4().hex[:12]}"
    return request_booking(shipment_id, idempotency_key, clock, reschedule=True)
