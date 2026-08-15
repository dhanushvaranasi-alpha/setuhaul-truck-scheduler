import uuid
from datetime import timedelta

import pytest

from clock import IST, SimulatedClock
from src import db
from src.core.escalation import escalate_to_human
from src.core.holds import create_hold
from src.core.notifications import notify_warehouse
from src.core.sweeps import (
    deliver_warehouse_replies,
    sweep_escalations,
    sweep_holds,
    sweep_pending_confirmations,
)
from src.reset_demo import reset_demo

DOCK_ID = "DOCK-JAI-D1"


@pytest.fixture(scope="module", autouse=True)
def clean_db():
    reset_demo()


def _clear(shipment_id, appointment_id=None):
    with db.get_conn() as con:
        con.execute(
            "DELETE FROM chat_messages WHERE thread_id IN (SELECT thread_id FROM chat_threads WHERE shipment_id = %s)",
            (shipment_id,),
        )
        con.execute("DELETE FROM escalations WHERE shipment_id = %s", (shipment_id,))
        con.execute("DELETE FROM slot_holds WHERE shipment_id = %s", (shipment_id,))
        con.execute("DELETE FROM chat_threads WHERE shipment_id = %s", (shipment_id,))
        if appointment_id:
            con.execute("DELETE FROM pending_warehouse_replies WHERE appointment_id = %s", (appointment_id,))
            con.execute("DELETE FROM operational_messages WHERE appointment_id = %s", (appointment_id,))
            con.execute("DELETE FROM appointment_slot_allocations WHERE appointment_id = %s", (appointment_id,))
            con.execute("DELETE FROM appointments WHERE appointment_id = %s", (appointment_id,))
        con.commit()


