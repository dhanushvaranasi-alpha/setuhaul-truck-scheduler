import time  # TEMPORARY — diagnostic instrumentation, see agent.py's TimingCallbackHandler
from dataclasses import dataclass, replace
from datetime import datetime
from datetime import time as dt_time
from datetime import timedelta

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
    dock_type: str
    supports_refrigerated: bool
    max_vehicle_weight_kg: int


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
        candidates.append(
            DockCandidate(
                dock_id=dock_id,
                dock_code=dock_code,
                dock_type=dock_type,
                supports_refrigerated=supports_refrigerated,
                max_vehicle_weight_kg=max_weight,
            )
        )
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


@dataclass(frozen=True)
class SpanEvalBatch:
    """Facility- and dock-level data that's identical for every candidate
    span evaluate_span checks within one _search_window call — fetched
    once per dock (facility hours/last-start once per whole search)
    instead of once per candidate. Root cause of the /api/chat latency
    investigation: without this, find_feasible_slots ran 7 queries x every
    candidate (287 sequential round-trips for one real search — see
    RUNBOOK/agent.py timing notes). Only used by _search_window; the two
    single-span re-validation callers (booking.py, sweeps.py) still pass
    batch=None and get the original always-fresh per-call queries, since
    N=1 there and re-validation freshness matters more than round-trip
    count."""

    open_time: dt_time
    close_time: dt_time
    last_start: tuple[int, int] | None
    blocked_windows: list[tuple[datetime, datetime]]
    slot_status: dict[str, str]
    occupied_slot_ids: set[str]
    held_slot_ids: set[str]


