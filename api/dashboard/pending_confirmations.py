import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api._http_common import json_get_handler  # noqa: E402
from src import db  # noqa: E402
from src.clock_state import get_clock  # noqa: E402


def _pending_confirmations(query: dict) -> dict:
    facility_id = query.get("facility_id", "FAC-JAI-01")

    with db.get_conn() as con:
        clock = get_clock(con)
        rows = con.execute(
            """
            SELECT a.appointment_id, a.shipment_id, a.booked_at, a.span_start_ts, a.span_end_ts,
                   dk.dock_code, d.driver_id, d.driver_name
            FROM appointments a
            JOIN shipments s ON s.shipment_id = a.shipment_id
            JOIN drivers d ON d.driver_id = s.driver_id
            LEFT JOIN docks dk ON dk.dock_id = a.dock_id
            WHERE a.appointment_status = 'PENDING_CONFIRMATION' AND a.is_current
              AND a.dock_id IN (SELECT dock_id FROM docks WHERE facility_id = %s)
            ORDER BY a.booked_at
            """,
            (facility_id,),
        ).fetchall()

    return {
        "facility_id": facility_id,
        "now": clock.now().isoformat(),
        "pending": [
            {
                "appointment_id": r[0],
                "shipment_id": r[1],
                "booked_at": r[2].isoformat(),
                "span_start": r[3].isoformat() if r[3] else None,
                "span_end": r[4].isoformat() if r[4] else None,
                "dock_code": r[5],
                "driver_id": r[6],
                "driver_name": r[7],
            }
            for r in rows
        ],
    }


handler = json_get_handler(_pending_confirmations)
