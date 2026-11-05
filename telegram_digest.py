"""Deterministic morning digest. Read-only evidence, no inferred sales."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hmac
import json
import os
import tempfile
import threading
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from sqlalchemy import text

from telegram_bot import (
    SARAJEVO,
    TelegramAPIError,
    TelegramConfigurationError,
    _all,
    _money,
    _one,
    _statement_timeout,
    _utc,
    telegram_call,
)


MAX_DIGEST_CHARS = 1200
STALE_CLEAN_DAYS = 14
PRICE_WINDOW_DAYS = 7
SEND_HOUR = 9
SEND_MINUTE_START = 40
TEST_SEND_CONFIRM = "SEND_TEST"
_MONTHS = (
    "jan", "feb", "mar", "apr", "maj", "jun",
    "jul", "aug", "sep", "okt", "nov", "dec",
)

RUN_SQL = """
  SELECT status,started_at,finished_at,search_attempted,detail_attempted,
    details_completed,observations,new_listings,errors
  FROM website_collection_runs
  WHERE finished_at IS NOT NULL
  ORDER BY started_at DESC,id DESC LIMIT 1
"""
CLEAN_SQL = """
  SELECT max(end_time) FROM scraping_logs
  WHERE status='completed' AND coalesce(errors_count,0)=0
"""
SNAPSHOT_SQL = "SELECT max(observed_at) FROM listing_snapshots"
PRICE_SQL = """
  WITH unique_observations AS (
    SELECT DISTINCT ON (listing_id,observed_at)
      listing_id,observed_at,asking_price,price_status
    FROM listing_snapshots
    ORDER BY listing_id,observed_at,id DESC
  ), transitions AS (
    SELECT listing_id,observed_at,asking_price,
      lag(asking_price) OVER (PARTITION BY listing_id ORDER BY observed_at) AS previous_price
    FROM unique_observations
    WHERE price_status='priced' AND asking_price>0
  ), recent_listings AS (
    SELECT DISTINCT listing_id FROM unique_observations
    WHERE observed_at>=:cutoff AND observed_at<=:as_of
  )
  SELECT
    (SELECT count(*) FROM recent_listings) AS sample_size,
    count(*) FILTER (WHERE observed_at>=:cutoff AND observed_at<=:as_of
      AND asking_price<previous_price) AS drops,
    count(*) FILTER (WHERE observed_at>=:cutoff AND observed_at<=:as_of
      AND asking_price>previous_price) AS increases
  FROM transitions
"""
CUTS_SQL = """
  WITH unique_observations AS (
    SELECT DISTINCT ON (listing_id,observed_at)
      listing_id,observed_at,asking_price,price_status
    FROM listing_snapshots
    ORDER BY listing_id,observed_at,id DESC
  ), transitions AS (
    SELECT listing_id,observed_at,asking_price,
      lag(asking_price) OVER (PARTITION BY listing_id ORDER BY observed_at) AS previous_price
    FROM unique_observations
    WHERE price_status='priced' AND asking_price>0
  )
  SELECT t.listing_id,t.asking_price,t.previous_price,t.observed_at,
    coalesce(l.make,c.make) AS make,coalesce(l.model,c.model) AS model,c.year
  FROM transitions t
  LEFT JOIN car_listings c ON c.listing_id=t.listing_id
  LEFT JOIN dealer_vehicle_labels l ON l.listing_id=t.listing_id
  WHERE t.observed_at>=:cutoff AND t.observed_at<=:as_of
    AND t.asking_price<t.previous_price
  ORDER BY (t.previous_price-t.asking_price) DESC,t.observed_at DESC,t.listing_id
  LIMIT 3
"""
SEEN_SQL = """
  SELECT c.listing_id,coalesce(l.make,c.make) AS make,
    coalesce(l.model,c.model) AS model,c.year,
    CASE WHEN c.price_status='priced' AND c.price>0 THEN c.price END AS price,
    c.first_seen_at
  FROM car_listings c
  LEFT JOIN dealer_vehicle_labels l USING(listing_id)
  WHERE c.source='olx.ba'
    AND c.collection_method IN ('olx_api','olx_website')
    AND c.first_seen_at BETWEEN :started AND :finished
  ORDER BY c.first_seen_at DESC,c.listing_id
  LIMIT 3
