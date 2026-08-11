"""
SetuHaul clock.

The dataset is frozen at 2026-08-04T10:00+05:30. The demo is on a different
date. If any code calls datetime.now() directly, then on demo day every slot
is in the past, every ETA is stale, every hold expires on creation and every
pending confirmation is instantly overdue. The system fails completely and
looks like a logic bug.

So: nothing calls datetime.now() except this module.

Enforce with a flake8/ruff rule or a grep in CI:

    ruff:  flake8-datetimez (DTZ005 bans datetime.now() without tz)
    CI:    ! grep -rn "datetime\\.now()\\|time\\.time()" src/ --exclude=clock.py
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

IST = timezone(timedelta(hours=5, minutes=30))
SNAPSHOT_INSTANT = datetime(2026, 8, 4, 10, 0, 0, tzinfo=IST)
FACILITY_TZ = "Asia/Kolkata"


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime:
        """Always timezone-aware. Never naive."""


class SystemClock(Clock):
    """Production: real time."""

    def now(self) -> datetime:
        return datetime.now(IST)


class SimulatedClock(Clock):
    """
    Demo and tests: starts at the dataset snapshot, moves only when told.

    Turns a constraint into a feature - advance on stage to show a hold
    expiring, or jump past RULE002's 30-minute grace to show a no-show fire,
    instead of waiting for it.
    """

    def __init__(self, start: datetime | str = SNAPSHOT_INSTANT):
        self._current = self._parse(start)

    @staticmethod
    def _parse(v: datetime | str) -> datetime:
        dt = datetime.fromisoformat(v) if isinstance(v, str) else v
        if dt.tzinfo is None:
            raise ValueError("clock instants must be timezone-aware")
        return dt

    def now(self) -> datetime:
        return self._current

    def advance_seconds(self, seconds: float) -> datetime:
        self._current += timedelta(seconds=seconds)
        return self._current

    def advance_minutes(self, minutes: float) -> datetime:
        return self.advance_seconds(minutes * 60)

    def set(self, instant: datetime | str) -> datetime:
        self._current = self._parse(instant)
        return self._current

    def reset(self) -> datetime:
        return self.set(SNAPSHOT_INSTANT)


class OffsetClock(Clock):
    """
    Ticking simulated clock: starts at the snapshot, advances in real time.

    Use for the concurrency test, where 20 parallel requests need genuinely
    distinct instants for hold_created_at tie-breaking to mean anything, while
    the dataset must still look like 4 August.
    """

    def __init__(self, start: datetime | str = SNAPSHOT_INSTANT):
        target = SimulatedClock._parse(start)
        self._offset = target - datetime.now(IST)

    def now(self) -> datetime:
        return datetime.now(IST) + self._offset


# --------------------------------------------------------------------------
# Serverless caveat: module-level state does NOT survive between Vercel
# invocations, so a SimulatedClock advanced in one request is back at the
# snapshot in the next. For a multi-step demo, persist the instant in the
# database and rebuild the clock per request.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ClockState:
    mode: Literal["system", "simulated", "offset"]
    simulated_instant: str | None = None
    offset_seconds: float | None = None


def clock_from_state(state: ClockState) -> Clock:
    if state.mode == "system":
        return SystemClock()
    if state.mode == "simulated":
        return SimulatedClock(state.simulated_instant or SNAPSHOT_INSTANT)
    offset = timedelta(seconds=state.offset_seconds or 0)

    class _Offset(Clock):
        def now(self) -> datetime:
            return datetime.now(IST) + offset

    return _Offset()


# --------------------------------------------------------------------------
# Everything time-dependent takes the clock as an argument. No exceptions -
# one forgotten datetime.now() is enough to break demo day.
#
#   is_feasible(shipment, span, clock)          # F1, F14
#   resolve_hold_ttl(ratio, policy, clock)      # writes absolute expires_at
#   sweep_expired_holds(tx, clock)              # section 17.6
#   pending_has_expired(appt, clock)            # section 10.2
#   is_within_freeze_window(appt, clock)        # section 11.2
#   is_no_show(appt, facility_rules, clock)     # RULE002 / RULE006
#   escalation_overdue(esc, clock)              # section 12.1
# --------------------------------------------------------------------------
