"""Deterministic morning digest in Telegram HTML. Read-only evidence, no inferred sales."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hmac
import json
import os
import tempfile
import threading
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

import logging

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from database.run_outcome import classify
from telegram_i18n import plural_bs, t
from telegram_bot import (
    Html,
    SARAJEVO,
    esc,
    message_payload,
    TelegramAPIError,
    TelegramConfigurationError,
    _all,
    _money,
    _one,
    _statement_timeout,
    _utc,
    telegram_call,
)


MAX_DIGEST_CHARS = 4000  # Telegram allows 4096; HTML tags count here, so this is conservative
DIGEST_CUTS = 5
STALE_CLEAN_DAYS = 14
PRICE_WINDOW_DAYS = 7
SEND_HOUR = 9
SEND_MINUTE_START = 40
TEST_SEND_CONFIRM = "SEND_TEST"
_WEEKDAYS = ("ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja")
_MONTHS = (
    "jan", "feb", "mar", "apr", "maj", "jun",
    "jul", "aug", "sep", "okt", "nov", "dec",
)

RUN_SQL = """
  SELECT status,started_at,finished_at,search_attempted,detail_attempted,
    details_completed,observations,new_listings,errors,report
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
  LIMIT CAST(:limit AS integer)
"""
# "Najzanimljivije novo": listings first seen in the latest run that are priced at least 15% under
# the median of the same model and year (at least 4 similar ads seen in 90 days), with mileage at or
# under that cohort's median. Grounded in our data only; recent cars with absurd mileage are skipped.
SEEN_SQL = """
  WITH recent AS (
    SELECT c.listing_id,coalesce(l.make,c.make) AS make,coalesce(l.model,c.model) AS model,
      trim(concat_ws(' ',coalesce(l.make,c.make),coalesce(l.model,c.model))) AS label,
      c.year,c.price,c.mileage,c.first_seen_at
    FROM car_listings c
    LEFT JOIN dealer_vehicle_labels l USING(listing_id)
    WHERE c.source='olx.ba'
      AND c.collection_method IN ('olx_api','olx_website')
      AND c.price_status='priced' AND c.price>0
      AND c.year IS NOT NULL AND c.mileage IS NOT NULL
      AND c.last_verified_at >= (now() AT TIME ZONE 'UTC') - interval '90 days'
  ), cohort AS (
    SELECT label,year,count(*) AS n,
      percentile_cont(.5) WITHIN GROUP (ORDER BY price) AS med_price,
      percentile_cont(.5) WITHIN GROUP (ORDER BY mileage) AS med_km
    FROM recent WHERE label<>'' GROUP BY label,year HAVING count(*)>=4
  )
  SELECT r.listing_id,r.make,r.model,r.year,r.price,r.mileage,r.first_seen_at,k.n,k.med_price,
    round((1-CAST(r.price AS numeric)/CAST(k.med_price AS numeric))*100) AS discount_pct
  FROM recent r JOIN cohort k ON k.label=r.label AND k.year=r.year
  WHERE r.first_seen_at BETWEEN :started AND :finished
    AND r.price<=0.85*k.med_price AND r.mileage<=k.med_km
    AND NOT (r.year >= CAST(extract(year FROM (now() AT TIME ZONE 'UTC')) AS integer) - 5
             AND r.mileage > 80000*greatest(CAST(extract(year FROM (now() AT TIME ZONE 'UTC')) AS integer)-r.year,1))
  ORDER BY discount_pct DESC,r.first_seen_at DESC,r.listing_id
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


LOG = logging.getLogger("telegram_digest")
_MISSING_TABLE = "42P01"
_NO_PERMISSION = "42501"


def _unavailable(error):
    """True when the ledger table is missing or the role may not use it (not yet applied)."""
    code = getattr(getattr(error, "orig", None), "pgcode", None) or getattr(getattr(error, "orig", None), "sqlstate", None)
    return code in {_MISSING_TABLE, _NO_PERMISSION}


