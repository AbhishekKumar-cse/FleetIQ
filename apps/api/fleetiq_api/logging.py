"""Bounded JSON logs with explicit identifier fields and secret redaction."""

import json
import logging
import re
from datetime import UTC, datetime

SENSITIVE = re.compile(r"password|secret|token|authorization|private.?key|database.?url", re.I)


class JsonFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...] = ()):
        super().__init__()
        self.secrets = tuple(sorted((s for s in secrets if s), key=len, reverse=True))

    def redact(self, value):
        if isinstance(value, dict):
            return {
                str(k): "[REDACTED]" if SENSITIVE.search(str(k)) else self.redact(v)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.redact(v) for v in value]
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, "[REDACTED]")
            value = re.sub(
                r"-----BEGIN (?:RSA )?PRIVATE KEY-----.*?-----END (?:RSA )?PRIVATE KEY-----",
                "[REDACTED]",
                value,
                flags=re.S,
            )
            value = re.sub(r"(://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", value)
            value = re.sub(
                r"(?i)(password|token|secret|authorization)\s*[=:]\s*\S+", r"\1=[REDACTED]", value
            )
            return value[:4096]
        return value

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in ("request_id", "job_id", "context"):
            if hasattr(record, name):
                payload[name] = getattr(record, name)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(self.redact(payload), default=str)


def configure_logging(secrets: tuple[str, ...] = ()) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(secrets))
    logger = logging.getLogger("fleetiq")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
