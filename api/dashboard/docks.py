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
            SELECT a.appointment_id, a.dock_id, a.shipment_id, a.appointment_status,
                   a.span_start_ts, a.span_end_ts, d.driver_id, d.driver_name, s.required_dock_type
            FROM appointments a
            JOIN shipments s ON s.shipment_id = a.shipment_id
            JOIN drivers d ON d.driver_id = s.driver_id
            WHERE a.dock_id IN (SELECT dock_id FROM docks WHERE facility_id = %s)
              AND a.appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
            ORDER BY a.span_start_ts
            """,
            (facility_id,),
        ).fetchall()

        holds = con.execute(
            """
            SELECT DISTINCT h.hold_group_id, h.dock_id, h.shipment_id, h.expires_at,
                   MIN(h.span_start_ts) OVER (PARTITION BY h.hold_group_id) AS span_start,
                   MAX(h.span_end_ts) OVER (PARTITION BY h.hold_group_id) AS span_end,
                   d.driver_id, d.driver_name
            FROM slot_holds h
            JOIN shipments s ON s.shipment_id = h.shipment_id
            JOIN drivers d ON d.driver_id = s.driver_id
            WHERE h.dock_id IN (SELECT dock_id FROM docks WHERE facility_id = %s) AND h.hold_status = 'ACTIVE'
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
                "driver_id": r[6],
                "driver_name": r[7],
                "required_dock_type": r[8],
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
                "driver_id": r[6],
                "driver_name": r[7],
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
