from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from clock import IST, Clock
from ..config import get_operating_constants
from ..models import FeasibleSpan, Options, Rejected, ToolResult

ACTIVE_APPOINTMENT_STATUSES = ("PENDING_CONFIRMATION", "CONFIRMED", "IN_PROGRESS")
BLOCKING_EVENT_TYPES = ("BREAKDOWN", "MAINTENANCE")  # I12: CAPACITY_REDUCTION does not block
DEADLINE_CONSTRAINT_TYPES = ("LATEST_GATE_OUT", "LATEST_UNLOAD_START")


@dataclass(frozen=True)
class ShipmentContext:
    shipment_id: str
    driver_id: str
    facility_id: str
    priority_code: str
    load_weight_kg: int
    required_dock_type: str
    temperature_control_required: bool
    expected_unload_min: int
    effective_eta_ts: datetime
    eta_confidence: str
    eta_updated_at: datetime | None
    deadline_ts: datetime | None


@dataclass(frozen=True)
class DockCandidate:
    dock_id: str
    dock_code: str


@dataclass(frozen=True)
class SlotRow:
    slot_id: str
    slot_start: datetime
    slot_end: datetime


@dataclass(frozen=True)
class SpanCandidate:
    dock: DockCandidate
    slot_ids: list[str]
    span_start: datetime
    span_end: datetime


def get_shipment_context(con, shipment_id: str) -> ShipmentContext:
    row = con.execute(
        """
        SELECT shipment_id, driver_id, destination_facility_id, priority_code,
               load_weight_kg, required_dock_type, temperature_control_required,
               expected_unload_min, original_eta_ts
        FROM shipments WHERE shipment_id = %s
        """,
        (shipment_id,),
    ).fetchone()

    eta_row = con.execute(
        """
        SELECT declared_eta_ts, confidence_code, created_at
        FROM eta_updates WHERE shipment_id = %s
        ORDER BY created_at DESC LIMIT 1
        """,
        (shipment_id,),
    ).fetchone()
    if eta_row:
        effective_eta_ts, eta_confidence, eta_updated_at = eta_row
    else:
        effective_eta_ts, eta_confidence, eta_updated_at = row[8], "MEDIUM", None

    deadline_row = con.execute(
        """
        SELECT MIN(constraint_value) FROM driver_constraints
        WHERE shipment_id = %s AND is_active = TRUE
          AND constraint_type = ANY(%s)
        """,
        (shipment_id, list(DEADLINE_CONSTRAINT_TYPES)),
    ).fetchone()
    deadline_ts = deadline_row[0] if deadline_row else None

    return ShipmentContext(
        shipment_id=row[0],
        driver_id=row[1],
        facility_id=row[2],
        priority_code=row[3],
        load_weight_kg=row[4],
        required_dock_type=row[5],
        temperature_control_required=row[6],
        expected_unload_min=row[7],
        effective_eta_ts=effective_eta_ts,
        eta_confidence=eta_confidence,
        eta_updated_at=eta_updated_at,
        deadline_ts=deadline_ts,
    )


def dock_type_matches(required_dock_type: str, dock_type: str) -> bool:
    # F7: ANY matches STANDARD only (I5 domain rule, decision 12)
    if required_dock_type == "ANY":
        return dock_type == "STANDARD"
    return dock_type == required_dock_type


def get_candidate_docks(con, ctx: ShipmentContext) -> list[DockCandidate]:
    """F4, F6, F7: capability filter. Reads docks, never facility_rules (I5)."""
    rows = con.execute(
        """
        SELECT dock_id, dock_code, dock_type, supports_refrigerated, max_vehicle_weight_kg
        FROM docks
        WHERE facility_id = %s AND dock_status = 'ACTIVE'
        """,
        (ctx.facility_id,),
    ).fetchall()

    candidates = []
    for dock_id, dock_code, dock_type, supports_refrigerated, max_weight in rows:
        if ctx.temperature_control_required and not supports_refrigerated:
            continue  # F4
        if ctx.load_weight_kg > max_weight:
            continue  # F6
        if not dock_type_matches(ctx.required_dock_type, dock_type):
            continue  # F7
        candidates.append(DockCandidate(dock_id=dock_id, dock_code=dock_code))
    return candidates


def get_facility_hours(con, facility_id: str) -> tuple:
    row = con.execute(
        "SELECT open_time, close_time FROM facilities WHERE facility_id = %s",
        (facility_id,),
    ).fetchone()
    return row[0], row[1]


def get_last_new_start_time(con, facility_id: str):
    row = con.execute(
        """
        SELECT rule_value FROM facility_rules
        WHERE facility_id = %s AND rule_type = 'LAST_NEW_START_TIME' AND active_flag = TRUE
        """,
        (facility_id,),
    ).fetchone()
    if row is None:
        return None
    hour, minute = row[0].split(":")[:2]
    return int(hour), int(minute)


