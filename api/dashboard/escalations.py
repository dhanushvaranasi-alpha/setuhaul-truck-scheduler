import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api._http_common import json_get_handler  # noqa: E402
from src import db  # noqa: E402
from src.clock_state import get_clock  # noqa: E402


def _escalations(_query: dict) -> dict:
    with db.get_conn() as con:
        clock = get_clock(con)
        rows = con.execute(
            """
            SELECT e.escalation_id, e.shipment_id, e.reason_code, e.status,
                   e.acknowledge_due_at, e.created_at, e.contact_ladder_position,
                   fc.contact_name, d.driver_id, d.driver_name
            FROM escalations e
            LEFT JOIN facility_contacts fc ON fc.contact_id = e.assigned_contact_id
            LEFT JOIN shipments s ON s.shipment_id = e.shipment_id
            LEFT JOIN drivers d ON d.driver_id = s.driver_id
            WHERE e.status = 'OPEN'
            ORDER BY e.acknowledge_due_at
            """
        ).fetchall()

    return {
        "now": clock.now().isoformat(),
        "escalations": [
            {
                "escalation_id": r[0],
                "shipment_id": r[1],
                "reason_code": r[2],
                "status": r[3],
                "acknowledge_due_at": r[4].isoformat(),
                "created_at": r[5].isoformat(),
                "contact_ladder_position": r[6],
                "assigned_to": r[7],
                "driver_id": r[8],
                "driver_name": r[9],
                "overdue": clock.now() > r[4],
            }
            for r in rows
        ],
    }


handler = json_get_handler(_escalations)