def _make_synthetic_shipment(shipment_id: str, clock) -> None:
    """A clean shipment with requirements that trivially match DOCK-JAI-D1
    and a 15-minute span, so feasibility revalidation never trips on
    unrelated real-seed-data specifics (weight, duration, existing
    appointments) — isolating the sweep logic under test."""
    with db.get_conn() as con:
        con.execute("DELETE FROM chat_threads WHERE shipment_id = %s", (shipment_id,))
        con.execute("DELETE FROM shipments WHERE shipment_id = %s", (shipment_id,))
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
                    FALSE, 'NORMAL', %s, %s, 15, 'WAITING', %s, %s)
            ON CONFLICT (shipment_id) DO NOTHING
            """,
            (
                shipment_id,
                f"ORD-{shipment_id}",
                clock.now() - timedelta(hours=3),
                clock.now() - timedelta(hours=1),
                clock.now(),
                clock.now(),
            ),
        )
        con.execute(
            """
            INSERT INTO chat_threads (thread_id, driver_id, shipment_id, opened_at, thread_status, thread_intent)
            VALUES (%s, 'DRV001', %s, %s, 'OPEN', 'REPORT_DELAY')
            """,
            (f"THR-{shipment_id}", shipment_id, clock.now()),
        )
        con.commit()


def _clear_synthetic_shipment(shipment_id: str) -> None:
    with db.get_conn() as con:
        con.execute("DELETE FROM chat_messages WHERE thread_id = %s", (f"THR-{shipment_id}",))
        con.execute("DELETE FROM chat_threads WHERE shipment_id = %s", (shipment_id,))
        con.execute("DELETE FROM shipments WHERE shipment_id = %s", (shipment_id,))
        con.commit()


def test_sweep_holds_expires_and_notifies():
    shipment_id = "SHP1017"  # DRV001
    clock = SimulatedClock()
    span_start = clock.now().astimezone(IST).replace(
        year=2026, month=8, day=6, hour=13, minute=0, second=0, microsecond=0
    )
    span_end = span_start + timedelta(minutes=15)
    _clear(shipment_id)
    try:
        with db.get_conn() as con:
            con.execute(
                """
                INSERT INTO chat_threads (thread_id, driver_id, shipment_id, opened_at, thread_status, thread_intent)
                VALUES (%s, 'DRV001', %s, %s, 'OPEN', 'REPORT_DELAY')
                """,
                (f"THR-{shipment_id}", shipment_id, clock.now()),
            )
            con.commit()
        with db.get_conn() as con:
            held = create_hold(
                con,
                shipment_id=shipment_id,
                facility_id="FAC-JAI-01",
                dock_id=DOCK_ID,
                slot_ids=["SLOT-D1-20260806-1300"],
                span_start=span_start,
                span_end=span_end,
                band_start=clock.now(),
                band_end=clock.now() + timedelta(hours=4),
                clock=clock,
            )
            con.commit()
        assert held.status == "held"

        clock.advance_seconds(held.ttl_seconds + 60)
        summary = sweep_holds(clock)
        assert summary["expired_shipment_count"] >= 1

        with db.get_conn() as con:
            status = con.execute(
                "SELECT hold_status FROM slot_holds WHERE hold_group_id = %s LIMIT 1",
                (held.hold_group_id,),
            ).fetchone()[0]
            note = con.execute(
                """
                SELECT count(*) FROM chat_messages cm
                JOIN chat_threads ct ON ct.thread_id = cm.thread_id
                WHERE ct.shipment_id = %s AND cm.sender_type = 'AGENT'
                """,
                (shipment_id,),
            ).fetchone()[0]
        assert status == "EXPIRED"
        assert note >= 1
    finally:
        _clear(shipment_id)


def test_sweep_pending_confirmations_expires_stale_delivered():
    shipment_id = "SYN-SWEEP-PC"
    appointment_id = f"APT-TEST-{uuid.uuid4().hex[:8]}"
    clock = SimulatedClock()
    slot_id = "SLOT-D1-20260806-1400"
    span_start = clock.now().astimezone(IST).replace(
        year=2026, month=8, day=6, hour=14, minute=0, second=0, microsecond=0
    )
    span_end = span_start + timedelta(minutes=15)
    _make_synthetic_shipment(shipment_id, clock)
    try:
        with db.get_conn() as con:
            con.execute(
                """
                INSERT INTO appointments
                    (appointment_id, shipment_id, slot_id, dock_id, span_start_ts, span_end_ts,
                     appointment_status, booking_source, is_current, booked_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'PENDING_CONFIRMATION', 'DRIVER_CHAT', TRUE, %s, %s)
                """,
                (appointment_id, shipment_id, slot_id, DOCK_ID, span_start, span_end, clock.now(), clock.now()),
            )
            notify_warehouse(con, appointment_id, shipment_id, DOCK_ID, clock)
            con.execute(
                "UPDATE operational_messages SET delivery_status='DELIVERED' WHERE appointment_id=%s",
                (appointment_id,),
            )
            con.commit()

        clock.advance_minutes(25)  # past delivered_expiry_minutes (20)
        summary = sweep_pending_confirmations(clock)
        assert summary["expired"] >= 1

        with db.get_conn() as con:
            status = con.execute(
                "SELECT appointment_status FROM appointments WHERE appointment_id = %s", (appointment_id,)
            ).fetchone()[0]
            esc = con.execute(
                "SELECT count(*) FROM escalations WHERE shipment_id = %s AND reason_code = 'PENDING_EXPIRED'",
                (shipment_id,),
            ).fetchone()[0]
        assert status == "CANCELLED"
        assert esc >= 1
    finally:
        _clear(shipment_id, appointment_id)
        _clear_synthetic_shipment(shipment_id)


def test_sweep_escalations_advances_ladder():
    thread_id = "THR007"  # DRV003 / SHP1003
    shipment_id = "SHP1003"
    clock = SimulatedClock()
    _clear(shipment_id)
    try:
        with db.get_conn() as con:
            con.execute(
                """
                INSERT INTO chat_threads (thread_id, driver_id, shipment_id, opened_at, thread_status, thread_intent)
                VALUES (%s, 'DRV003', %s, %s, 'OPEN', 'REPORT_DELAY')
                """,
                (thread_id, shipment_id, clock.now()),
            )
            con.commit()
        with db.get_conn() as con:
            result = escalate_to_human(con, thread_id, "NO_FEASIBLE_SLOT", {}, clock)
            con.commit()

        clock.advance_minutes(15)  # past the 10-minute ack deadline
        summary = sweep_escalations(clock)
        assert summary["advanced"] >= 1

        with db.get_conn() as con:
            position, due = con.execute(
                "SELECT contact_ladder_position, acknowledge_due_at FROM escalations WHERE escalation_id = %s",
                (result.escalation_id,),
            ).fetchone()
        assert position == 1
        assert due > clock.now()
    finally:
        _clear(shipment_id)


def test_deliver_warehouse_replies_confirms_on_affirmative():
    shipment_id = "SYN-SWEEP-WH"
    appointment_id = f"APT-TEST-{uuid.uuid4().hex[:8]}"
    clock = SimulatedClock()
    slot_id = "SLOT-D1-20260806-1500"
    span_start = clock.now().astimezone(IST).replace(
        year=2026, month=8, day=6, hour=15, minute=0, second=0, microsecond=0
    )
    span_end = span_start + timedelta(minutes=15)
    _make_synthetic_shipment(shipment_id, clock)
    try:
        with db.get_conn() as con:
            con.execute(
                """
                INSERT INTO appointments
                    (appointment_id, shipment_id, slot_id, dock_id, span_start_ts, span_end_ts,
                     appointment_status, booking_source, is_current, booked_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'PENDING_CONFIRMATION', 'DRIVER_CHAT', TRUE, %s, %s)
                """,
                (appointment_id, shipment_id, slot_id, DOCK_ID, span_start, span_end, clock.now(), clock.now()),
            )
            con.execute(
                "INSERT INTO appointment_slot_allocations VALUES (%s, %s, 0)",
                (appointment_id, slot_id),
            )
            notify_warehouse(con, appointment_id, shipment_id, DOCK_ID, clock)  # CONFIRM mode default
            con.commit()

        clock.advance_seconds(60)  # past the stub's reply_delay_seconds (45)
        summary = deliver_warehouse_replies(clock)
        assert summary["confirmed"] >= 1

        with db.get_conn() as con:
            status = con.execute(
                "SELECT appointment_status FROM appointments WHERE appointment_id = %s", (appointment_id,)
            ).fetchone()[0]
        assert status == "CONFIRMED"
    finally:
        _clear(shipment_id, appointment_id)
        _clear_synthetic_shipment(shipment_id)