class DatabaseLedger:
    """One digest per Sarajevo date, recorded in Supabase (docs/proposed-migrations/telegram_digest_sends.sql).

    Until that table exists (or if the bot role cannot use it) every call falls back to ``fallback``,
    the ledger the bot used before, so nothing changes. Other database errors are raised: if the
    ledger cannot be read, no digest is sent rather than risking a duplicate.
    """

    def __init__(self, engine, fallback, chat_id=0):
        self.engine = engine
        self.fallback = fallback
        self.chat_id = chat_id
        self.durable = None  # None until known; False after the table proved unavailable

    def _run(self, sql, params):
        if self.durable is False:
            return None
        try:
            with self.engine.begin() as connection:
                connection.execute(text("SET LOCAL statement_timeout = '5s'"))
                result = connection.execute(text(sql), params).scalar()
        except DBAPIError as error:
            if not _unavailable(error):
                raise
            if self.durable is not False:
                LOG.warning("digest ledger table unavailable; using the in-process ledger")
            self.durable = False
            return None
        self.durable = True
        return result

    def claim(self, day):
        claimed = self._run("""
          INSERT INTO telegram_digest_sends(day,chat_id) VALUES (CAST(:day AS date),:chat)
          ON CONFLICT(day) DO NOTHING RETURNING 1
        """, {"day": day, "chat": self.chat_id})
        if self.durable is False:
            return self.fallback.claim(day)
        return claimed == 1

    def release(self, day):
        self._run("DELETE FROM telegram_digest_sends WHERE day=CAST(:day AS date) AND sent_at IS NULL RETURNING 1",
                  {"day": day})
        if self.durable is False:
            self.fallback.release(day)

    def mark_sent(self, day, message_id):
        self._run("""
          UPDATE telegram_digest_sends SET sent_at=(now() AT TIME ZONE 'UTC'),message_id=:message
          WHERE day=CAST(:day AS date) AND sent_at IS NULL RETURNING 1
        """, {"day": day, "message": message_id if isinstance(message_id, int) else None})


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
            chat_ids = [int(part) for part in chat.split(",") if part.strip()]
            chat_id = chat_ids[0]  # the digest goes to the first (primary) group
        except (ValueError, IndexError) as exc:
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
    return f"{name} {year}" if year else name


def _car(item):
    return _link(_label(item.get("make"), item.get("model"), item.get("year")), item.get("listing_id"))


def _link(label, listing_id):
    url = f"https://olx.ba/artikal/{quote(str(listing_id or ''), safe='')}"
    return f'<a href="{esc(url)}">{esc(label)}</a>'


def _amount(value):
    return f"{int(round(value)):,}".replace(",", ".")


def assess(evidence):
    now = _aware(evidence.get("generated_at")) or datetime.now(timezone.utc)
    clean = _aware(evidence.get("clean_at"))
    stale = clean is None or (now - clean) > timedelta(days=STALE_CLEAN_DAYS)
    run = evidence.get("run")
    outcome = classify(run)
    partial = outcome not in {"ok", "budget_limited"}
    return {"trends_stale": stale, "partial": partial, "empty": not run, "outcome": outcome}


def _status_line(run, flags):
    """One line: ✅ when the latest run is clean, ⚠️ only when something is wrong."""
    if not run:
        return "⚠️ Nema završenog prolaza kolektora"
    new = int(run.get("new_listings") or 0)
    errors = int(run.get("errors") or 0)
    counts = (f"{new} {plural_bs(new, 'novi oglas', 'nova oglasa', 'novih oglasa')} · "
              f"{errors} {plural_bs(errors, 'greška', 'greške', 'grešaka')}")
    if flags.get("outcome") == "budget_limited":
        return f"✅ Sve radi · {counts} · dnevni budžet stranica iskorišten"
    if not flags["partial"]:
        return f"✅ Sve radi · {counts}"
    if str(run.get("status") or "").lower() == "failed":
        return f"⚠️ Prolaz kolektora nije uspio · {counts}"
    return f"⚠️ Djelimičan prolaz kolektora · {counts}"