"""


class MemoryLedger:
    """Process-local claim of one digest per Sarajevo date."""

    def __init__(self):
        self._days = set()
        self._lock = threading.Lock()

    def claim(self, day):
        with self._lock:
            if day in self._days:
                return False
            self._days.add(day)
            return True

    def release(self, day):
        with self._lock:
            self._days.discard(day)


class FileLedger:
    """Single-file claim ledger. Unreadable files fail closed (no second send)."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError, UnicodeError):
            return None
        return data if isinstance(data, dict) else None

    def _write(self, data):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=directory, prefix=".digest-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            os.replace(temporary, self.path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def claim(self, day):
        with self._lock:
            data = self._read()
            if data is None or data.get(day):
                return False
            data[day] = {"claimed_at": datetime.now(timezone.utc).isoformat()}
            self._write(data)
            return True

    def release(self, day):
        with self._lock:
            data = self._read()
            if not data or day not in data:
                return
            data.pop(day, None)
            self._write(data)


_MEMORY_LEDGER = MemoryLedger()


def default_ledger(environ=None):
    values = os.environ if environ is None else environ
    path = (values.get("TELEGRAM_DIGEST_LEDGER") or "").strip()
    if path:
        return FileLedger(path)
    return _MEMORY_LEDGER


def _header(headers, name):
    if not headers:
        return ""
    getter = getattr(headers, "get", None)
    if getter:
        direct = getter(name)
        if direct:
            return str(direct)
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted and value:
            return str(value)
    return ""


def cron_secret(environ):
    values = os.environ if environ is None else environ
    return (values.get("CRON_SECRET") or values.get("TELEGRAM_CRON_SECRET") or "").strip()


def cron_authorized(headers, environ=None):
    secret = cron_secret(environ)
    if len(secret) < 16:
        return False
    authorization = _header(headers, "authorization")
    if authorization.lower().startswith("bearer "):
        candidate = authorization[7:].strip()
    else:
        candidate = _header(headers, "x-digest-secret").strip()
    if not candidate:
        return False
    return hmac.compare_digest(secret, candidate)


