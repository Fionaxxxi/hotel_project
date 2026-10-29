from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


class Database:
    def __init__(self, url: str, max_size=2):
        from urllib.parse import urlparse

        from services.core.g5.recovery import permit

        self.restored = not permit(urlparse(url).path.removeprefix("/"))
        self.pool = ConnectionPool(
            url,
            min_size=0,
            max_size=max_size,
            timeout=3,
            open=False,
            kwargs={"row_factory": dict_row, "connect_timeout": 3},
        )

    @contextmanager
    def transaction(self, tenant_id=None, subject=None, repeatable=False):
        with self.pool.connection() as conn:
            with conn.transaction():
                if repeatable:
                    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                if self.restored:
                    conn.execute("SET TRANSACTION READ ONLY")
                conn.execute(
                    "SELECT set_config('app.tenant_id', %s, true)",
                    (str(tenant_id) if tenant_id else "",),
                )
                conn.execute("SELECT set_config('app.subject', %s, true)", (subject or "",))
                yield conn