def _assemble(parts, limit=MAX_DIGEST_CHARS):
    """Join (text, optional) rows. Drop optional rows from the bottom until it fits.

    Rows are whole HTML fragments, so dropping rows never breaks a tag. Required rows are short;
    if they alone were too long the plain-text fallback in the sender takes over.
    """

    def joined(items):
        return "\n".join(text for text, _optional in items).strip()

    chosen = list(parts)
    while len(joined(chosen)) > limit and any(optional for _text, optional in chosen):
        for index in range(len(chosen) - 1, -1, -1):
            if chosen[index][1]:
                del chosen[index]
                break
    return joined(chosen)


def _row(text, optional=False):
    return (text, optional)


def render_digest(evidence, tz=SARAJEVO):
    """Bosnian morning digest in Telegram HTML. Never infers a sale or a publish date."""
    flags = assess(evidence)
    now = _aware(evidence.get("generated_at")) or datetime.now(timezone.utc)
    local = now.astimezone(tz)
    run = evidence.get("run")
    parts = [
        _row(f"🚗 <b>Garaža · {_WEEKDAYS[local.weekday()]} {local.day}. {_MONTHS[local.month - 1]}</b>"),
        _row(esc(_status_line(run, flags))),
    ]
    if flags["trends_stale"]:
        parts.append(_row(esc(f"⚠️ Zadnji potpuno čist prolaz: {_stamp(evidence.get('clean_at'), tz)}, "
                              "pa su brojke o cijenama orijentacione.")))

    drops = int(evidence.get("price_drops") or 0)
    cuts = list(evidence.get("notable_cuts") or [])
    parts.extend([_row(""), _row(f"📉 <b>Padovi cijena ({PRICE_WINDOW_DAYS} dana): {drops}</b>")])
    if not cuts:
        parts.append(_row(esc("Nema zabilježenih padova tražene cijene.")))
    for item in cuts:
        new, old = item.get("asking_price"), item.get("previous_price")
        pct = round((old - new) / old * 100) if new and old else None
        change = f" (−{pct}%)" if pct else ""
        parts.append(_row(f"• {_car(item)}: <b>{esc(_money(new))}</b> <s>{esc(_amount(old))}</s>{esc(change)}",
                          optional=True))
    if drops > len(cuts) and cuts:
        parts.append(_row(esc(f"• …još {drops - len(cuts)}"), optional=True))

    standouts = list(evidence.get("first_seen") or [])
    if standouts:
        parts.extend([_row(""), _row("🆕 <b>Najzanimljivije novo</b>")])
        for item in standouts:
            parts.append(_row(
                f"• {_car(item)}: <b>{esc(_money(item.get('price')))}</b> · "
                + esc(f"{int(item.get('discount_pct') or 0)}% ispod medijana {_money(item.get('med_price'))} "
                      f"za {int(item.get('n') or 0)} sličnih · {_amount(item.get('mileage') or 0)} km"),
                optional=True))

    parts.extend([
        _row(""),
        _row("<i>" + esc("Tražene cijene, ne prodajne. Novo = prvi put viđeno kod nas, ne datum objave. "
                         "Nestanak iz pretrage nije prodaja. Ništa od ovoga nije preporuka.") + "</i>"),
    ])
    details = []
    if run:
        shown_status = ("dnevni budžet stranica iskorišten" if flags.get("outcome") == "budget_limited"
                        else str(run.get("status") or "nepoznato").lower())
        details.append(f"Pokušaj: {_stamp(run.get('finished_at') or run.get('started_at'), tz)} ({shown_status})")
        details.append(f"{int(run.get('observations') or 0)} zapažanja · "
                       f"{int(run.get('new_listings') or 0)} prvi put viđeno kod nas · "
                       f"{int(run.get('errors') or 0)} {plural_bs(int(run.get('errors') or 0), 'greška', 'greške', 'grešaka')}")
        details.append(f"Posjete: {int(run.get('search_attempted') or 0)} pretrage · "
                       f"{int(run.get('detail_attempted') or 0)} detalja "
                       f"({int(run.get('details_completed') or 0)} završeno)")
    else:
        details.append("Nema završenog prolaza kolektora.")
    details.append(f"Zadnje zapažanje: {_stamp(evidence.get('latest_snapshot'), tz)} · "
                   f"zadnji čist prolaz: {_stamp(evidence.get('clean_at'), tz)}")
    rises = int(evidence.get("price_increases") or 0)
    details.append(f"Uzorak cijena ({PRICE_WINDOW_DAYS} dana): {int(evidence.get('price_sample') or 0)} oglasa · "
                   f"{drops} {plural_bs(drops, 'pad', 'pada', 'padova')} · "
                   f"{rises} {plural_bs(rises, 'rast', 'rasta', 'rastova')}")
    parts.append(_row("<blockquote expandable>🔧 Detalji kolektora\n" + esc("\n".join(details)) + "</blockquote>"))
    return Html(_assemble(parts))