def _fetch_blocked_windows(
    con, dock_id: str, window_start: datetime, window_end: datetime
) -> list[tuple[datetime, datetime]]:
    """F9's per-candidate query, batched: every BREAKDOWN/MAINTENANCE event
    overlapping the whole search window for this dock, fetched once. Any
    candidate span is a subset of [window_start, window_end), so this is a
    superset of what a per-candidate query would ever need — the overlap
    check itself still runs per-candidate, just against pre-fetched rows."""
    rows = con.execute(
        """
        SELECT event_start_ts, COALESCE(event_end_ts, 'infinity'::timestamptz)
        FROM dock_status_events
        WHERE dock_id = %s AND event_type = ANY(%s)
          AND event_start_ts < %s
          AND COALESCE(event_end_ts, 'infinity'::timestamptz) > %s
        """,
        (dock_id, list(BLOCKING_EVENT_TYPES), window_end, window_start),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _fetch_slot_status(con, slot_ids: list[str]) -> dict[str, str]:
    """F10's per-candidate query, batched over every open slot for the dock
    in one call."""
    if not slot_ids:
        return {}
    rows = con.execute(
        "SELECT slot_id, slot_status FROM appointment_slots WHERE slot_id = ANY(%s)",
        (slot_ids,),
    ).fetchall()
    return dict(rows)


def _fetch_occupied_slot_ids(con, slot_ids: list[str]) -> set[str]:
    """F11's per-candidate query, batched."""
    if not slot_ids:
        return set()
    rows = con.execute(
        """
        SELECT al.slot_id FROM appointment_slot_allocations al
        JOIN appointments a ON a.appointment_id = al.appointment_id
        WHERE al.slot_id = ANY(%s) AND a.is_current AND a.appointment_status = ANY(%s)
        """,
        (slot_ids, list(ACTIVE_APPOINTMENT_STATUSES)),
    ).fetchall()
    return {r[0] for r in rows}


def _fetch_held_slot_ids(con, slot_ids: list[str], shipment_id: str, clock: Clock) -> set[str]:
    """F12's per-candidate query, batched."""
    if not slot_ids:
        return set()
    rows = con.execute(
        """
        SELECT slot_id FROM slot_holds
        WHERE slot_id = ANY(%s) AND hold_status = 'ACTIVE' AND expires_at > %s
          AND shipment_id <> %s
        """,
        (slot_ids, clock.now(), shipment_id),
    ).fetchall()
    return {r[0] for r in rows}


def evaluate_span(
    con,
    ctx: ShipmentContext,
    span: SpanCandidate,
    clock: Clock,
    exclude_appointment_id: str | None = None,
    batch: SpanEvalBatch | None = None,
) -> list[str]:
    """All 13 predicates against the whole span (I7). Returns failing reason
    codes. Pass exclude_appointment_id when re-validating an *existing*
    appointment before confirming it (I6) — otherwise F11 would flag the
    appointment's own allocation as occupying its own slot. Pass batch
    (SpanEvalBatch) when called from the _search_window hot loop, where
    F2/F3/F9/F10/F11/F12's data has already been fetched once for the
    whole dock/window rather than fresh per call."""
    failures: list[str] = []
    constants = get_operating_constants()

    # F1
    if span.span_start < ctx.effective_eta_ts + timedelta(minutes=constants.eta_buffer_minutes):
        failures.append("ETA_TOO_LATE")

    # F2 — facility hours are civil time in IST; TIMESTAMPTZ values come back
    # from Postgres with UTC tzinfo, so convert before comparing time-of-day.
    span_start_ist = span.span_start.astimezone(IST)
    span_end_ist = span.span_end.astimezone(IST)
    if batch is not None:
        open_time, close_time = batch.open_time, batch.close_time
    else:
        open_time, close_time = get_facility_hours(con, ctx.facility_id)
    if span_start_ist.time() < open_time or span_end_ist.time() > close_time:
        failures.append("OUTSIDE_OPERATING_HOURS")

    # F3
    last_start = batch.last_start if batch is not None else get_last_new_start_time(con, ctx.facility_id)
    if last_start is not None:
        hour, minute = last_start
        if span_start_ist.time() >= span_start_ist.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        ).time():
            failures.append("AFTER_LAST_START_TIME")

    # F4, F6, F7 (redundant with candidate-dock filtering, kept for
    # booking-time revalidation of an already-chosen span). span.dock
    # already carries these — from get_candidate_docks's own query when
    # this came through _search_window, or from a fresh per-call query at
    # the two single-span call sites — so no DB hit needed here either way.
    dock_type = span.dock.dock_type
    supports_refrigerated = span.dock.supports_refrigerated
    max_weight = span.dock.max_vehicle_weight_kg
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
    if batch is not None:
        event = any(
            event_start < span.span_end and event_end > span.span_start
            for event_start, event_end in batch.blocked_windows
        )
    else:
        event = (
            con.execute(
                """
                SELECT 1 FROM dock_status_events
                WHERE dock_id = %s AND event_type = ANY(%s)
                  AND event_start_ts < %s
                  AND COALESCE(event_end_ts, 'infinity'::timestamptz) > %s
                LIMIT 1
                """,
                (span.dock.dock_id, list(BLOCKING_EVENT_TYPES), span.span_end, span.span_start),
            ).fetchone()
            is not None
        )
    if event:
        failures.append("DOCK_UNAVAILABLE")

    # F10 (I7: every slot)
    if batch is not None:
        blocked = any(batch.slot_status.get(sid) != "OPEN" for sid in span.slot_ids)
    else:
        blocked = (
            con.execute(
                "SELECT 1 FROM appointment_slots WHERE slot_id = ANY(%s) AND slot_status <> 'OPEN' LIMIT 1",
                (span.slot_ids,),
            ).fetchone()
            is not None
        )
    if blocked:
        failures.append("SLOT_BLOCKED")

    # F11 (I7: every slot). exclude_appointment_id is never set on the
    # batch path — _search_window only searches for new spans, it never
    # re-validates an existing appointment's own allocation.
    if batch is not None:
        occupied = any(sid in batch.occupied_slot_ids for sid in span.slot_ids)
    else:
        occupied = (
            con.execute(
                """
                SELECT 1 FROM appointment_slot_allocations al
                JOIN appointments a ON a.appointment_id = al.appointment_id
                WHERE al.slot_id = ANY(%s) AND a.is_current AND a.appointment_status = ANY(%s)
                  AND a.appointment_id IS DISTINCT FROM %s
                LIMIT 1
                """,
                (span.slot_ids, list(ACTIVE_APPOINTMENT_STATUSES), exclude_appointment_id),
            ).fetchone()
            is not None
        )
    if occupied:
        failures.append("SLOT_OCCUPIED")

    # F12 (I7, I8: unexpired only — expiry checked here at read time, not via
    # an index predicate)
    if batch is not None:
        held = any(sid in batch.held_slot_ids for sid in span.slot_ids)
    else:
        held = (
            con.execute(
                """
                SELECT 1 FROM slot_holds
                WHERE slot_id = ANY(%s) AND hold_status = 'ACTIVE' AND expires_at > %s
                  AND shipment_id <> %s
                LIMIT 1
                """,
                (span.slot_ids, clock.now(), ctx.shipment_id),
            ).fetchone()
            is not None
        )
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
    t_docks = time.time()
    docks = get_candidate_docks(con, ctx)
    print(f"[timing]   get_candidate_docks: {time.time() - t_docks:.3f}s ({len(docks)} docks)")

    # F2/F3 are facility-level — identical for every dock and every
    # candidate in this call, so fetched once instead of once per candidate.
    open_time, close_time = get_facility_hours(con, ctx.facility_id)
    last_start = get_last_new_start_time(con, ctx.facility_id)

    feasible = []
    candidates_evaluated = 0
    t_eval_total = 0.0
    for dock in docks:
        t_slots = time.time()
        slots = get_open_slots(con, dock.dock_id, window_start, window_end)
        slots_dt = time.time() - t_slots
        candidates = build_span_candidates(dock, slots, ctx.expected_unload_min)
        print(
            f"[timing]   get_open_slots({dock.dock_code}): {slots_dt:.3f}s "
            f"({len(slots)} open slots -> {len(candidates)} span candidates)"
        )
        if not candidates:
            continue

        # F9/F10/F11/F12 are dock+window-level — batched once per dock here
        # instead of once per candidate span (was the N+1 hot path: 7
        # queries x every candidate, ~287 round-trips for one real search).
        t_batch = time.time()
        slot_ids = [s.slot_id for s in slots]
        batch = SpanEvalBatch(
            open_time=open_time,
            close_time=close_time,
            last_start=last_start,
            blocked_windows=_fetch_blocked_windows(con, dock.dock_id, window_start, window_end),
            slot_status=_fetch_slot_status(con, slot_ids),
            occupied_slot_ids=_fetch_occupied_slot_ids(con, slot_ids),
            held_slot_ids=_fetch_held_slot_ids(con, slot_ids, ctx.shipment_id, clock),
        )
        print(f"[timing]   batch fetch ({dock.dock_code}): {time.time() - t_batch:.3f}s")

        for span in candidates:
            t_eval = time.time()
            ok = not evaluate_span(con, ctx, span, clock, batch=batch)
            t_eval_total += time.time() - t_eval
            candidates_evaluated += 1
            if ok:
                feasible.append(span)
    print(
        f"[timing]   evaluate_span total: {t_eval_total:.3f}s over {candidates_evaluated} candidates "
        f"({t_eval_total / candidates_evaluated * 1000:.0f}ms/candidate)"
        if candidates_evaluated
        else "[timing]   evaluate_span: 0 candidates"
    )
    feasible.sort(key=lambda s: s.span_start)
    return feasible


