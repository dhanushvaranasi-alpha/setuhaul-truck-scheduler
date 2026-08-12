import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from clock import IST  # noqa: E402

from api._http_common import json_post_handler  # noqa: E402
from src import db  # noqa: E402
from src.clock_state import get_clock  # noqa: E402
from src.core.sweeps import (  # noqa: E402
    deliver_warehouse_replies,
    sweep_escalations,
    sweep_holds,
    sweep_pending_confirmations,
)


def _tick(_body: dict) -> dict:
    """Runs all four sweeps in sequence — a manual stand-in for the four
    separate 1-minute crons (api/cron/*.py), useful for driving the demo
    clock forward without waiting on a real schedule. Gated the same way as
    reset_demo() (ALLOW_RESET=true), not CRON_SECRET — this is an operator
    tool, not something Vercel's scheduler calls."""
    if os.environ.get("ALLOW_RESET") != "true":
        raise PermissionError("admin/tick is disabled — set ALLOW_RESET=true")

    with db.get_conn() as con:
        clock = get_clock(con)

    holds_result = sweep_holds(clock)
    confirmations_result = sweep_pending_confirmations(clock)
    escalations_result = sweep_escalations(clock)
    replies_result = deliver_warehouse_replies(clock)

    return {
        "holds_swept": holds_result["expired_shipment_count"],
        "confirmations_swept": sum(confirmations_result.values()),
        "escalations_advanced": escalations_result["advanced"],
        "replies_delivered": sum(replies_result.values()),
        "clock": clock.now().astimezone(IST).isoformat(),
    }


handler = json_post_handler(_tick)
