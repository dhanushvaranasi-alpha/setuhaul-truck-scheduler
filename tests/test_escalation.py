from datetime import timedelta

import pytest

from clock import IST, SimulatedClock
from src import db
from src.core.escalation import REASON_CODES, escalate_to_human
from src.core.holds import create_hold
from src.reset_demo import reset_demo

THREAD_ID = "THR007"  # DRV003 / SHP1003 — created by this module's own fixture
SHIPMENT_ID = "SHP1003"
DOCK_ID = "DOCK-JAI-D1"
SLOT_ID = "SLOT-D1-20260806-1100"


@pytest.fixture(scope="module", autouse=True)
def clean_db():
    reset_demo()
    with db.get_conn() as con:
        con.execute(
            """
            INSERT INTO chat_threads (thread_id, driver_id, shipment_id, opened_at, thread_status, thread_intent)
            VALUES (%s, 'DRV003', %s, now(), 'OPEN', 'REPORT_DELAY')
            """,
            (THREAD_ID, SHIPMENT_ID),
        )
        con.commit()
    yield
    with db.get_conn() as con:
        con.execute("DELETE FROM escalations WHERE thread_id = %s", (THREAD_ID,))
        con.execute("DELETE FROM slot_holds WHERE shipment_id = %s", (SHIPMENT_ID,))
        con.execute("DELETE FROM chat_threads WHERE thread_id = %s", (THREAD_ID,))
        con.commit()


def test_all_twelve_reason_codes_accepted():
    assert len(REASON_CODES) == 12
    clock = SimulatedClock()
    with db.get_conn() as con:
        for code in REASON_CODES:
            result = escalate_to_human(con, THREAD_ID, code, {"note": "test"}, clock)
            con.commit()
            assert result.status == "escalated"
            assert result.reason_code == code


def test_acknowledge_deadline_is_ten_minutes_out():
    clock = SimulatedClock()
    with db.get_conn() as con:
        result = escalate_to_human(con, THREAD_ID, "NO_FEASIBLE_SLOT", {}, clock)
        con.commit()
    from datetime import datetime

    update_by = datetime.fromisoformat(result.update_by)
    assert update_by - clock.now() == timedelta(minutes=10)


def test_rejects_unknown_reason_code():
    clock = SimulatedClock()
    with db.get_conn() as con, pytest.raises(ValueError):
        escalate_to_human(con, THREAD_ID, "MADE_UP_REASON", {}, clock)


def test_never_auto_releases_an_active_hold():
    clock = SimulatedClock()
    span_start = clock.now().astimezone(IST).replace(
        year=2026, month=8, day=6, hour=11, minute=30, second=0, microsecond=0
    )
    span_end = span_start + timedelta(minutes=15)

    with db.get_conn() as con:
        held = create_hold(
            con,
            shipment_id=SHIPMENT_ID,
            facility_id="FAC-JAI-01",
            dock_id=DOCK_ID,
            slot_ids=[SLOT_ID],
            span_start=span_start,
            span_end=span_end,
            band_start=clock.now(),
            band_end=clock.now() + timedelta(hours=4),
            clock=clock,
        )
        con.commit()
    assert held.status == "held"

    with db.get_conn() as con:
        escalate_to_human(con, THREAD_ID, "NO_FEASIBLE_SLOT", {}, clock)
        con.commit()

    with db.get_conn() as con:
        status = con.execute(
            "SELECT hold_status FROM slot_holds WHERE hold_group_id = %s LIMIT 1",
            (held.hold_group_id,),
        ).fetchone()[0]
    assert status == "ACTIVE"