def find_feasible_slots_impl(
    shipment_id: str, earliest_ts: str | None, clock: Clock, trigger_reason: str | None = None
) -> ToolResult:
    from .. import db
    from . import driver_context as dc

    constants = get_operating_constants()
    with db.get_conn() as con:
        # A driver's claim that their dock is broken must be verified against
        # actual dock events, not taken on faith — enforced here in code
        # rather than only in the system prompt, since an LLM applies the
        # "verify before acting" rule inconsistently once other signals
        # (on-site, missed slot) make the claim feel plausible enough to
        # skip checking. The caller must tag the call DOCK_FAULT for this
        # gate to run — the LLM still decides *that* a dock-fault claim was
        # made (unavoidable, since that's derived from the driver's own
        # words), but not whether the claim is true.
        if trigger_reason == "DOCK_FAULT":
            state = dc.get_shipment_state(con, shipment_id, clock)
            if not state.active_dock_events:
                return Rejected(
                    status="rejected",
                    reason="UNVERIFIED_DOCK_FAULT",
                    detail=(
                        "No fault is recorded on your assigned dock. "
                        "Please confirm with the dock supervisor."
                    ),
                )

        ctx = get_shipment_context(con, shipment_id)

        # A driver already on site is searched from now, never from a
        # declared/asked arrival time — enforced here in code rather than
        # only in the system prompt, since an LLM applies a prompt rule
        # inconsistently across queue states; gate_in_ts being set always
        # wins over whatever earliest_ts the caller passed (or omitted).
        gate_in_row = con.execute(
            "SELECT gate_in_ts FROM facility_checkins WHERE shipment_id = %s", (shipment_id,)
        ).fetchone()
        on_site = gate_in_row is not None and gate_in_row[0] is not None

        if on_site:
            ctx = replace(ctx, effective_eta_ts=clock.now())
        elif earliest_ts is not None:
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
                searched_until=band_end.astimezone(IST).isoformat(),
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
                    searched_until=end_of_day.astimezone(IST).isoformat(),
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
        dock_type=span.dock.dock_type,
        # IST, not the UTC tzinfo psycopg attaches to TIMESTAMPTZ columns by
        # default (this is a display concern only — the underlying instant,
        # and therefore every comparison against it, was always correct).
        span_start=span.span_start.astimezone(IST).isoformat(),
        span_end=span.span_end.astimezone(IST).isoformat(),
    )
