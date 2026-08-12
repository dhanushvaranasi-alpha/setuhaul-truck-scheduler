from datetime import timedelta

import pytest

from src import db
from clock import SimulatedClock
from src.core.feasibility import (
    ShipmentContext,
    build_span_candidates,
    find_feasible_slots_impl,
    get_candidate_docks,
    get_open_slots,
    get_shipment_context,
)
from src.reset_demo import reset_demo


@pytest.fixture(scope="module", autouse=True)
def clean_db():
    reset_demo()


@pytest.fixture
def clock():
    return SimulatedClock()


def test_shp1015_no_reefer_slot(clock):
    result = find_feasible_slots_impl("SHP1015", None, clock)
    assert result.status == "rejected"
    assert result.reason == "NO_SAME_DAY_SLOT"


def test_shp1016_heavy_only(clock):
    with db.get_conn() as con:
        ctx = get_shipment_context(con, "SHP1016")
        docks = get_candidate_docks(con, ctx)
    assert [d.dock_id for d in docks] == ["DOCK-JAI-D6"]

    result = find_feasible_slots_impl("SHP1016", None, clock)
    assert result.status == "options"
    assert all(s.dock_id == "DOCK-JAI-D6" for s in result.spans)


def test_shp1005_75min_span(clock):
    result = find_feasible_slots_impl("SHP1005", None, clock)
    assert result.status == "options"
    assert len(result.spans) > 0
    first = result.spans[0]
    from datetime import datetime

    start = datetime.fromisoformat(first.span_start)
    end = datetime.fromisoformat(first.span_end)
    assert end - start == timedelta(minutes=75)

    with db.get_conn() as con:
        ctx = get_shipment_context(con, "SHP1005")
        docks = get_candidate_docks(con, ctx)
        dock = next(d for d in docks if d.dock_id == first.dock_id)
        slots = get_open_slots(con, dock.dock_id, start, start + timedelta(hours=6))
        candidates = build_span_candidates(dock, slots, ctx.expected_unload_min)
        match = next(c for c in candidates if c.span_start == start)
    assert len(match.slot_ids) == 5  # 5 x 15min = 75min, contiguous, one dock


def test_shp1013_low_confidence(clock):
    result = find_feasible_slots_impl("SHP1013", None, clock)
    assert result.status == "options"
    assert len(result.spans) > 0


def test_frozen_load_gurgaon(clock):
    with db.get_conn() as con:
        base = get_shipment_context(con, "SHP1015")  # any real shipment for a template
        ctx = ShipmentContext(
            shipment_id=base.shipment_id,
            driver_id=base.driver_id,
            facility_id="FAC-GGN-01",
            priority_code="NORMAL",
            load_weight_kg=15000,
            required_dock_type="REEFER",
            temperature_control_required=True,
            expected_unload_min=60,
            effective_eta_ts=base.effective_eta_ts,
            eta_confidence="HIGH",
            eta_updated_at=None,
            deadline_ts=None,
        )
        docks = get_candidate_docks(con, ctx)
    dock_ids = {d.dock_id for d in docks}
    assert "DOCK-GGN-D1" not in dock_ids
    assert "DOCK-GGN-D2" not in dock_ids
    assert "DOCK-GGN-D3" in dock_ids
