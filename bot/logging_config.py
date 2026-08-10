from __future__ import annotations

import logging
import re


_TELEGRAM_TOKEN = re.compile(r"(api\.telegram\.org/bot)[^/\s]+", re.IGNORECASE)
_AUTHORIZATION = re.compile(r"(Authorization\s*[:=]\s*)(?:Bearer\s+)?[^,\s]+", re.IGNORECASE)


class SecretRedactionFilter(logging.Filter):
    """Last-resort protection for secrets accidentally included in log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        rendered = _TELEGRAM_TOKEN.sub(r"\1<redacted>", rendered)
        rendered = _AUTHORIZATION.sub(r"\1<redacted>", rendered)
        record.msg, record.args = rendered, ()
        return True


def configure_logging() -> None:
    logging.basicConfig(format="%(asctime)s | %(name)s | %(levelname)s | %(message)s", level=logging.INFO)
    # httpx's INFO request line contains the Telegram token as part of the URL.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    for handler in logging.getLogger().handlers:
        if not any(isinstance(item, SecretRedactionFilter) for item in handler.filters):
            handler.addFilter(SecretRedactionFilter())