@dataclass(frozen=True)
class DigestConfig:
    bot_token: str
    chat_id: int
    timezone: ZoneInfo
    send_enabled: bool
    market_url: str | None = None

    @classmethod
    def from_env(cls, environ=None):
        values = os.environ if environ is None else environ
        token = (values.get("TELEGRAM_BOT_TOKEN") or "").strip()
        chat = (values.get("TELEGRAM_CHAT_ID") or "").strip()
        missing = [name for name, present in (
            ("TELEGRAM_BOT_TOKEN", token),
            ("TELEGRAM_CHAT_ID", chat),
        ) if not present]
        if missing:
            raise TelegramConfigurationError(
                "Missing Telegram digest configuration: " + ", ".join(missing))
        try:
            chat_id = int(chat)
        except ValueError as exc:
            raise TelegramConfigurationError(
                "Telegram chat ID must be an integer") from exc
        zone_name = (values.get("BOT_TIMEZONE") or "Europe/Sarajevo").strip()
        try:
            zone = ZoneInfo(zone_name)
        except Exception as exc:
            raise TelegramConfigurationError(
                "BOT_TIMEZONE must be an IANA timezone") from exc
        market_url = (values.get("TELEGRAM_MARKET_URL") or "").strip() or None
        if market_url:
            parsed = urlparse(market_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise TelegramConfigurationError("Telegram market URL must use HTTPS")
        flag = (values.get("TELEGRAM_DIGEST_SEND") or "").strip().lower()
        return cls(
            bot_token=token,
            chat_id=chat_id,
            timezone=zone,
            send_enabled=flag in {"1", "true", "yes"},
            market_url=market_url,
        )


def _aware(value):
    value = _utc(value)
    return value


def _local_day(now, tz):
    return _aware(now).astimezone(tz).date()


def in_send_window(now, tz):
    local = _aware(now).astimezone(tz)
    return local.hour == SEND_HOUR and local.minute >= SEND_MINUTE_START


def _stamp(value, tz):
    value = _aware(value)
    if value is None:
        return "nema zapisa"
    local = value.astimezone(tz)
    return f"{local.day}. {_MONTHS[local.month - 1]}, {local.strftime('%H:%M')}"


def _label(make, model, year):
    name = " ".join(part for part in (make, model) if part) or "Nepoznat model"
    return f"{name} · {year}" if year else name


def assess(evidence):
    now = _aware(evidence.get("generated_at")) or datetime.now(timezone.utc)
    clean = _aware(evidence.get("clean_at"))
    stale = clean is None or (now - clean) > timedelta(days=STALE_CLEAN_DAYS)
    run = evidence.get("run")
    partial = (
        not run
        or str(run.get("status") or "").lower() != "completed"
        or int(run.get("errors") or 0) > 0
    )
    return {"trends_stale": stale, "partial": partial, "empty": not run}


def _quality(run, flags):
    if not run:
        return "nema prolaza (empty)"
    status = str(run.get("status") or "").lower()
    if status == "completed" and not flags["partial"]:
        return "čisto (clean)"
    if status == "failed":
        return "neuspjelo (failed)"
    return "djelimično (partial)"


def _assemble(parts, limit=MAX_DIGEST_CHARS):
    """Join (text, optional) rows. Drop optional rows from the bottom until it fits."""

    def joined(items):
        return "\n".join(text for text, _optional in items).strip()

    chosen = list(parts)
    while len(joined(chosen)) > limit and any(optional for _text, optional in chosen):
        for index in range(len(chosen) - 1, -1, -1):
            if chosen[index][1]:
                del chosen[index]
                break
    text = joined(chosen)
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _row(text, optional=False):
    return (text, optional)


def render_digest(evidence, tz=SARAJEVO):
    """Compact Bosnian digest with short English labels. Never infers a sale."""
    flags = assess(evidence)
    now = _aware(evidence.get("generated_at")) or datetime.now(timezone.utc)
    local = now.astimezone(tz)
    run = evidence.get("run")
    parts = [
        _row(f"Garaža · {local.day}. {_MONTHS[local.month - 1]}, {local.strftime('%H:%M')}"),
        _row(f"Status: {_quality(run, flags)}"),
        _row("Pregled se šalje i kad je stanje djelimično ili zastarjelo."),
    ]
    if not run:
        parts.append(_row("Nema završenog prolaza kolektora (empty)."))
    else:
        parts.append(_row(
            f"Pokušaj: {_stamp(run.get('finished_at') or run.get('started_at'), tz)}"))
        parts.append(_row(
            f"{int(run.get('observations') or 0)} zapažanja · "
            f"{int(run.get('new_listings') or 0)} prvi put viđeno kod nas (first seen) · "
            f"{int(run.get('errors') or 0)} grešaka"
        ))
        parts.append(_row(
            f"Posjete: {int(run.get('search_attempted') or 0)} pretrage · "
            f"{int(run.get('detail_attempted') or 0)} detalja "
            f"({int(run.get('details_completed') or 0)} završeno)"
        ))
    parts.append(_row(
        f"Svježina: zapažanje do {_stamp(evidence.get('latest_snapshot'), tz)} · "
        f"zadnji čist prolaz {_stamp(evidence.get('clean_at'), tz)}"
    ))
    sample = int(evidence.get("price_sample") or 0)
    drops = int(evidence.get("price_drops") or 0)
    increases = int(evidence.get("price_increases") or 0)
    parts.extend([
        _row(""),
        _row(f"Padovi cijene (price cuts) · zadnjih {PRICE_WINDOW_DAYS} dana"),
    ])
    if flags["trends_stale"]:
        parts.append(_row(
            "Trendovi potisnuti (stale clean anchor): čist prolaz je star "
            f"ili ga nema ({_stamp(evidence.get('clean_at'), tz)}). "
            "Broj je označen, nije tržišni indeks."
        ))
    else:
        parts.append(_row(
            f"Uzorak: {sample} oglasa · {drops} padova · {increases} rastova tražene cijene. "
            "Prozor i uzorak su u ovom redu. Ovo nije tržišni indeks."
        ))
    parts.append(_row(
        f"Zabilježeni padovi tražene cijene u uzorku od {sample} oglasa: {drops}."
    ))
    for item in evidence.get("notable_cuts") or []:
        parts.append(_row(
            f"• {_label(item.get('make'), item.get('model'), item.get('year'))} · "
            f"{_money(item.get('asking_price'))} ← {_money(item.get('previous_price'))}\n"
            f"  zapaženo {_stamp(item.get('observed_at'), tz)} · "
            f"https://olx.ba/artikal/{item.get('listing_id')}",
            optional=True,
        ))
    seen_count = int((run or {}).get("new_listings") or 0)
    parts.extend([_row(""), _row("Prvi put viđeno kod nas (first seen)")])
    if not run:
        parts.append(_row("Nema pokušaja iz kojeg bi se brojali novi ID-jevi (empty)."))
    else:
        parts.append(_row(
            f"{seen_count} ID-jeva u ovom pokušaju. To je prvo viđenje u našoj bazi, "
            "ne provjeren datum objave."
        ))
        if seen_count and not (evidence.get("first_seen") or []):
            parts.append(_row(
                "Nijedan od tih ID-jeva nije povezan s redom u ovom prozoru."))
        elif not seen_count and not (evidence.get("first_seen") or []):
            parts.append(_row("Nema ID-jeva prvi put viđenih u ovom pokušaju."))
    for item in evidence.get("first_seen") or []:
        parts.append(_row(
            f"• {_label(item.get('make'), item.get('model'), item.get('year'))} · "
            f"{_money(item.get('price'))}\n"
            f"  prvo viđeno {_stamp(item.get('first_seen_at'), tz)} · "
            f"https://olx.ba/artikal/{item.get('listing_id')}",
            optional=True,
        ))
    parts.extend([
        _row(""),
        _row(
            "Nestanak iz pretrage nije prodaja. Tražene cijene nisu prodajne cijene. "
            "Prvo viđeno kod nas nije provjeren datum objave."
        ),
    ])
    return _assemble(parts)


def load_digest_evidence(engine, now):
    as_of = _aware(now)
    cutoff = as_of - timedelta(days=PRICE_WINDOW_DAYS)
    with engine.begin() as connection:
        _statement_timeout(connection)
        run = _one(connection, RUN_SQL)
        clean = connection.execute(text(CLEAN_SQL)).scalar()
        latest = connection.execute(text(SNAPSHOT_SQL)).scalar()
        prices = _one(connection, PRICE_SQL, {"cutoff": cutoff, "as_of": as_of}) or {}
        cuts = _all(connection, CUTS_SQL, {"cutoff": cutoff, "as_of": as_of})
        seen = []
        if run and run.get("started_at") and run.get("finished_at"):
            seen = _all(connection, SEEN_SQL, {
                "started": run["started_at"],
                "finished": run["finished_at"],
            })
    return {
        "generated_at": as_of,
        "run": run,
        "clean_at": clean,
        "latest_snapshot": latest,
        "price_sample": int(prices.get("sample_size") or 0),
        "price_drops": int(prices.get("drops") or 0),
        "price_increases": int(prices.get("increases") or 0),
        "notable_cuts": cuts,
        "first_seen": seen,
    }


def digest_keyboard(market_url):
    if not market_url:
        return None
    parsed = urlparse(market_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return {"inline_keyboard": [[
        {"text": "Market", "url": f"{origin}/market"},
        {"text": "Compare", "url": f"{origin}/compare"},
        {"text": "Following", "url": f"{origin}/following"},
    ]]}


def plan_delivery(*, mode, now, tz, send_enabled, confirmed, ledger):
    day = _local_day(now, tz).isoformat()
    chosen = (mode or "dry").strip().lower()
    if chosen == "dry":
        return {"action": "render", "reason": "dry_render", "day": day}
    if chosen == "test-send":
        if not confirmed:
            return {"action": "render", "reason": "test_send_unconfirmed", "day": day}
        if not ledger.claim(day):
            return {"action": "skip", "reason": "duplicate_send", "day": day}
        return {"action": "send", "reason": "test_send", "day": day}
    if chosen == "send":
        if not send_enabled:
            return {"action": "render", "reason": "send_disabled", "day": day}
        if not in_send_window(now, tz):
            return {"action": "skip", "reason": "outside_window", "day": day}
        if not ledger.claim(day):
            return {"action": "skip", "reason": "duplicate_send", "day": day}
        return {"action": "send", "reason": "scheduled", "day": day}
    return {"action": "render", "reason": "unknown_mode", "day": day}


def handle_digest(engine, *, headers, mode, environ=None, now=None, ledger=None,
                  call=telegram_call):
    """Dry-render by default. Send only through the scheduled or test-send gate."""
    if not cron_authorized(headers, environ):
        return 401, {"ok": False, "reason": "unauthorized"}
    try:
        config = DigestConfig.from_env(environ)
    except TelegramConfigurationError as exc:
        return 503, {"ok": False, "reason": "not_configured", "detail": str(exc)}
    now = now or datetime.now(timezone.utc)
    evidence = load_digest_evidence(engine, now)
    flags = assess(evidence)
    text = render_digest(evidence, config.timezone)
    ledger = default_ledger(environ) if ledger is None else ledger
    confirmed = _header(headers, "x-digest-confirm").strip() == TEST_SEND_CONFIRM
    plan = plan_delivery(
        mode=mode or "dry",
        now=now,
        tz=config.timezone,
        send_enabled=config.send_enabled,
        confirmed=confirmed,
        ledger=ledger,
    )
    body = {
        "ok": True,
        "action": plan["action"],
        "reason": plan["reason"],
        "day": plan["day"],
        "chars": len(text),
        "stale": flags["trends_stale"],
        "partial": flags["partial"],
        "empty": flags["empty"],
        "text": text,
    }
    if plan["action"] != "send":
        return 200, body
    payload = {
        "chat_id": config.chat_id,
        "text": text[:MAX_DIGEST_CHARS],
        "disable_web_page_preview": True,
    }
    markup = digest_keyboard(config.market_url)
    if markup:
        payload["reply_markup"] = markup
    try:
        result = call(config, "sendMessage", payload)
    except TelegramAPIError:
        ledger.release(plan["day"])
        return 502, {"ok": False, "reason": "telegram_send_failed"}
    except Exception:
        ledger.release(plan["day"])
        raise
    if isinstance(result, dict):
        body["message_id"] = result.get("message_id")
    return 200, body