def load_digest_evidence(engine, now):
    as_of = _aware(now)
    cutoff = as_of - timedelta(days=PRICE_WINDOW_DAYS)
    with engine.begin() as connection:
        _statement_timeout(connection)
        run = _one(connection, RUN_SQL)
        clean = connection.execute(text(CLEAN_SQL)).scalar()
        latest = connection.execute(text(SNAPSHOT_SQL)).scalar()
        prices = _one(connection, PRICE_SQL, {"cutoff": cutoff, "as_of": as_of}) or {}
        cuts = _all(connection, CUTS_SQL, {"cutoff": cutoff, "as_of": as_of, "limit": DIGEST_CUTS})
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
    rows = [
        [{"text": "📉 Svi padovi", "callback_data": "drops"}, {"text": "🆕 Novi oglasi", "callback_data": "latest"}],
        [{"text": "🔍 Pretraga", "callback_data": "search"}],
    ]
    if market_url:
        parsed = urlparse(market_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        rows.append([
            {"text": "Market", "url": f"{origin}/market"},
            {"text": "Compare", "url": f"{origin}/compare"},
            {"text": "Following", "url": f"{origin}/following"},
        ])
    return {"inline_keyboard": rows}


def drops_message(engine, lang="bs", now=None, limit=10):
    """The "📉 Svi padovi" button: the largest asking-price drops of the last 7 days, linked."""
    as_of = _aware(now or datetime.now(timezone.utc))
    cutoff = as_of - timedelta(days=PRICE_WINDOW_DAYS)
    with engine.begin() as connection:
        _statement_timeout(connection)
        prices = _one(connection, PRICE_SQL, {"cutoff": cutoff, "as_of": as_of}) or {}
        cuts = _all(connection, CUTS_SQL, {"cutoff": cutoff, "as_of": as_of, "limit": limit})
    drops = int(prices.get("drops") or 0)
    lines = [f"📉 <b>{esc(t(lang, 'drops_title', days=PRICE_WINDOW_DAYS, n=drops))}</b>"]
    if not cuts:
        lines.append(esc(t(lang, "drops_none")))
    for item in cuts:
        new, old = item.get("asking_price"), item.get("previous_price")
        pct = round((old - new) / old * 100) if new and old else None
        lines.append(f"• {_car(item)}: <b>{esc(_money(new, lang))}</b> <s>{esc(_amount(old))}</s>"
                     + (esc(f" (−{pct}%)") if pct else ""))
    if drops > len(cuts) and cuts:
        lines.append(esc(t(lang, "drops_more", n=drops - len(cuts))))
    lines.extend(["", "<i>" + esc(t(lang, "drops_note")) + "</i>"])
    return Html("\n".join(lines))


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
    if ledger is None:
        ledger = DatabaseLedger(engine, default_ledger(environ), config.chat_id)
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
    payload = message_payload(config.chat_id, text)
    payload["reply_markup"] = digest_keyboard(config.market_url)
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
    mark = getattr(ledger, "mark_sent", None)
    if mark:
        try:
            mark(plan["day"], body.get("message_id"))
        except Exception:  # the message is out; a failed bookkeeping update must not report a failure
            LOG.warning("digest ledger could not record the sent message")
    body["ledger"] = "database" if getattr(ledger, "durable", None) else "process"
    return 200, body
