import uuid
from datetime import timedelta

import pytest

from clock import SimulatedClock
from src import db
from src.config import get_operating_constants
from src.core.booking import reschedule_appointment
from src.core.feasibility import find_feasible_slots_impl
from src.core.holds import create_hold
from src.core.tokens import verify_option_token
from src.reset_demo import reset_demo


@pytest.fixture(autouse=True)
def clean_db():
    reset_demo()
    yield


def _hold_new_slot(shipment_id: str, earliest_ts: str, clock):
    """find_feasible_slots -> hold_slot, mirroring the real tool chain the
    conversation layer drives."""
    result = find_feasible_slots_impl(shipment_id, earliest_ts, clock)
    assert result.status == "options", result
    option = result.spans[0]
    constants = get_operating_constants()
    with db.get_conn() as con:
        token = verify_option_token(con, option.option_token, shipment_id)
        dock_row = con.execute(
            "SELECT sl.dock_id, d.facility_id FROM appointment_slots sl "
            "JOIN docks d ON d.dock_id = sl.dock_id WHERE sl.slot_id = %s",
            (token.span_slot_ids[0],),
        ).fetchone()
        dock_id, facility_id = dock_row
        span_row = con.execute(
            "SELECT MIN(slot_start_ts), MAX(slot_end_ts) FROM appointment_slots WHERE slot_id = ANY(%s)",
            (token.span_slot_ids,),
        ).fetchone()
        span_start, span_end = span_row
        held = create_hold(
            con,
            shipment_id=shipment_id,
            facility_id=facility_id,
            dock_id=dock_id,
            slot_ids=token.span_slot_ids,
            span_start=span_start,
            span_end=span_end,
            band_start=clock.now(),
            band_end=clock.now() + timedelta(hours=constants.search_band_hours),
            clock=clock,
        )
        con.commit()
    assert held.status == "held", held
    return held, dock_id, span_start, span_end, token.span_slot_ids[0]


def test_reschedule_cancels_old_books_new_and_links_them():
    clock = SimulatedClock()
    with db.get_conn() as con:
        old_appointment_id = con.execute(
            "SELECT appointment_id FROM appointments WHERE shipment_id = 'SHP1006' AND is_current"
        ).fetchone()[0]

    held, *_ = _hold_new_slot("SHP1006", "2026-08-04T13:00:00+05:30", clock)

    result = reschedule_appointment(held.hold_group_id, f"IDEM-{uuid.uuid4().hex[:12]}", clock)
    assert result.status == "booked", result
    assert result.appointment_id != old_appointment_id

    with db.get_conn() as con:
        old_status, old_current = con.execute(
            "SELECT appointment_status, is_current FROM appointments WHERE appointment_id = %s",
            (old_appointment_id,),
        ).fetchone()
        new_status, new_current, replaced_id = con.execute(
            "SELECT appointment_status, is_current, replaced_appointment_id FROM appointments WHERE appointment_id = %s",
            (result.appointment_id,),
        ).fetchone()

    assert (old_status, old_current) == ("CANCELLED", False)
    assert (new_status, new_current, replaced_id) == ("PENDING_CONFIRMATION", True, old_appointment_id)


def test_reschedule_without_existing_appointment_is_rejected_not_crashed():
    # SHP1012 has no appointment row at all in seed data.
    clock = SimulatedClock()
    held, *_ = _hold_new_slot("SHP1012", "2026-08-04T13:00:00+05:30", clock)

    result = reschedule_appointment(held.hold_group_id, f"IDEM-{uuid.uuid4().hex[:12]}", clock)

    assert result.status == "rejected"
    assert result.reason == "NOT_RESCHEDULABLE"


def test_reschedule_lost_race_leaves_old_appointment_untouched():
    """If the new slot is lost to a concurrent booking after the old
    appointment would be cancelled, the whole reschedule must roll back
    atomically — the driver keeps their original confirmed appointment
    rather than ending up with neither."""
    clock = SimulatedClock()
    with db.get_conn() as con:
        old_appointment_id = con.execute(
            "SELECT appointment_id FROM appointments WHERE shipment_id = 'SHP1007' AND is_current"
        ).fetchone()[0]

    held, dock_id, span_start, span_end, slot_id = _hold_new_slot(
        "SHP1007", "2026-08-04T13:00:00+05:30", clock
    )

    # Plant a competing appointment on the exact dock+span the hold reserved,
    # bypassing holds entirely (mirrors test_concurrency.py's
    # attempt_raw_booking) so request_booking's INSERT hits no_dock_overlap.
    # is_current=FALSE so it doesn't collide with SHP1008's own per-shipment
    # unique index — SHP1008 is CANCELLED with no appointment in seed data,
    # used here only to satisfy the shipment_id foreign key.
    with db.get_conn() as con:
        with con.transaction():
            con.execute(
                """
                INSERT INTO appointments
                    (appointment_id, shipment_id, slot_id, dock_id,
                     span_start_ts, span_end_ts, appointment_status,
                     booking_source, is_current, booked_at, updated_at)
                VALUES (%s, 'SHP1008', %s, %s, %s, %s, 'PENDING_CONFIRMATION',
                        'DRIVER_CHAT', FALSE, %s, %s)
                """,
                (
                    f"APT-TEST-{uuid.uuid4().hex[:12]}",
                    slot_id,
                    dock_id,
                    span_start,
                    span_end,
                    clock.now(),
                    clock.now(),
                ),
            )

    result = reschedule_appointment(held.hold_group_id, f"IDEM-{uuid.uuid4().hex[:12]}", clock)
    assert result.status == "lost_race", result

    with db.get_conn() as con:
        old_status, old_current = con.execute(
            "SELECT appointment_status, is_current FROM appointments WHERE appointment_id = %s",
            (old_appointment_id,),
        ).fetchone()
        con.execute("DELETE FROM appointments WHERE shipment_id = 'SHP1008'")
        con.commit()

    assert (old_status, old_current) == ("CONFIRMED", True)
