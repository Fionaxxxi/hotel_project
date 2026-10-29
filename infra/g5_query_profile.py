"""Opt-in pytest call profiler; writes private timing data, never arguments."""

import cProfile
import json
import time
from collections import defaultdict
from pathlib import Path

import pytest
from psycopg import Connection


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    profile = cProfile.Profile()
    timings = defaultdict(lambda: {'calls':0,'seconds':0,'max_seconds':0})
    execute = Connection.execute

    def measured(connection, query, *args, **kwargs):
        start = time.perf_counter()
        try:
            return execute(connection, query, *args, **kwargs)
        finally:
            duration = time.perf_counter()-start
            # SQL templates only: parameters/returned records are never retained.
            key = str(query)
            entry = timings[key]
            entry['calls'] += 1
            entry['seconds'] += duration
            entry['max_seconds'] = max(entry['max_seconds'],duration)

    Connection.execute = measured
    profile.enable()
    try:
        yield
    finally:
        profile.disable()
        Connection.execute = execute
        destination = Path(__file__).resolve().parents[1] / '.runtime/g5-reopen-profile.pstats'
        profile.dump_stats(str(destination))
        destination.with_suffix('.json').write_text(json.dumps(sorted([{'sql':key,**value} for key,value in timings.items()],key=lambda x:x['seconds'],reverse=True),indent=2)+'\n',encoding='utf-8')
