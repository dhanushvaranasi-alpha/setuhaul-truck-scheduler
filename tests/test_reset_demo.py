import psycopg

from src.reset_demo import DIRECT_URL, reset_demo


def test_reset_demo_idempotent():
    reset_demo()
    reset_demo()

    with psycopg.connect(DIRECT_URL) as con:
        slots = con.execute("SELECT count(*) FROM appointment_slots").fetchone()[0]
        spans = con.execute(
            "SELECT count(*) FROM appointments WHERE span_start_ts IS NOT NULL"
        ).fetchone()[0]
        allocs = con.execute(
            "SELECT count(*) FROM appointment_slot_allocations"
        ).fetchone()[0]
        assert slots == 1656, f"expected 1656 slots, got {slots}"
        assert spans == 20, f"expected 20 spans, got {spans}"
        assert allocs == 85, f"expected 85 allocations, got {allocs}"
        print("reset_demo: verified")
