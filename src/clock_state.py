from clock import Clock, ClockState, clock_from_state


def get_clock(con) -> Clock:
    row = con.execute(
        "SELECT mode, simulated_instant, offset_seconds FROM clock_state WHERE singleton"
    ).fetchone()
    mode, simulated_instant, offset_seconds = row
    state = ClockState(
        mode=mode,
        simulated_instant=simulated_instant.isoformat() if simulated_instant else None,
        offset_seconds=offset_seconds,
    )
    return clock_from_state(state)
