import json
import uuid
from datetime import timedelta
from typing import Literal

from langchain_core.runnables.config import ensure_config
from langchain_core.tools import tool

from . import db
from .clock_state import get_clock
from .config import get_operating_constants
from .core import driver_context as dc
from .core import escalation as esc
from .core.booking import log_action, request_booking
from .core.feasibility import find_feasible_slots_impl
from .core.holds import create_hold
from .core.tokens import verify_option_token
from .models import Rejected

# --------------------------------------------------------------------------
# I11: no tool accepts driver_id. It comes from the session — never exposed
# in the tool schema the LLM sees. Every tool that touches a shipment/thread
# validates ownership here first; violations are logged to agent_actions
# with status REJECTED and never reach the deterministic core.
#
# Session identity is read via ensure_config(), not a `config:
# RunnableConfig` function parameter. AgentExecutor's tool-calling loop
# (_perform_agent_action -> tool.run()) never passes a `config` kwarg, and
# BaseTool.run() unconditionally overwrites any declared config-typed
# parameter with that (missing => None) value — verified against the
# installed langchain-core source. It does, however, correctly set the
# ambient contextvar via set_config_context(), which ensure_config() reads.
# --------------------------------------------------------------------------


def _session() -> tuple[str, str]:
    cfg = ensure_config()["configurable"]
    return cfg["driver_id"], cfg["thread_id"]


def _reject_ownership(con, driver_id: str, tool_name: str, arguments: dict) -> dict:
    result = Rejected(
        status="rejected", reason="NOT_YOUR_SHIPMENT", detail="That shipment isn't on your session."
    )
    log_action(con, f"REJ-{uuid.uuid4().hex[:12]}", tool_name, arguments, result, "REJECTED")
    return result.model_dump()


@tool
def resolve_driver_context() -> dict:
    """Look up the driver's active shipments for this session."""
    driver_id, _ = _session()
    with db.get_conn() as con:
        result = dc.resolve_driver_context(con, driver_id)
        con.commit()
        return result.model_dump()


@tool
def get_shipment_state(shipment_id: str) -> dict:
    """Fetch the current status, ETA, and appointment (if any) for a shipment."""
    driver_id, _ = _session()
    with db.get_conn() as con:
        if not dc.owns_shipment(con, shipment_id, driver_id):
            return _reject_ownership(con, driver_id, "get_shipment_state", {"shipment_id": shipment_id})
        result = dc.get_shipment_state(con, shipment_id)
        con.commit()
        return result.model_dump()


@tool
def record_eta_update(
    shipment_id: str,
    declared_eta: str,
    confidence: Literal["LOW", "MEDIUM", "HIGH"],
    source: Literal["DRIVER_DECLARED", "OPERATIONS_OVERRIDE"],
) -> dict:
    """Record a driver-declared arrival time (ISO-8601 with timezone), never a duration."""
    driver_id, _ = _session()
    with db.get_conn() as con:
        if not dc.owns_shipment(con, shipment_id, driver_id):
            return _reject_ownership(con, driver_id, "record_eta_update", {"shipment_id": shipment_id})
        clock = get_clock(con)
        result = dc.record_eta_update(con, shipment_id, declared_eta, confidence, source, clock)
        con.commit()
        return result.model_dump()


@tool
def record_driver_constraint(
    shipment_id: str,
    type: Literal["LATEST_GATE_OUT", "LATEST_UNLOAD_START", "EARLIEST_ARRIVAL", "OTHER"],
    value: str,
) -> dict:
    """Record a deadline the driver stated (e.g. "must leave by 1:30"), ISO-8601 with timezone."""
    driver_id, _ = _session()
    with db.get_conn() as con:
        if not dc.owns_shipment(con, shipment_id, driver_id):
            return _reject_ownership(con, driver_id, "record_driver_constraint", {"shipment_id": shipment_id})
        clock = get_clock(con)
        result = dc.record_driver_constraint(con, shipment_id, type, value, clock)
        con.commit()
        return result.model_dump()


@tool
def find_feasible_slots(shipment_id: str, earliest_ts: str | None = None) -> dict:
    """Find available dock slots for a shipment after its declared ETA. Never holds capacity."""
    driver_id, _ = _session()
    with db.get_conn() as con:
        if not dc.owns_shipment(con, shipment_id, driver_id):
            return _reject_ownership(con, driver_id, "find_feasible_slots", {"shipment_id": shipment_id})
        clock = get_clock(con)
    result = find_feasible_slots_impl(shipment_id, earliest_ts, clock)
    return result.model_dump()


