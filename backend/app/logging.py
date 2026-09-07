"""Allowlisted application logs: readable locally, JSON in production."""

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from app.middleware.correlation import correlation_id


class SafeFormatter(logging.Formatter):
    def __init__(self, *, production: bool) -> None:
        super().__init__()
        self.production = production

    def format(self, record: logging.LogRecord) -> str:
        event = (
            record.msg
            if record.msg in {"request_failed", "maintenance_iteration_failed"}
            else "application_event"
        )
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "event": str(event),
        }
        for name in ("correlation_id", "device_id", "command_id", "conversation_id"):
            value = getattr(record, name, correlation_id.get() if name == "correlation_id" else "")
            try:
                payload[name] = str(UUID(str(value)))
            except ValueError:
                continue
        operation = getattr(record, "operation", "")
        if operation in {"presence_offline", "command_expiration", "http_request"}:
            payload["operation"] = operation
        if self.production:
            return json.dumps(payload, ensure_ascii=False)
        return " ".join(f"{key}={value}" for key, value in payload.items())


def configure_logging(*, production: bool) -> None:
    logger = logging.getLogger("portero")
    logger.setLevel(logging.INFO)
    for handler in logger.handlers:
        if handler.name == "portero_safe":
            handler.setFormatter(SafeFormatter(production=production))
            return
    handler = logging.StreamHandler()
    handler.name = "portero_safe"
    handler.setFormatter(SafeFormatter(production=production))
    logger.addHandler(handler)
