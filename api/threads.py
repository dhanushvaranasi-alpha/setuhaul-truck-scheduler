import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api._http_common import json_get_handler  # noqa: E402
from src import db  # noqa: E402
from src.clock_state import get_clock  # noqa: E402


def _thread_state(query: dict) -> dict:
    driver_id = query.get("driver_id")
    if not driver_id:
        raise LookupError("driver_id is required")

    with db.get_conn() as con:
        clock = get_clock(con)

        shipments = con.execute(
            """
            SELECT shipment_id, order_reference, current_status, priority_code
            FROM shipments WHERE driver_id = %s ORDER BY created_at
            """,
            (driver_id,),
        ).fetchall()

        thread_row = con.execute(
            "SELECT thread_id FROM chat_threads WHERE driver_id = %s ORDER BY opened_at DESC LIMIT 1",
            (driver_id,),
        ).fetchone()
        messages = []
        thread_id = None
        if thread_row:
            thread_id = thread_row[0]
            messages = con.execute(
                """
                SELECT sender_type, message_text, message_ts FROM chat_messages
                WHERE thread_id = %s ORDER BY message_ts
                """,
                (thread_id,),
            ).fetchall()

        appointments = con.execute(
            """
            SELECT a.appointment_id, a.shipment_id, a.appointment_status, d.dock_code,
                   a.span_start_ts, a.span_end_ts
            FROM appointments a
            JOIN shipments s ON s.shipment_id = a.shipment_id
            LEFT JOIN docks d ON d.dock_id = a.dock_id
            WHERE s.driver_id = %s AND a.is_current
            ORDER BY a.span_start_ts
            """,
            (driver_id,),
        ).fetchall()

        hold = con.execute(
            """
            SELECT hg.hold_group_id, hg.shipment_id, MAX(hg.expires_at), MAX(hg.ttl_seconds),
                   MAX(hg.contention_ratio), MAX(hg.dock_id)
            FROM slot_holds hg
            JOIN shipments s ON s.shipment_id = hg.shipment_id
            WHERE s.driver_id = %s AND hg.hold_status = 'ACTIVE'
            GROUP BY hg.hold_group_id, hg.shipment_id
            ORDER BY MAX(hg.expires_at) DESC LIMIT 1
            """,
            (driver_id,),
        ).fetchone()

    return {
        "driver_id": driver_id,
        "now": clock.now().isoformat(),
        "shipments": [
            {"shipment_id": r[0], "order_reference": r[1], "current_status": r[2], "priority_code": r[3]}
            for r in shipments
        ],
        "thread_id": thread_id,
        "messages": [
            {"sender_type": r[0], "message_text": r[1], "message_ts": r[2].isoformat()} for r in messages
        ],
        "appointments": [
            {
                "appointment_id": r[0],
                "shipment_id": r[1],
                "appointment_status": r[2],
                "dock_code": r[3],
                "span_start": r[4].isoformat() if r[4] else None,
                "span_end": r[5].isoformat() if r[5] else None,
            }
            for r in appointments
        ],
        "active_hold": (
            {
                "hold_group_id": hold[0],
                "shipment_id": hold[1],
                "expires_at": hold[2].isoformat(),
                "ttl_seconds": hold[3],
                "contention_ratio": hold[4],
                "dock_id": hold[5],
            }
            if hold
            else None
        ),
    }


handler = json_get_handler(_thread_state)
