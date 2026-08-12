import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api._cron_common import make_cron_handler  # noqa: E402
from src.core.sweeps import deliver_warehouse_replies  # noqa: E402

handler = make_cron_handler(deliver_warehouse_replies)
