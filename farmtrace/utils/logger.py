"""Application logging with automatic redaction of credentials in connection strings."""

from __future__ import annotations

import logging
import re
import sys

_CREDENTIAL_URL = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<user>[^:/@\s]+):(?P<password>[^@\s]+)@")
_PASSWORD_PARAM = re.compile(r"(?i)(password=)([^\s&;]+)")
_CONFIGURED = False


def redact(text: str) -> str:
    """Remove passwords from any connection string or ``password=`` parameter in ``text``."""
    text = _CREDENTIAL_URL.sub(lambda m: f"{m.group('scheme')}{m.group('user')}:***@", text)
    return _PASSWORD_PARAM.sub(r"\1***", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed log call
            return True
        record.msg = redact(message)
        record.args = None
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    root = logging.getLogger("farmtrace")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    if not name.startswith("farmtrace"):
        name = f"farmtrace.{name}"
    return logging.getLogger(name)
