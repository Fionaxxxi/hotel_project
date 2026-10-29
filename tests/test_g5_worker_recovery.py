"""A transient pool outage must not silently remove a distribution worker."""

import pytest
from psycopg_pool import PoolTimeout

from services.core.g3 import worker


def test_distribution_worker_survives_pool_timeout(monkeypatch):
    class Finished(BaseException):
        pass

    class Pool:
        closed = False

        def open(self):
            pass

        def close(self):
            self.closed = True

    class DB:
        pool = Pool()

    database = DB()
    calls = []
    monkeypatch.setattr(worker, 'Database', lambda *a, **k: database)
    monkeypatch.setattr('services.core.g5.worker.periodic_heartbeat', lambda *a: None)
    monkeypatch.setattr('services.core.g5.logging.configure', lambda *a: None)
    monkeypatch.setattr(worker.time, 'sleep', lambda *a: None)

    def tick(db, owner):
        calls.append((db, owner))
        if len(calls) == 1:
            raise PoolTimeout('controlled transient outage')
        raise Finished()

    monkeypatch.setattr(worker, 'tick', tick)
    with pytest.raises(Finished):
        worker.main()
    assert len(calls) == 2 and calls[0] == calls[1]
    assert database.pool.closed
