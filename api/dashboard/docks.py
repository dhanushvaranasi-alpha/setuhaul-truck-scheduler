import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api._http_common import json_get_handler  # noqa: E402
from src import db  # noqa: E402


def _dock_timeline(query: dict) -> dict:
    facility_id = query.get("facility_id", "FAC-JAI-01")

    with db.get_conn() as con:
        docks = con.execute(
            "SELECT dock_id, dock_code, dock_type FROM docks WHERE facility_id = %s ORDER BY dock_code",
            (facility_id,),
        ).fetchall()

        appointments = con.execute(
            """
            SELECT appointment_id, dock_id, shipment_id, appointment_status, span_start_ts, span_end_ts
            FROM appointments
            WHERE dock_id IN (SELECT dock_id FROM docks WHERE facility_id = %s)
              AND appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
            ORDER BY span_start_ts
            """,
            (facility_id,),
        ).fetchall()

        holds = con.execute(
            """
            SELECT DISTINCT hold_group_id, dock_id, shipment_id, expires_at,
                   MIN(span_start_ts) OVER (PARTITION BY hold_group_id) AS span_start,
                   MAX(span_end_ts) OVER (PARTITION BY hold_group_id) AS span_end
            FROM slot_holds
            WHERE dock_id IN (SELECT dock_id FROM docks WHERE facility_id = %s) AND hold_status = 'ACTIVE'
            """,
            (facility_id,),
        ).fetchall()

        events = con.execute(
            """
            SELECT dock_id, event_type, event_start_ts, event_end_ts
            FROM dock_status_events
            WHERE dock_id IN (SELECT dock_id FROM docks WHERE facility_id = %s)
              AND event_type IN ('BREAKDOWN','MAINTENANCE')
            """,
            (facility_id,),
        ).fetchall()

    return {
        "facility_id": facility_id,
        "docks": [{"dock_id": r[0], "dock_code": r[1], "dock_type": r[2]} for r in docks],
        "spans": [
            {
                "appointment_id": r[0],
                "dock_id": r[1],
                "shipment_id": r[2],
                "status": r[3],
                "span_start": r[4].isoformat(),
                "span_end": r[5].isoformat(),
            }
            for r in appointments
        ],
        "holds": [
            {
                "hold_group_id": r[0],
                "dock_id": r[1],
                "shipment_id": r[2],
                "expires_at": r[3].isoformat(),
                "span_start": r[4].isoformat(),
                "span_end": r[5].isoformat(),
            }
            for r in holds
        ],
        "blocked_windows": [
            {
                "dock_id": r[0],
                "event_type": r[1],
                "start": r[2].isoformat(),
                "end": r[3].isoformat() if r[3] else None,
            }
            for r in events
        ],
    }


handler = json_get_handler(_dock_timeline)
