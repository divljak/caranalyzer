"""Best-effort log of bot interactions, for improving answers (docs/proposed-migrations/bot_interactions.sql).

What is stored per message or button tap: time, chat type, a salted hash of the Telegram user id (never
a name or username), the message with e-mail addresses, phone numbers and @handles redacted, the parsed
intent, whether the free parser or Gemini read it, the filters, how many results there were and how many
were shown, whether Gemini phrased the reply (or why it fell back), the button, latency and an error class.

Logging can never break or slow a reply:
* it runs after the reply has been delivered, in a daemon thread that the caller waits on for at most
  ``WAIT_SECONDS``, with a short statement timeout;
* every exception is swallowed (only a class name is logged, never text);
* if the table is missing or the role may not insert, logging switches itself off for an hour.
"""

from dataclasses import asdict, is_dataclass
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError


LOG = logging.getLogger("telegram_log")
WAIT_SECONDS = 1.0
STATEMENT_TIMEOUT = "1500ms"
DISABLE_SECONDS = 3600
MAX_TEXT = 400
_disabled_until = [0.0]

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_HANDLE = re.compile(r"(?<![\w@])@\w{2,}")
# A phone number: an optional +, then at least 8 digits with spaces, dots, dashes, slashes or brackets.
_PHONE = re.compile(r"(?<![\w.,])(?:\+|00)?\d(?:[\s./()-]*\d){7,}(?![\w])")
_MONEY_OR_KM = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$|^\d{1,7}$")


def redact(value, bot_username=None):
    """Remove e-mail addresses, phone numbers and @handles (except the bot's own) from a message."""
    value = (value or "")[:MAX_TEXT]
    value = _EMAIL.sub("[email]", value)

    def phone(match):
        raw = match.group(0)
        # prices and mileages ("15.000", "1.250.000", "150000") are not phone numbers
        if _MONEY_OR_KM.match(raw.strip()):
            return raw
        return "[phone]"

    value = _PHONE.sub(phone, value)

    def handle(match):
        name = match.group(0)[1:]
        return match.group(0) if bot_username and name.lower() == bot_username.lower() else "@user"

    return _HANDLE.sub(handle, value)


def user_hash(user_id, secret):
    """Salted, truncated hash of a Telegram user id. Same person → same hash; not reversible without the salt."""
    if not isinstance(user_id, int) or not secret:
        return None
    key = b"bot-interactions:" + str(secret).encode()
    return hmac.new(key, str(user_id).encode(), hashlib.sha256).hexdigest()[:24]


def salt(config, environ=None):
    """TELEGRAM_LOG_SALT if set, else derived from the webhook secret (already a private value)."""
    values = os.environ if environ is None else environ
    return (values.get("TELEGRAM_LOG_SALT") or "").strip() or getattr(config, "webhook_secret", "")


def _filters_json(filters):
    if filters is None or not is_dataclass(filters):
        return None
    data = {k: v for k, v in asdict(filters).items() if v not in (None, "", False)}
    return json.dumps(data, ensure_ascii=False) if data else None


def build_record(*, chat_kind, user_id, secret, message_text=None, button=None, intent=None, path=None,
                 result_count=None, shown_count=None, phrasing=None, phrasing_reason=None, latency_ms=None,
                 error=None, bot_username=None):
    return {
        "chat_type": chat_kind if chat_kind in {"private", "group"} else None,
        "user_hash": user_hash(user_id, secret),
        "message_text": redact(message_text, bot_username) if message_text else None,
        "button": (button or "")[:64] or None,
        "intent": (getattr(intent, "kind", None) or None),
        "path": path,
        "filters": _filters_json(getattr(intent, "filters", None)),
        "result_count": result_count if isinstance(result_count, int) else None,
        "shown_count": shown_count if isinstance(shown_count, int) else None,
        "phrasing": phrasing,
        "phrasing_reason": (phrasing_reason or "")[:80] or None,
        "latency_ms": int(latency_ms) if isinstance(latency_ms, (int, float)) else None,
        "error": (error or "")[:80] or None,
    }


INSERT_SQL = """
  INSERT INTO bot_interactions(chat_type,user_hash,message_text,button,intent,path,filters,result_count,
    shown_count,phrasing,phrasing_reason,latency_ms,error)
  VALUES (:chat_type,:user_hash,:message_text,:button,:intent,:path,CAST(:filters AS jsonb),:result_count,
    :shown_count,:phrasing,:phrasing_reason,:latency_ms,:error)
"""


def _code(error):
    orig = getattr(error, "orig", None)
    return getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)


def _write(engine, record):
    try:
        with engine.begin() as connection:
            connection.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
            connection.execute(text(INSERT_SQL), record)
    except DBAPIError as error:
        if _code(error) in {"42P01", "42501"}:  # table not created yet / role may not insert
            _disabled_until[0] = time.monotonic() + DISABLE_SECONDS
            return
        LOG.warning("bot interaction not logged: %s", type(error).__name__)
    except Exception as error:  # never let logging surface anywhere
        LOG.warning("bot interaction not logged: %s", type(error).__name__)


def enabled(environ=None):
    values = os.environ if environ is None else environ
    if (values.get("TELEGRAM_INTERACTION_LOG") or "").strip().lower() in {"0", "off", "false", "no"}:
        return False
    return time.monotonic() >= _disabled_until[0]


def record(engine, entry, *, wait=WAIT_SECONDS):
    """Write one interaction without ever raising; waits at most ``wait`` seconds. Returns the thread."""
    if not isinstance(engine, Engine) or not enabled():
        return None
    try:
        worker = threading.Thread(target=_write, args=(engine, entry), name="bot-interaction-log", daemon=True)
        worker.start()
        worker.join(wait)
        return worker
    except Exception as error:
        LOG.warning("bot interaction not logged: %s", type(error).__name__)
        return None
