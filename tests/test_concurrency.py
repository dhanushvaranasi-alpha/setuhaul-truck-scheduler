import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import psycopg.errors
import pytest

from clock import IST, OffsetClock, SimulatedClock
from src import db
from src.core.booking import request_booking
from src.core.holds import create_hold
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


# --------------------------------------------------------------------------
# Step 11: the same guarantee, wired through the real request_booking()
# service — feasibility revalidation, hold consumption, idempotency logging,
# warehouse notification, all included. 20 synthetic shipments with
# identical, deliberately-feasible requirements so the EXCLUDE constraint
# (not an unrelated feasibility mismatch) is what decides the winner.
# --------------------------------------------------------------------------

REAL_SLOT_ID = "SLOT-D1-20260806-0930"
REAL_SPAN_START = datetime(2026, 8, 6, 9, 30, 0, tzinfo=IST)
REAL_SPAN_END = datetime(2026, 8, 6, 9, 45, 0, tzinfo=IST)
SYNTHETIC_SHIPMENT_IDS = [f"SYN-CONC-{i:02d}" for i in range(20)]


def _make_synthetic_shipments(clock):
    with db.get_conn() as con:
        for i, shipment_id in enumerate(SYNTHETIC_SHIPMENT_IDS):
            con.execute(
                """
                INSERT INTO shipments
                    (shipment_id, order_reference, carrier_id, driver_id, vehicle_id,
                     origin_name, origin_city, destination_facility_id, customer_name,
                     product_category, load_weight_kg, required_dock_type,
                     temperature_control_required, priority_code, planned_departure_ts,
                     original_eta_ts, expected_unload_min, current_status, created_at, updated_at)
                VALUES (%s, %s, 'CAR001', 'DRV001', 'VEH001', 'Test Origin', 'Jaipur',
                        'FAC-JAI-01', 'Test Customer', 'General', 10000, 'STANDARD',
                        FALSE, 'NORMAL', %s, %s, 15, 'IN_TRANSIT', %s, %s)
                """,
                (
                    shipment_id,
                    f"ORD-SYN-CONC-{i:02d}",
                    clock.now() - timedelta(hours=3),
                    clock.now() - timedelta(hours=1),
                    clock.now(),
                    clock.now(),
                ),
            )
        con.commit()


def _clear_synthetic_shipments():
    with db.get_conn() as con:
        con.execute("DELETE FROM agent_actions WHERE arguments_json::text LIKE '%SYN-CONC%'")
        con.execute("DELETE FROM allocation_decisions WHERE shipment_id LIKE 'SYN-CONC%'")
        con.execute("DELETE FROM slot_holds WHERE shipment_id LIKE 'SYN-CONC%'")
        con.execute(
            """
            DELETE FROM pending_warehouse_replies WHERE appointment_id IN
                (SELECT appointment_id FROM appointments WHERE shipment_id LIKE 'SYN-CONC%')
            """
        )
        con.execute(
            """
            DELETE FROM operational_messages WHERE shipment_id LIKE 'SYN-CONC%'
               OR appointment_id IN
                (SELECT appointment_id FROM appointments WHERE shipment_id LIKE 'SYN-CONC%')
            """
        )
        con.execute("DELETE FROM appointments WHERE shipment_id LIKE 'SYN-CONC%'")
        con.execute("DELETE FROM shipments WHERE shipment_id LIKE 'SYN-CONC%'")
        con.commit()


def _attempt_hold_then_book(shipment_id, clock):
    # slot_holds' own no_hold_overlap EXCLUDE constraint means simultaneous
    # ACTIVE holds on the same span can't coexist — so the realistic race
    # for 20 drivers wanting the same slot happens at hold time, not just
    # at booking time. Whichever thread wins the hold proceeds to book;
    # the rest lose here and never reach request_booking at all.
    with db.get_conn() as con:
        held = create_hold(
            con,
            shipment_id=shipment_id,
            facility_id="FAC-JAI-01",
            dock_id=DOCK_ID,
            slot_ids=[REAL_SLOT_ID],
            span_start=REAL_SPAN_START,
            span_end=REAL_SPAN_END,
            band_start=clock.now(),
            band_end=clock.now() + timedelta(hours=4),
            clock=clock,
        )
        con.commit()
    if held.status != "held":
        return held  # Rejected(SLOT_HELD) — lost the hold race
    return request_booking(held.hold_group_id, f"IDEM-{held.hold_group_id}", clock)


def test_no_double_booking_via_request_booking():
    clock = SimulatedClock()
    _clear_synthetic_shipments()
    _make_synthetic_shipments(clock)
    try:
        results = list(
            ThreadPoolExecutor(20).map(
                lambda sid: _attempt_hold_then_book(sid, clock), SYNTHETIC_SHIPMENT_IDS
            )
        )
        successes = [r for r in results if r.status == "booked"]
        assert len(successes) == 1, f"expected 1, got {len(successes)}"

        with db.get_conn() as con:
            count = con.execute(
                """
                SELECT count(*) FROM appointments
                WHERE dock_id = %s AND appointment_status IN ('CONFIRMED','PENDING_CONFIRMATION')
                  AND tstzrange(span_start_ts, span_end_ts) && tstzrange(%s, %s)
                """,
                (DOCK_ID, REAL_SPAN_START, REAL_SPAN_END),
            ).fetchone()[0]
            assert count == 1
    finally:
        _clear_synthetic_shipments()
