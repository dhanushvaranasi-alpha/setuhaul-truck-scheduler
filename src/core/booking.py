import uuid

import psycopg.errors
from psycopg.types.json import Json

from clock import IST, Clock

from ..config import get_hold_policy
from ..models import Booked, LostRace, Rejected, ToolResult
from .feasibility import (
    DockCandidate,
    SpanCandidate,
    evaluate_span,
    find_feasible_slots_impl,
    get_shipment_context,
)
from .holds import sweep_expired_holds
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


def request_booking(hold_group_id: str, idempotency_key: str, clock: Clock) -> ToolResult:
    from .. import db

    with db.get_conn() as con:
        with con.transaction():
            # 1. Idempotency check — duplicate retry, no-op.
            if (prior := lookup_action(con, idempotency_key)) is not None:
                return prior

            # A hold group's dock_id is fixed at creation and survives expiry,
            # so this lookup works whether or not sweep is about to expire it.
            dock_row = con.execute(
                "SELECT DISTINCT dock_id FROM slot_holds WHERE hold_group_id = %s",
                (hold_group_id,),
            ).fetchone()
            if dock_row is None:
                result = Rejected(status="rejected", reason="HOLD_EXPIRED", detail="No such hold.")
                log_action(con, idempotency_key, "request_booking", {"hold_group_id": hold_group_id}, result, "REJECTED")
                return result
            dock_id = dock_row[0]

            # 2. Sweep expired holds — MUST be inside this transaction (I8).
            sweep_expired_holds(con, dock_id, clock)

            # 3. Load holds.
            holds = _load_active_holds(con, hold_group_id)
            if not holds:
                result = Rejected(status="rejected", reason="HOLD_EXPIRED", detail="Hold timed out.")
                log_action(con, idempotency_key, "request_booking", {"hold_group_id": hold_group_id}, result, "REJECTED")
                return result

            shipment_id = holds[0]["shipment_id"]
            slot_ids = [h["slot_id"] for h in holds]
            span_start = min(h["span_start_ts"] for h in holds)
            span_end = max(h["span_end_ts"] for h in holds)

            # 4. Re-validate feasibility across the full span — the slot may
            # have been lost to a race while the hold sat idle.
            ctx = get_shipment_context(con, shipment_id)
            dock_code_row = con.execute(
                "SELECT dock_code FROM docks WHERE dock_id = %s", (dock_id,)
            ).fetchone()
            span = SpanCandidate(
                dock=DockCandidate(dock_id=dock_id, dock_code=dock_code_row[0]),
                slot_ids=slot_ids,
                span_start=span_start,
                span_end=span_end,
            )
            failures = evaluate_span(con, ctx, span, clock)
            if failures:
                result = Rejected(
                    status="rejected", reason=failures[0], detail="No longer feasible at booking time."
                )
                log_action(con, idempotency_key, "request_booking", {"hold_group_id": hold_group_id}, result, "REJECTED")
                log_decision(con, shipment_id, "REJECTED_INFEASIBLE", slot_ids[0])
                return result

            # 5. Insert — the database catches any race (I2), not this code.
            appointment_id = f"APT-{uuid.uuid4().hex[:12]}"
            try:
                with con.transaction():  # savepoint: roll back only the insert on conflict
                    con.execute(
                        """
                        INSERT INTO appointments
                            (appointment_id, shipment_id, slot_id, dock_id,
                             span_start_ts, span_end_ts, appointment_status,
                             booking_source, is_current, booked_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, 'PENDING_CONFIRMATION',
                                'DRIVER_CHAT', TRUE, %s, %s)
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
                # back, so it's an equally valid lost-race signal).
                log_decision(con, shipment_id, "LOST_RACE", slot_ids[0])
                alternatives = find_feasible_slots_impl(shipment_id, None, clock)
                result = LostRace(
                    status="lost_race",
                    message="that slot went just now",
                    alternatives=alternatives.spans if alternatives.status == "options" else [],
                )
                log_action(con, idempotency_key, "request_booking", {"hold_group_id": hold_group_id}, result, "REJECTED")
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
            log_action(con, idempotency_key, "request_booking", {"hold_group_id": hold_group_id}, result, "SUCCESS")
            return result
