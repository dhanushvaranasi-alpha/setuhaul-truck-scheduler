from datetime import timedelta

import pytest

from src import db
from clock import SimulatedClock
from src.config import get_allocation_policy
from src.core.allocation import rank_shipments, score_shipment
from src.reset_demo import reset_demo

REFERENCE_TABLE = {
    "SHP1005": 10.88,
    "SHP1009": 7.80,
    "SHP1014": 7.40,
    "SHP1006": 4.40,
    "SHP1004": 2.98,
    "SHP1003": 1.00,
    "SHP1013": 0.30,
}


@pytest.fixture(scope="module", autouse=True)
def clean_db():
    reset_demo()


def test_allocation_ordering_matches_reference_table():
    clock = SimulatedClock()  # snapshot instant, matches score_final.py's NOW
    with db.get_conn() as con:
        ranking = rank_shipments(con, "FAC-JAI-01", clock)

    by_shipment = {b.shipment_id: round(b.total, 2) for b in ranking}

    # score_shipment() reproduces every reference value exactly.
    for shipment_id, expected in REFERENCE_TABLE.items():
        assert by_shipment[shipment_id] == expected, shipment_id

    # ... and their relative order is preserved (§4's reference ordering).
    # The live competing set also includes a few lower-priority shipments
    # not shown in the brief's excerpt table; they all rank below every
    # reference shipment, so they don't affect any allocation outcome.
    ordered_ids = [b.shipment_id for b in ranking if b.shipment_id in REFERENCE_TABLE]
    assert ordered_ids == list(REFERENCE_TABLE.keys())


def test_anti_starvation_3h_waiting_overtakes_fresh_critical():
    policy = get_allocation_policy()
    now = SimulatedClock().now()
    waiting_since = now - timedelta(hours=3)

    waiting_normal = {
        "shipment_id": "SYN-WAITING",
        "priority_code": "NORMAL",
        "eta_confidence": "HIGH",
        "temperature_control_required": False,
        "carrier_id": "SYN-CARRIER-A",
        "gate_in_ts": waiting_since,
        "queue_state": "WAITING_LATE",
        "span_start_ts": waiting_since,
    }
    fresh_critical = {
        "shipment_id": "SYN-FRESH-CRITICAL",
        "priority_code": "CRITICAL",
        "eta_confidence": "HIGH",
        "temperature_control_required": False,
        "carrier_id": "SYN-CARRIER-B",
        "gate_in_ts": None,
        "queue_state": None,
        "span_start_ts": None,
    }

    waiting_score = score_shipment(waiting_normal, now, {}, policy)
    critical_score = score_shipment(fresh_critical, now, {}, policy)

    assert waiting_score.total > critical_score.total
