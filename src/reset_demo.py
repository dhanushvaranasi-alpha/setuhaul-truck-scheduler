import os
import subprocess

import psycopg

DIRECT_URL = os.environ["DATABASE_URL"]  # direct (not pooled) for scripts
SQL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MUTABLE_TABLES = [
    "pending_warehouse_replies",
    "escalations",
    "agent_actions",
    "allocation_decisions",
    "slot_holds",
    "driver_constraints",
    "appointment_slot_allocations",
    "facility_checkins",
    "operational_messages",
    "driver_exceptions",
    "chat_messages",
    "chat_threads",
    "appointments",
    "appointment_slots",
    "eta_updates",
]


def reset_demo() -> None:
    if os.environ.get("ALLOW_RESET") != "true":
        raise PermissionError("reset_demo() is disabled — set ALLOW_RESET=true")

    with psycopg.connect(DIRECT_URL) as con:
        for table in MUTABLE_TABLES:
            con.execute(f"DELETE FROM {table}")
        con.execute("""
            UPDATE clock_state
            SET mode = 'simulated',
                simulated_instant = '2026-08-04T10:00:00+05:30',
                offset_seconds = NULL
        """)
        con.commit()

    for fname in ["seed_data.sql", "migration.sql"]:
        path = os.path.join(SQL_DIR, fname)
        result = subprocess.run(
            ["psql", DIRECT_URL, "-f", path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"{fname} failed:\n{result.stderr}")

    print("reset_demo: done")

    with psycopg.connect(DIRECT_URL) as con:
        slots = con.execute("SELECT count(*) FROM appointment_slots").fetchone()[0]
        spans = con.execute(
            "SELECT count(*) FROM appointments WHERE span_start_ts IS NOT NULL"
        ).fetchone()[0]
        allocs = con.execute("SELECT count(*) FROM appointment_slot_allocations").fetchone()[0]
        assert slots == 1656, f"expected 1656 slots, got {slots}"
        assert spans == 20, f"expected 20 spans, got {spans}"
        assert allocs == 85, f"expected 85 allocations, got {allocs}"
        print(f"reset_demo: verified ✓  slots={slots} spans={spans} allocs={allocs}")