def get_open_slots(con, dock_id: str, from_ts: datetime, to_ts: datetime) -> list[SlotRow]:
    rows = con.execute(
        """
        SELECT slot_id, slot_start_ts, slot_end_ts FROM appointment_slots
        WHERE dock_id = %s AND slot_status = 'OPEN'
          AND slot_start_ts >= %s AND slot_start_ts < %s
        ORDER BY slot_start_ts
        """,
        (dock_id, from_ts, to_ts),
    ).fetchall()
    return [SlotRow(slot_id=r[0], slot_start=r[1], slot_end=r[2]) for r in rows]


def build_span_candidates(
    dock: DockCandidate, slots: list[SlotRow], unload_min: int
) -> list[SpanCandidate]:
    """For each possible starting slot, walk forward while contiguous until the
    span covers >= unload_min. A gap before that means no candidate from this
    start (F8 would fail anyway)."""
    candidates = []
    duration = timedelta(minutes=unload_min)
    for i in range(len(slots)):
        start = slots[i]
        span_end = start.slot_start + duration
        chain = [start.slot_id]
        cursor_end = start.slot_end
        j = i
        ok = cursor_end >= span_end
        while not ok:
            j += 1
            if j >= len(slots) or slots[j].slot_start != cursor_end:
                ok = False
                break
            chain.append(slots[j].slot_id)
            cursor_end = slots[j].slot_end
            ok = cursor_end >= span_end
        if ok:
            candidates.append(
                SpanCandidate(
                    dock=dock, slot_ids=chain, span_start=start.slot_start, span_end=span_end
                )
            )
    return candidates


def evaluate_span(
    con, ctx: ShipmentContext, span: SpanCandidate, clock: Clock
) -> list[str]:
    """All 13 predicates against the whole span (I7). Returns failing reason codes."""
    failures: list[str] = []
    constants = get_operating_constants()

    # F1
    if span.span_start < ctx.effective_eta_ts + timedelta(minutes=constants.eta_buffer_minutes):
        failures.append("ETA_TOO_LATE")

    # F2 — facility hours are civil time in IST; TIMESTAMPTZ values come back
    # from Postgres with UTC tzinfo, so convert before comparing time-of-day.
    span_start_ist = span.span_start.astimezone(IST)
    span_end_ist = span.span_end.astimezone(IST)
    open_time, close_time = get_facility_hours(con, ctx.facility_id)
    if span_start_ist.time() < open_time or span_end_ist.time() > close_time:
        failures.append("OUTSIDE_OPERATING_HOURS")

    # F3
    last_start = get_last_new_start_time(con, ctx.facility_id)
    if last_start is not None:
        hour, minute = last_start
        if span_start_ist.time() >= span_start_ist.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        ).time():
            failures.append("AFTER_LAST_START_TIME")

    # F4, F6, F7 (redundant with candidate-dock filtering, kept for
    # booking-time revalidation of an already-chosen span)
    dock_row = con.execute(
        "SELECT dock_type, supports_refrigerated, max_vehicle_weight_kg FROM docks WHERE dock_id = %s",
        (span.dock.dock_id,),
    ).fetchone()
    dock_type, supports_refrigerated, max_weight = dock_row
    if ctx.temperature_control_required and not supports_refrigerated:
        failures.append("REEFER_REQUIRED")
    if ctx.load_weight_kg > max_weight:
        failures.append("DOCK_WEIGHT_LIMIT")
    if not dock_type_matches(ctx.required_dock_type, dock_type):
        failures.append("DOCK_TYPE_MISMATCH")

    # F8
    if (span.span_end - span.span_start) < timedelta(minutes=ctx.expected_unload_min):
        failures.append("INSUFFICIENT_DURATION")

    # F9 (I12: only BREAKDOWN/MAINTENANCE block; I7: whole span, not just first slot)
    event = con.execute(
        """
        SELECT 1 FROM dock_status_events
        WHERE dock_id = %s AND event_type = ANY(%s)
          AND event_start_ts < %s
          AND COALESCE(event_end_ts, 'infinity'::timestamptz) > %s
        LIMIT 1
        """,
        (span.dock.dock_id, list(BLOCKING_EVENT_TYPES), span.span_end, span.span_start),
    ).fetchone()
    if event:
        failures.append("DOCK_UNAVAILABLE")

    # F10 (I7: every slot)
    blocked = con.execute(
        "SELECT 1 FROM appointment_slots WHERE slot_id = ANY(%s) AND slot_status <> 'OPEN' LIMIT 1",
        (span.slot_ids,),
    ).fetchone()
    if blocked:
        failures.append("SLOT_BLOCKED")

    # F11 (I7: every slot)
    occupied = con.execute(
        """
        SELECT 1 FROM appointment_slot_allocations al
        JOIN appointments a ON a.appointment_id = al.appointment_id
        WHERE al.slot_id = ANY(%s) AND a.is_current AND a.appointment_status = ANY(%s)
        LIMIT 1
        """,
        (span.slot_ids, list(ACTIVE_APPOINTMENT_STATUSES)),
    ).fetchone()
    if occupied:
        failures.append("SLOT_OCCUPIED")

    # F12 (I7, I8: unexpired only — expiry checked here at read time, not via
    # an index predicate)
    held = con.execute(
        """
        SELECT 1 FROM slot_holds
        WHERE slot_id = ANY(%s) AND hold_status = 'ACTIVE' AND expires_at > %s
          AND shipment_id <> %s
        LIMIT 1
        """,
        (span.slot_ids, clock.now(), ctx.shipment_id),
    ).fetchone()
    if held:
        failures.append("SLOT_HELD")

    # F13
    if ctx.deadline_ts is not None:
        if span.span_end + timedelta(minutes=10) > ctx.deadline_ts:
            failures.append("DRIVER_DEADLINE")

    # F14
    if ctx.eta_confidence == "LOW" and ctx.eta_updated_at is not None:
        if clock.now() - ctx.eta_updated_at > timedelta(minutes=45):
            failures.append("ETA_UNRELIABLE")

    return failures


