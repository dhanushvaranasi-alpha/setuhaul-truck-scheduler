from src.db import get_conn, get_direct_conn


def test_pooled_connection():
    with get_conn() as con:
        assert con.execute("SELECT 1").fetchone()[0] == 1


def test_direct_connection():
    with get_direct_conn() as con:
        assert con.execute("SELECT 1").fetchone()[0] == 1
