import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api._http_common import json_get_handler  # noqa: E402
from src import db  # noqa: E402
from src.clock_state import get_clock  # noqa: E402

ACTIVE_EXCEPTION_STATUSES_EXCLUDED = ("RESOLVED", "CANCELLED", "DUPLICATE")


def _thread_state(query: dict) -> dict:
    driver_id = query.get("driver_id")
    if not driver_id:
        raise LookupError("driver_id is required")

    with db.get_conn() as con:
        clock = get_clock(con)

        shipments = con.execute(
            """
            SELECT s.shipment_id, s.order_reference, s.current_status, s.priority_code, f.city
            FROM shipments s
            JOIN facilities f ON f.facility_id = s.destination_facility_id
            WHERE s.driver_id = %s ORDER BY s.created_at
            """,
            (driver_id,),
        ).fetchall()

        # Order by the thread's own last message, not opened_at: two threads
        # can share the same opened_at under the demo's simulated clock (it
        # doesn't advance between message sends), so opened_at alone isn't a
        # reliable "most recent" signal. thread_id is a final deterministic
        # tiebreak so the choice never depends on unspecified row order.
        thread_row = con.execute(
            """
            SELECT ct.thread_id
            FROM chat_threads ct
            LEFT JOIN (
                SELECT thread_id, MAX(message_ts) AS last_message_ts
                FROM chat_messages
                GROUP BY thread_id
            ) cm ON cm.thread_id = ct.thread_id
            WHERE ct.driver_id = %s
            ORDER BY COALESCE(cm.last_message_ts, ct.opened_at) DESC, ct.thread_id DESC
            LIMIT 1
            """,
            (driver_id,),
        ).fetchone()
        messages = []
        thread_id = None
        if thread_row:
            thread_id = thread_row[0]
            # message_ts alone is not a reliable sort key: under the demo's
            # simulated clock (frozen between requests) many messages in the
            # same thread share an identical message_ts, so ties need a
            # deterministic tiebreaker — created_at is real wall-clock
            # insertion time, which is always distinct and monotonic.
            messages = con.execute(
                """
                SELECT sender_type, message_text, message_ts FROM chat_messages
                WHERE thread_id = %s ORDER BY message_ts, created_at
                """,
                (thread_id,),
            ).fetchall()

        appointments = con.execute(
            """
            SELECT a.appointment_id, a.shipment_id, a.appointment_status, d.dock_code,
                   d.dock_type, a.span_start_ts, a.span_end_ts, f.city
            FROM appointments a
            JOIN shipments s ON s.shipment_id = a.shipment_id
            JOIN facilities f ON f.facility_id = s.destination_facility_id
            LEFT JOIN docks d ON d.dock_id = a.dock_id
            WHERE s.driver_id = %s AND a.is_current
            ORDER BY a.span_start_ts
            """,
            (driver_id,),
        ).fetchall()

        hold = con.execute(
            """
            SELECT hg.hold_group_id, hg.shipment_id, MAX(hg.expires_at), MAX(hg.ttl_seconds),
                   MAX(hg.contention_ratio), MAX(hg.dock_id), MAX(d.dock_code),
                   MIN(hg.span_start_ts), MAX(hg.span_end_ts)
            FROM slot_holds hg
            JOIN shipments s ON s.shipment_id = hg.shipment_id
            LEFT JOIN docks d ON d.dock_id = hg.dock_id
            WHERE s.driver_id = %s AND hg.hold_status = 'ACTIVE'
            GROUP BY hg.hold_group_id, hg.shipment_id
            ORDER BY MAX(hg.expires_at) DESC LIMIT 1
            """,
            (driver_id,),
        ).fetchone()

        # Most recent still-open exception for this driver — feeds the chat
        # input's ghost suggestion (a driver mid-exception is more likely to
        # be following up on it than starting a new topic).
        exception = con.execute(
            """
            SELECT exception_type, reported_delay_min, declared_eta_ts, shipment_id
            FROM driver_exceptions
            WHERE driver_id = %s AND exception_status <> ALL(%s)
            ORDER BY reported_at DESC LIMIT 1
            """,
            (driver_id, list(ACTIVE_EXCEPTION_STATUSES_EXCLUDED)),
        ).fetchone()

    return {
        "driver_id": driver_id,
        "now": clock.now().isoformat(),
        "shipments": [
            {
                "shipment_id": r[0],
                "order_reference": r[1],
                "current_status": r[2],
                "priority_code": r[3],
                "destination_city": r[4],
            }
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
                "dock_type": r[4],
                "span_start": r[5].isoformat() if r[5] else None,
                "span_end": r[6].isoformat() if r[6] else None,
                "destination_city": r[7],
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
                "dock_code": hold[6],
                "span_start": hold[7].isoformat() if hold[7] else None,
                "span_end": hold[8].isoformat() if hold[8] else None,
            }
            if hold
            else None
        ),
        "active_exception": (
            {
                "exception_type": exception[0],
                "reported_delay_min": exception[1],
                "declared_eta": exception[2].isoformat() if exception[2] else None,
                "shipment_id": exception[3],
            }
            if exception
            else None
        ),
    }


handler = json_get_handler(_thread_state)
