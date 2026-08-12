import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api._http_common import json_post_handler  # noqa: E402
from src import db  # noqa: E402
from src.agent import handle_message  # noqa: E402
from src.clock_state import get_clock  # noqa: E402


def _chat(body: dict) -> dict:
    driver_id = body.get("driver_id")
    message = body.get("message")
    if not driver_id or not message:
        raise LookupError("driver_id and message are required")

    with db.get_conn() as con:
        date_str = get_clock(con).now().date().isoformat()

    reply = handle_message(driver_id, date_str, message)
    return {"reply": reply}


handler = json_post_handler(_chat)
