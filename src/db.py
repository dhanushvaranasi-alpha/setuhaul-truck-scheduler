import os

import psycopg
from psycopg_pool import ConnectionPool

# Pooled — for all app queries on Vercel
pool = ConnectionPool(
    os.environ["POOLED_DATABASE_URL"], min_size=1, max_size=10, open=True
)


def get_conn():
    """Use this in every request handler."""
    return pool.connection()


def get_direct_conn():
    """Use this only in scripts and reset_demo()."""
    return psycopg.connect(os.environ["DATABASE_URL"])
