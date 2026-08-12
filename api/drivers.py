import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api._http_common import json_get_handler  # noqa: E402
from src import db  # noqa: E402


def _list_drivers(_query: dict) -> dict:
    with db.get_conn() as con:
        rows = con.execute(
            "SELECT driver_id, driver_name, home_base_city FROM drivers WHERE driver_status = 'ACTIVE' ORDER BY driver_id"
        ).fetchall()
    return {
        "drivers": [
            {"driver_id": r[0], "driver_name": r[1], "home_base_city": r[2]} for r in rows
        ]
    }


handler = json_get_handler(_list_drivers)
