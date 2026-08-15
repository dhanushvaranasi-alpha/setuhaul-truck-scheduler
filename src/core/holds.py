import uuid
from datetime import datetime, timedelta

import psycopg.errors

from clock import IST, Clock

from ..config import get_hold_policy
from ..models import HoldConfirmed, HoldPolicy, Rejected, ToolResult
from .allocation import get_competing_shipments

RETRYABLE_INSERT_ERRORS = (psycopg.errors.ExclusionViolation, psycopg.errors.DeadlockDetected)


def sweep_expired_holds(con, dock_id: str, clock: Clock) -> int:
    """Must run inside the same transaction as the insert (I8). Neither
    Postgres nor SQLite allows an index predicate to reference now(), so
    hold_status='ACTIVE' alone can't also mean "not expired" — an expired,
    unswept hold keeps blocking."""
    return con.execute(
        """
        UPDATE slot_holds SET hold_status = 'EXPIRED'
        WHERE dock_id = %s AND hold_status = 'ACTIVE' AND expires_at < %s
        """,
        (dock_id, clock.now()),
    ).rowcount


def resolve_offered_option(con, shipment_id: str, option_number: int) -> str | None:
    """Looks up the option_token for the Nth (1-indexed) option from this
    shipment's most recent find_feasible_slots call, logged to
    agent_actions. option_number is the only thing the LLM needs to supply
    — the raw signed token was never part of its conversation history to
    begin with (RunnableWithMessageHistory only persists the final
    natural-language reply between turns, never the intermediate tool JSON),
    so requiring the LLM to reproduce it verbatim is exactly the kind of
    argument a tool should never need (see find_active_hold_group above)."""
    row = con.execute(
        """
        SELECT result_json FROM agent_actions
        WHERE tool_name = 'find_feasible_slots' AND status = 'SUCCESS'
          AND arguments_json->>'shipment_id' = %s
        ORDER BY created_at DESC LIMIT 1
        """,
        (shipment_id,),
    ).fetchone()
    if row is None:
        return None
    result = row[0]
    if result.get("status") != "options":
        return None
    spans = result.get("spans", [])
    if not (1 <= option_number <= len(spans)):
        return None
    return spans[option_number - 1]["option_token"]


def find_active_hold_group(con, shipment_id: str) -> str | None:
    """create_hold enforces one active hold group per shipment (a new hold
    supersedes any prior one), so this is unambiguous. Looked up from
    shipment_id — never taken as a caller-supplied argument — because the
    LLM re-quoting a hold_group_id it saw several turns ago is exactly the
    kind of argument I11 says a tool should never require."""
    row = con.execute(
        "SELECT DISTINCT hold_group_id FROM slot_holds WHERE shipment_id = %s AND hold_status = 'ACTIVE'",
        (shipment_id,),
    ).fetchone()
    return row[0] if row else None


def compute_contention_ratio(
    con, facility_id: str, band_start: datetime, band_end: datetime
) -> float:
    """R = competing trucks / available slot-units over the 4-hour band."""
    competing = len(get_competing_shipments(con, facility_id))
    available = con.execute(
        """
        SELECT count(*) FROM appointment_slots sl
        JOIN docks d ON d.dock_id = sl.dock_id
        WHERE d.facility_id = %s AND sl.slot_status = 'OPEN'
          AND sl.slot_start_ts >= %s AND sl.slot_start_ts < %s
        """,
        (facility_id, band_start, band_end),
    ).fetchone()[0]
    if available == 0:
        return float("inf") if competing > 0 else 0.0
    return competing / available


def resolve_ttl(ratio: float, policy: HoldPolicy) -> int:
    if policy.mode == "fixed":
        ttl = policy.fixed_ttl_seconds
    else:
        ttl = policy.bands[-1].ttl_seconds  # catch-all fallback
        for band in policy.bands:
            if band.max_ratio is None or ratio <= band.max_ratio:
                ttl = band.ttl_seconds
                break
    return max(policy.floor_seconds, min(policy.ceiling_seconds, ttl))


def release_hold(con, hold_group_id: str, reason: str) -> None:
    con.execute(
        """
        UPDATE slot_holds SET hold_status = 'RELEASED', released_reason = %s
        WHERE hold_group_id = %s AND hold_status = 'ACTIVE'
        """,
        (reason, hold_group_id),
    )


def create_hold(
    con,
    shipment_id: str,
    facility_id: str,
    dock_id: str,
    slot_ids: list[str],
    span_start: datetime,
    span_end: datetime,
    band_start: datetime,
    band_end: datetime,
    clock: Clock,
) -> ToolResult:
    """One active hold group per shipment: a new hold supersedes any prior
    active one for this shipment (§9 — "actually make it 11:45" loops back
    and re-validates any live hold, it doesn't stack a second one).

    slot_holds carries its own no_hold_overlap EXCLUDE constraint (I2's
    guard, mirrored at the hold layer) — two shipments can never hold the
    same dock/span at once. A concurrent hold attempt on an already-held
    slot is a normal, expected outcome, not a bug: it's how competing
    drivers actually get sequenced, since the database — not a
    "check-then-insert" in this function — is what decides who holds it."""
    policy = get_hold_policy()

    sweep_expired_holds(con, dock_id, clock)  # I8: before the insert

    prior = con.execute(
        "SELECT DISTINCT hold_group_id FROM slot_holds WHERE shipment_id = %s AND hold_status = 'ACTIVE'",
        (shipment_id,),
    ).fetchall()
    for (prior_group_id,) in prior:
        release_hold(con, prior_group_id, "superseded by a new hold")

    ratio = compute_contention_ratio(con, facility_id, band_start, band_end)
    ttl_seconds = resolve_ttl(ratio, policy)
    now = clock.now()
    expires_at = now + timedelta(seconds=ttl_seconds)  # absolute, never recomputed
    hold_group_id = f"HOLD-{uuid.uuid4().hex[:12]}"

    slot_rows = con.execute(
        "SELECT slot_id, slot_start_ts, slot_end_ts FROM appointment_slots WHERE slot_id = ANY(%s)",
        (slot_ids,),
    ).fetchall()
    slot_times = {row[0]: (row[1], row[2]) for row in slot_rows}

    try:
        with con.transaction():  # savepoint: roll back only this insert batch on conflict
            for slot_id in slot_ids:
                slot_start, slot_end = slot_times[slot_id]
                con.execute(
                    """
                    INSERT INTO slot_holds
                        (hold_id, hold_group_id, slot_id, shipment_id, dock_id,
                         span_start_ts, span_end_ts, hold_status, created_at,
                         expires_at, ttl_seconds, contention_ratio, policy_version)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'ACTIVE', %s, %s, %s, %s, %s)
                    """,
                    (
                        f"SH-{uuid.uuid4().hex[:12]}",
                        hold_group_id,
                        slot_id,
                        shipment_id,
                        dock_id,
                        slot_start,
                        slot_end,
                        now,
                        expires_at,
                        ttl_seconds,
                        ratio,
                        policy.version,
                    ),
                )
    except RETRYABLE_INSERT_ERRORS:
        return Rejected(
            status="rejected",
            reason="SLOT_HELD",
            detail="Another driver is currently holding this slot.",
        )

    return HoldConfirmed(
        status="held",
        hold_group_id=hold_group_id,
        expires_at=expires_at.astimezone(IST).isoformat(),
        ttl_seconds=ttl_seconds,
        contention_ratio=ratio,
    )
