"""Bounded JSON logs with UTC stamps, rotation and no exception traceback payloads."""

import json
import logging
import os
import re
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from services.core.g5.security import redact


class Formatter(logging.Formatter):
    def format(self, record):
        message = record.getMessage()
        try:
            data = json.loads(message)
            if not isinstance(data, dict):
                data = {"event": "log", "message": message[:1000]}
        except ValueError:
            data = {"event": "log", "message": message[:1000]}
        data.update(
            timestamp=datetime.now(UTC).isoformat(),
            level=record.levelname,
            service=getattr(self, "service", "unknown"),
        )
        return json.dumps(redact(data), ensure_ascii=False, default=str)


def configure(service):
    from services.core.g5.offline import install

    install()
    logger = logging.getLogger("hotel")
    if getattr(logger, "_g5_configured", False):
        return
    logger.setLevel(logging.INFO)
    formatter = Formatter()
    formatter.service = service
    if os.environ.get("HOTEL_G1_TEST_MODE") == "true":
        handler = logging.StreamHandler()
    else:
        root = Path(__file__).resolve().parents[3] / ".runtime/logs"
        root.mkdir(exist_ok=True)
        slot = re.sub(r"[^a-zA-Z0-9_-]", "_", os.environ.get("HOTEL_WORKER_SLOT", service))[:80]
        handler = RotatingFileHandler(
            root / (slot + ".jsonl"), maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger._g5_configured = True
