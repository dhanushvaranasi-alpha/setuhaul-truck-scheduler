import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api._http_common import json_get_handler  # noqa: E402
from src import db  # noqa: E402


def _metrics(query: dict) -> dict:
    facility_id = query.get("facility_id", "FAC-JAI-01")

    with db.get_conn() as con:
        # Proof, not just a number: genuinely re-checks for any two active
        # appointments on the same dock with overlapping spans. Should
        # always read 0 — that's the EXCLUDE constraint's guarantee (I2),
        # not application logic.
        conflicts = con.execute(
            """
            SELECT count(*) FROM appointments a1
            JOIN appointments a2 ON a1.dock_id = a2.dock_id AND a1.appointment_id < a2.appointment_id
            WHERE a1.appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
              AND a2.appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
              AND a1.dock_id IN (SELECT dock_id FROM docks WHERE facility_id = %s)
              AND tstzrange(a1.span_start_ts, a1.span_end_ts) && tstzrange(a2.span_start_ts, a2.span_end_ts)
            """,
            (facility_id,),
        ).fetchone()[0]

        total_slots, occupied_slots = con.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM appointment_slot_allocations al
                       JOIN appointments a ON a.appointment_id = al.appointment_id
                       WHERE al.slot_id = sl.slot_id
                         AND a.appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
                   ))
            FROM appointment_slots sl
            WHERE sl.dock_id IN (SELECT dock_id FROM docks WHERE facility_id = %s)
            """,
            (facility_id,),
        ).fetchone()

        infeasible = con.execute(
            """
            SELECT count(*) FROM allocation_decisions ad
            JOIN shipments s ON s.shipment_id = ad.shipment_id
            WHERE ad.outcome IN ('NO_SAME_DAY_SLOT', 'NO_FEASIBLE_SLOT')
              AND s.destination_facility_id = %s
            """,
            (facility_id,),
        ).fetchone()[0]

    utilisation = round(100 * occupied_slots / total_slots, 1) if total_slots else 0.0
    return {
        "facility_id": facility_id,
        "conflicts": conflicts,
        "utilisation_pct": utilisation,
        "options_later_infeasible": infeasible,
    }


handler = json_get_handler(_metrics)