def _search_window(
    con, ctx: ShipmentContext, clock: Clock, window_start: datetime, window_end: datetime
) -> list[SpanCandidate]:
    docks = get_candidate_docks(con, ctx)
    feasible = []
    for dock in docks:
        slots = get_open_slots(con, dock.dock_id, window_start, window_end)
        for span in build_span_candidates(dock, slots, ctx.expected_unload_min):
            if not evaluate_span(con, ctx, span, clock):
                feasible.append(span)
    feasible.sort(key=lambda s: s.span_start)
    return feasible


def find_feasible_slots_impl(shipment_id: str, earliest_ts: str | None, clock: Clock) -> ToolResult:
    from .. import db

    constants = get_operating_constants()
    with db.get_conn() as con:
        ctx = get_shipment_context(con, shipment_id)
        if earliest_ts is not None:
            ctx = replace(ctx, effective_eta_ts=datetime.fromisoformat(earliest_ts))

        band_start = ctx.effective_eta_ts + timedelta(minutes=constants.eta_buffer_minutes)
        band_end = ctx.effective_eta_ts + timedelta(hours=constants.search_band_hours)

        # 1. the relevance band
        results = _search_window(con, ctx, clock, band_start, band_end)
        if results:
            return Options(
                status="options",
                spans=[
                    _to_feasible_span(con, s, ctx, clock)
                    for s in results[: constants.max_options_returned]
                ],
                searched_until=band_end.isoformat(),
            )

        # 2. widen to end of operating day (civil boundary — IST)
        _, close_time = get_facility_hours(con, ctx.facility_id)
        band_start_ist = band_start.astimezone(IST)
        end_of_day = band_start_ist.replace(
            hour=close_time.hour, minute=close_time.minute, second=0, microsecond=0
        )
        if end_of_day > band_end:
            results = _search_window(con, ctx, clock, band_start, end_of_day)
            if results:
                return Options(
                    status="options",
                    spans=[
                        _to_feasible_span(con, s, ctx, clock)
                        for s in results[: constants.max_options_returned]
                    ],
                    searched_until=end_of_day.isoformat(),
                )

        # 3. next day (civil boundary — IST)
        open_time, close_time = get_facility_hours(con, ctx.facility_id)
        next_day = band_start_ist.date() + timedelta(days=1)
        next_day_start = datetime.combine(next_day, open_time, tzinfo=IST)
        next_day_end = datetime.combine(next_day, close_time, tzinfo=IST)
        results = _search_window(con, ctx, clock, next_day_start, next_day_end)
        if results:
            return Rejected(
                status="rejected",
                reason="NO_SAME_DAY_SLOT",
                detail="No feasible slot remains today; earliest option is tomorrow.",
            )

        # 4. nothing anywhere searched
        return Rejected(
            status="rejected",
            reason="NO_FEASIBLE_SLOT",
            detail="No feasible slot found for this shipment.",
        )


def _to_feasible_span(con, span: SpanCandidate, ctx: ShipmentContext, clock: Clock) -> FeasibleSpan:
    from .tokens import issue_option_token

    issued_at = clock.now().isoformat()
    return FeasibleSpan(
        option_token=issue_option_token(con, span.slot_ids, ctx.shipment_id, issued_at),
        dock_id=span.dock.dock_id,
        dock_code=span.dock.dock_code,
        span_start=span.span_start.isoformat(),
        span_end=span.span_end.isoformat(),
    )
