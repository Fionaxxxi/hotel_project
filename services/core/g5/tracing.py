import functools
import json
import logging
import time
from contextvars import ContextVar

from services.core.g1.common import REQUEST_TRACE

CAUSE_EVENT = ContextVar("cause_event", default=None)


def traced_job(fn):
    @functools.wraps(fn)
    def wrapped(db, job, *args, **kwargs):
        token = REQUEST_TRACE.set(job.get("trace_id"))
        cause = CAUSE_EVENT.set(job.get("cause_event_id"))
        started = time.monotonic()
        try:
            return fn(db, job, *args, **kwargs)
        finally:
            logging.getLogger("hotel.job").info(
                json.dumps(
                    {
                        "event": "job.attempt",
                        "tenant_id": str(job["tenant_id"]),
                        "job_id": str(job["id"]),
                        "resource_id": str(job["resource_id"]),
                        "correlation_id": str(job.get("trace_id")) if job.get("trace_id") else None,
                        "cause_event_id": str(job.get("cause_event_id"))
                        if job.get("cause_event_id")
                        else None,
                        "duration_seconds": time.monotonic() - started,
                    }
                )
            )
            REQUEST_TRACE.reset(token)
            CAUSE_EVENT.reset(cause)

    return wrapped
