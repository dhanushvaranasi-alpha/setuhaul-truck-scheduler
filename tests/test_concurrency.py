import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import psycopg.errors
import pytest

from src import db
from src.clock import IST, OffsetClock
from src.reset_demo import reset_demo

DOCK_ID = "DOCK-JAI-D1"
SLOT_ID = "SLOT-D1-20260806-0600"  # third day, unoccupied by seed data
SPAN_START = datetime(2026, 8, 6, 6, 0, 0, tzinfo=IST)
SPAN_END = datetime(2026, 8, 6, 6, 45, 0, tzinfo=IST)

# 20 distinct shipments competing for the exact same dock+span. This drives
# the design for Step 11's request_booking(): whatever mechanism books a
# span, the database EXCLUDE constraint (I2) must be the thing that lets
# exactly one of these win, not application-level coordination.
SHIPMENT_IDS = [f"SHP{1000 + i:04d}" for i in range(1, 21)]


@dataclass(frozen=True)
class RawBookingResult:
    status: Literal["booked", "lost_race"]


def attempt_raw_booking(shipment_id: str, clock) -> RawBookingResult:
    # is_current=FALSE deliberately: several seeded shipments already have an
    # active appointment, and ux_current_active_appointment_per_shipment (a
    # SEPARATE per-shipment invariant) would otherwise collide independently
    # of the dock/span guard this test is isolating. That partial unique
    # index only applies WHERE is_current, so it doesn't fire here, while
    # no_dock_overlap (which this test exists to prove) checks
    # appointment_status regardless of is_current.
    with db.get_conn() as con:
        try:
            with con.transaction():
                con.execute(
                    """
                    INSERT INTO appointments
                        (appointment_id, shipment_id, slot_id, dock_id,
                         span_start_ts, span_end_ts, appointment_status,
                         booking_source, is_current, booked_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, 'PENDING_CONFIRMATION',
                            'DRIVER_CHAT', FALSE, %s, %s)
                    """,
                    (
                        f"APT-TEST-{uuid.uuid4().hex[:12]}",
                        shipment_id,
                        SLOT_ID,
                        DOCK_ID,
                        SPAN_START,
                        SPAN_END,
                        clock.now(),
                        clock.now(),
                    ),
                )
        except (psycopg.errors.ExclusionViolation, psycopg.errors.DeadlockDetected):
            # ExclusionViolation (23P01) is the normal lost-race signal (I2).
            # DeadlockDetected (40P01) is a documented GiST EXCLUDE behavior
            # under heavy concurrent contention on the same key range — the
            # victim's transaction is fully rolled back by Postgres, so it's
            # equally safe to treat as "this attempt didn't win".
            return RawBookingResult(status="lost_race")
    return RawBookingResult(status="booked")


def _clear_test_appointments():
    with db.get_conn() as con:
        con.execute("DELETE FROM appointments WHERE appointment_id LIKE 'APT-TEST-%'")
        con.commit()


@pytest.fixture(scope="module", autouse=True)
def clean_db():
    reset_demo()  # known baseline once for the module
    yield
    _clear_test_appointments()


@pytest.fixture(autouse=True)
def clean_test_rows():
    _clear_test_appointments()
    yield


@pytest.mark.parametrize("run", range(10))
def test_no_double_booking(run):
    clock = OffsetClock()

    def attempt(shipment_id):
        return attempt_raw_booking(shipment_id, clock)

    results = list(ThreadPoolExecutor(20).map(attempt, SHIPMENT_IDS))
    successes = [r for r in results if r.status == "booked"]
    assert len(successes) == 1, f"expected 1, got {len(successes)}"

    with db.get_conn() as con:
        count = con.execute(
            """
            SELECT count(*) FROM appointments
            WHERE dock_id = %s AND appointment_status IN ('CONFIRMED','PENDING_CONFIRMATION')
              AND tstzrange(span_start_ts, span_end_ts) && tstzrange(%s, %s)
            """,
            (DOCK_ID, SPAN_START, SPAN_END),
        ).fetchone()[0]
        assert count == 1
