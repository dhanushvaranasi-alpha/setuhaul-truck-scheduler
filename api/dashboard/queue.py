import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api._http_common import json_get_handler  # noqa: E402
from src import db  # noqa: E402
from src.clock_state import get_clock  # noqa: E402
from src.core.allocation import rank_shipments  # noqa: E402


def _yard_queue(query: dict) -> dict:
    facility_id = query.get("facility_id", "FAC-JAI-01")
    with db.get_conn() as con:
        clock = get_clock(con)
        ranking = rank_shipments(con, facility_id, clock)
    return {
        "facility_id": facility_id,
        "queue": [
            {
                "shipment_id": b.shipment_id,
                "total": round(b.total, 2),
                "breakdown": {
                    "importance": round(b.importance, 2),
                    "waiting": round(b.waiting, 2),
                    "facility_fault": round(b.facility_fault, 2),
                    "perishable": round(b.perishable, 2),
                    "past_slot": round(b.past_slot, 2),
                    "eta_uncertainty": round(b.eta_uncertainty, 2),
                    "fairness": round(b.fairness, 2),
                },
            }
            for b in ranking
        ],
    }


handler = json_get_handler(_yard_queue)
