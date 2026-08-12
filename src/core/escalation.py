import uuid
from datetime import timedelta

from clock import Clock
from psycopg.types.json import Json

from ..config import get_operating_constants
from ..models import Escalated

REASON_CODES = (
    "NO_FEASIBLE_SLOT",
    "NO_SAME_DAY_SLOT",
    "AFTER_HOURS_APPROVAL",
    "AMBIGUOUS_UNRESOLVED",
    "CONTRADICTORY_STATE",
    "CANCELLED_SHIPMENT_ACTIVITY",
    "NOTIFICATION_FAILED",
    "PENDING_EXPIRED",
    "MOVE_BLOCKED_BY_CONSTRAINT",
    "NO_REACHABLE_CONTACT",
    "SAFETY_OR_COMMERCIAL",
    "REPEATED_RACE_LOSS",
)


def escalate_to_human(con, thread_id: str, reason_code: str, context: dict, clock: Clock) -> Escalated:
    """A designed outcome, not a failure path. Never auto-releases a slot —
    a human may be mid-decision (§10).

    reason_code is a Literal in the LLM-facing tool schema (tools.py), so an
    invalid value can't arrive from the agent path — this assertion is
    defense-in-depth for any other internal caller (e.g. a future cron
    sweep)."""
    if reason_code not in REASON_CODES:
        raise ValueError(f"unknown escalation reason_code: {reason_code!r}")

    constants = get_operating_constants()

    thread_row = con.execute(
        "SELECT shipment_id FROM chat_threads WHERE thread_id = %s", (thread_id,)
    ).fetchone()
    shipment_id = thread_row[0] if thread_row else None

    facility_id = None
    if shipment_id:
        facility_row = con.execute(
            "SELECT destination_facility_id FROM shipments WHERE shipment_id = %s", (shipment_id,)
        ).fetchone()
        facility_id = facility_row[0] if facility_row else None

    contact_id, contact_name = None, "dispatch"
    if facility_id:
        contact_row = con.execute(
            """
            SELECT contact_id, contact_name FROM facility_contacts
            WHERE facility_id = %s AND active_flag = TRUE
            ORDER BY contact_id LIMIT 1
            """,
            (facility_id,),
        ).fetchone()
        if contact_row:
            contact_id, contact_name = contact_row

    now = clock.now()
    acknowledge_due_at = now + timedelta(minutes=constants.escalation_acknowledge_minutes)
    escalation_id = f"ESC-{uuid.uuid4().hex[:12]}"
    con.execute(
        """
        INSERT INTO escalations
            (escalation_id, thread_id, shipment_id, reason_code, context_json,
             assigned_contact_id, contact_ladder_position, acknowledge_due_at, status, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, 0, %s, 'OPEN', %s)
        """,
        (
            escalation_id,
            thread_id,
            shipment_id,
            reason_code,
            Json(context),
            contact_id,
            acknowledge_due_at,
            now,
        ),
    )
    return Escalated(
        status="escalated",
        escalation_id=escalation_id,
        reason_code=reason_code,
        assigned_to=contact_name,
        update_by=acknowledge_due_at.isoformat(),
    )
