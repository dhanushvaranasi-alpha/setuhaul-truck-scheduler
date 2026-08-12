from datetime import datetime, timedelta

import pytest

from src import db
from src.clock import IST, SimulatedClock
from src.core.holds import create_hold
from src.reset_demo import reset_demo

FACILITY_ID = "FAC-JAI-01"
DOCK_ID = "DOCK-JAI-D1"
SLOT_ID = "SLOT-D1-20260806-0600"


def _clear_test_holds():
    with db.get_conn() as con:
        con.execute("DELETE FROM slot_holds WHERE shipment_id IN ('SHP1001', 'SHP1002')")
        con.commit()


@pytest.fixture(scope="module", autouse=True)
def clean_db():
    reset_demo()
    yield
    _clear_test_holds()


@pytest.fixture(autouse=True)
def clean_test_rows():
    _clear_test_holds()
    yield


def test_expired_hold_does_not_block_a_new_hold():
    clock = SimulatedClock()
    span_start = datetime(2026, 8, 6, 6, 0, 0, tzinfo=IST)
    span_end = datetime(2026, 8, 6, 6, 15, 0, tzinfo=IST)
    band_start = clock.now()
    band_end = clock.now() + timedelta(hours=4)

    with db.get_conn() as con:
        first = create_hold(
            con,
            shipment_id="SHP1001",
            facility_id=FACILITY_ID,
            dock_id=DOCK_ID,
            slot_ids=[SLOT_ID],
            span_start=span_start,
            span_end=span_end,
            band_start=band_start,
            band_end=band_end,
            clock=clock,
        )
        con.commit()
    assert first.status == "held"

    # The hold expired 7 minutes ago.
    clock.advance_seconds(first.ttl_seconds + 7 * 60)

    with db.get_conn() as con:
        second = create_hold(
            con,
            shipment_id="SHP1002",
            facility_id=FACILITY_ID,
            dock_id=DOCK_ID,
            slot_ids=[SLOT_ID],
            span_start=span_start,
            span_end=span_end,
            band_start=clock.now(),
            band_end=clock.now() + timedelta(hours=4),
            clock=clock,
        )
        con.commit()
    assert second.status == "held"

    with db.get_conn() as con:
        first_status = con.execute(
            "SELECT hold_status FROM slot_holds WHERE shipment_id = 'SHP1001'"
        ).fetchone()[0]
        second_status = con.execute(
            "SELECT hold_status FROM slot_holds WHERE shipment_id = 'SHP1002'"
        ).fetchone()[0]
    assert first_status == "EXPIRED"
    assert second_status == "ACTIVE"