@tool
def hold_slot(option_token: str) -> dict:
    """Place a soft hold on a specific option returned by find_feasible_slots."""
    driver_id, _ = _session()
    with db.get_conn() as con:
        clock = get_clock(con)
        # shipment_id is embedded in the (unverified) token payload; extract
        # it first so an ownership violation is logged even for a forged or
        # tampered token, before spending a signature check on it.
        shipment_id = json.loads(option_token).get("shipment_id", "")
        if not dc.owns_shipment(con, shipment_id, driver_id):
            return _reject_ownership(con, driver_id, "hold_slot", {"shipment_id": shipment_id})

        token = verify_option_token(con, option_token, shipment_id)
        if token is None:
            result = Rejected(
                status="rejected", reason="LOST_RACE", detail="That option is no longer valid; ask again."
            )
            con.commit()
            return result.model_dump()

        constants = get_operating_constants()
        band_start = clock.now()
        band_end = clock.now() + timedelta(hours=constants.search_band_hours)
        dock_row = con.execute(
            "SELECT sl.dock_id, d.facility_id FROM appointment_slots sl JOIN docks d ON d.dock_id = sl.dock_id WHERE sl.slot_id = %s",
            (token.span_slot_ids[0],),
        ).fetchone()
        dock_id, facility_id = dock_row
        span_row = con.execute(
            "SELECT MIN(slot_start_ts), MAX(slot_end_ts) FROM appointment_slots WHERE slot_id = ANY(%s)",
            (token.span_slot_ids,),
        ).fetchone()
        span_start, span_end = span_row

        result = create_hold(
            con,
            shipment_id=shipment_id,
            facility_id=facility_id,
            dock_id=dock_id,
            slot_ids=token.span_slot_ids,
            span_start=span_start,
            span_end=span_end,
            band_start=band_start,
            band_end=band_end,
            clock=clock,
        )
        con.commit()
        return result.model_dump()


@tool
def release_hold(hold_group_id: str) -> dict:
    """Release an active hold, e.g. because the driver changed their mind."""
    driver_id, _ = _session()
    with db.get_conn() as con:
        owner_row = con.execute(
            "SELECT shipment_id FROM slot_holds WHERE hold_group_id = %s LIMIT 1", (hold_group_id,)
        ).fetchone()
        if owner_row is None or not dc.owns_shipment(con, owner_row[0], driver_id):
            return _reject_ownership(con, driver_id, "release_hold", {"hold_group_id": hold_group_id})
        result = dc.release_hold(con, hold_group_id, "driver requested release")
        con.commit()
        return result.model_dump()


@tool
def request_booking_tool(hold_group_id: str, idempotency_key: str) -> dict:
    """Convert an active hold into a real booking request."""
    driver_id, _ = _session()
    with db.get_conn() as con:
        owner_row = con.execute(
            "SELECT shipment_id FROM slot_holds WHERE hold_group_id = %s LIMIT 1", (hold_group_id,)
        ).fetchone()
        if owner_row is None or not dc.owns_shipment(con, owner_row[0], driver_id):
            return _reject_ownership(con, driver_id, "request_booking", {"hold_group_id": hold_group_id})
        clock = get_clock(con)
    result = request_booking(hold_group_id, idempotency_key, clock)
    return result.model_dump()


request_booking_tool.name = "request_booking"


@tool
def cancel_appointment(appointment_id: str, reason: str) -> dict:
    """Cancel a pending or confirmed appointment."""
    driver_id, _ = _session()
    with db.get_conn() as con:
        owner_row = con.execute(
            "SELECT shipment_id FROM appointments WHERE appointment_id = %s", (appointment_id,)
        ).fetchone()
        if owner_row is None or not dc.owns_shipment(con, owner_row[0], driver_id):
            return _reject_ownership(con, driver_id, "cancel_appointment", {"appointment_id": appointment_id})
        clock = get_clock(con)
        result = dc.cancel_appointment(con, appointment_id, reason, clock)
        con.commit()
        return result.model_dump()


@tool
def get_appointment_status(appointment_id: str) -> dict:
    """Check the current status of an appointment."""
    driver_id, _ = _session()
    with db.get_conn() as con:
        owner_row = con.execute(
            "SELECT shipment_id FROM appointments WHERE appointment_id = %s", (appointment_id,)
        ).fetchone()
        if owner_row is None or not dc.owns_shipment(con, owner_row[0], driver_id):
            return _reject_ownership(con, driver_id, "get_appointment_status", {"appointment_id": appointment_id})
        result = dc.get_appointment_status(con, appointment_id)
        con.commit()
        return result.model_dump()


@tool
def escalate_to_human(
    reason_code: Literal[
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
    ],
    context: str,
) -> dict:
    """Hand off to a human dispatcher with what was tried and why it failed."""
    driver_id, thread_id = _session()
    with db.get_conn() as con:
        thread_row = con.execute(
            "SELECT driver_id FROM chat_threads WHERE thread_id = %s", (thread_id,)
        ).fetchone()
        if thread_row is None or thread_row[0] != driver_id:
            return _reject_ownership(con, driver_id, "escalate_to_human", {"thread_id": thread_id})
        clock = get_clock(con)
        result = esc.escalate_to_human(con, thread_id, reason_code, {"note": context}, clock)
        con.commit()
        return result.model_dump()


TOOLS = [
    resolve_driver_context,
    get_shipment_state,
    record_eta_update,
    record_driver_constraint,
    find_feasible_slots,
    hold_slot,
    release_hold,
    request_booking_tool,
    cancel_appointment,
    get_appointment_status,
    escalate_to_human,
]
