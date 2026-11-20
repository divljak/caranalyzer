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
DIGEST_CUTS = 1
DIGEST_STANDOUTS = 2
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
# New cars (year >= 2025 and under 1,000 km) are not used-car deals: they are neither standouts nor part
# of the comparison median. A NULL mileage is never "new" (such rows are dropped by the NOT NULL filter).
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
      AND NOT (c.year >= 2025 AND c.mileage < 1000)
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
  ORDER BY r.first_seen_at DESC,discount_pct DESC,r.listing_id
  LIMIT 2
"""
# "Cijene": the make+model whose median asking price moved the most over the last PRICE_WINDOW_DAYS
# versus the PRICE_WINDOW_DAYS before that. Each listing counts once per window (its latest priced
# observation in it), at least TREND_MIN_ADS ads in both windows, new cars (year >= 2025 and under
# 1,000 km) excluded. A NULL year or mileage is never "new".
TREND_MIN_ADS = 10
TREND_SQL = """
  WITH obs AS (
    SELECT DISTINCT ON (s.listing_id,w.win)
      s.listing_id,w.win,s.asking_price,
      trim(concat_ws(' ',coalesce(l.make,c.make),coalesce(l.model,c.model))) AS label
    FROM listing_snapshots s
    JOIN car_listings c ON c.listing_id=s.listing_id
    LEFT JOIN dealer_vehicle_labels l ON l.listing_id=s.listing_id
    CROSS JOIN LATERAL (SELECT CASE
        WHEN s.observed_at>:mid AND s.observed_at<=:as_of THEN 'cur'
        WHEN s.observed_at>:start AND s.observed_at<=:mid THEN 'prev' END AS win) w
    WHERE w.win IS NOT NULL
      AND c.source='olx.ba'
      AND s.price_status='priced' AND s.asking_price>0
      AND (c.year IS NULL OR c.mileage IS NULL OR c.year<2025 OR c.mileage>=1000)
    ORDER BY s.listing_id,w.win,s.observed_at DESC,s.id DESC
  ), med AS (
    SELECT label,
      count(*) FILTER (WHERE win='cur') AS n_cur,
      count(*) FILTER (WHERE win='prev') AS n_prev,
      percentile_cont(.5) WITHIN GROUP (ORDER BY asking_price) FILTER (WHERE win='cur') AS med_cur,
      percentile_cont(.5) WITHIN GROUP (ORDER BY asking_price) FILTER (WHERE win='prev') AS med_prev
    FROM obs WHERE label<>'' GROUP BY label
  )
  SELECT label,n_cur,n_prev,med_cur,med_prev,
    round((CAST(med_cur AS numeric)/CAST(med_prev AS numeric)-1)*100) AS change_pct
  FROM med
  WHERE n_cur>=CAST(:min_ads AS integer) AND n_prev>=CAST(:min_ads AS integer) AND med_prev>0
    AND round((CAST(med_cur AS numeric)/CAST(med_prev AS numeric)-1)*100)<>0
  ORDER BY abs(med_cur/med_prev-1) DESC,label
  LIMIT 1
"""
# Fallback "Najviše novih": the brand with the most ads our collector saw for the first time in the latest run
# window (the same window as SEEN_SQL and the status line).
# The collector does not record when an ad leaves search, so disappearance is not measured here.
HOT_SQL = """
  SELECT coalesce(nullif(trim(coalesce(l.make,c.make)),''),'Nepoznato') AS make,
    count(*) AS n,
    sum(count(*)) OVER () AS total
  FROM car_listings c
  LEFT JOIN dealer_vehicle_labels l USING(listing_id)
  WHERE c.source='olx.ba'
    AND c.collection_method IN ('olx_api','olx_website')
    AND c.first_seen_at BETWEEN :started AND :finished
  GROUP BY 1
  ORDER BY n DESC,make
  LIMIT 1
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


def _anomaly_line(run, status):
    """Extra ⚠️ line only for an anomaly (no new ads, errors) the status line does not already flag.

    A run with errors is always classified partial, so its status line is already ⚠️ and no second
    line is added; the same holds for a missing run. A discovery gap is not a separate stored flag
    (a budget-limited run is expected and healthy), so it is not reported here.
    """
    if not run or status.startswith("⚠️"):
        return None
    problems = []
    if int(run.get("new_listings") or 0) == 0:
        problems.append("0 novih oglasa u zadnjem prolazu")
    errors = int(run.get("errors") or 0)
    if errors > 0:
        problems.append(f"{errors} {plural_bs(errors, 'greška', 'greške', 'grešaka')} u zadnjem prolazu")
    return "⚠️ Pažnja: " + " · ".join(problems) if problems else None


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
    """Compact Bosnian morning digest in Telegram HTML. Never infers a sale or a publish date."""
    flags = assess(evidence)
    now = _aware(evidence.get("generated_at")) or datetime.now(timezone.utc)
    local = now.astimezone(tz)
    run = evidence.get("run")
    status = _status_line(run, flags)
    if flags["trends_stale"] and run:
        status += f" · cijene orijentaciono (zadnji čist prolaz {_stamp(evidence.get('clean_at'), tz)})"
    parts = [
        _row(f"🚗 <b>Garaža · {_WEEKDAYS[local.weekday()]} {local.day}. {_MONTHS[local.month - 1]}</b>"),
        _row(esc(status)),
    ]
    warning = _anomaly_line(run, status)
    if warning:
        parts.append(_row(esc(warning)))

    cuts = list(evidence.get("notable_cuts") or [])
    if cuts:
        item = cuts[0]
        new, old = item.get("asking_price"), item.get("previous_price")
        pct = round((old - new) / old * 100) if new and old else None
        change = f" (−{pct}%)" if pct else ""
        parts.append(_row(
            f"📉 Najveći pad ({PRICE_WINDOW_DAYS} dana): {_car(item)} −{esc(_money(old - new))} → "
            f"<b>{esc(_money(new))}</b> <s>{esc(_amount(old))}</s>{esc(change)}"))
    else:
        parts.append(_row(esc(f"📉 Nema padova cijena ({PRICE_WINDOW_DAYS} dana)")))

    for item in list(evidence.get("first_seen") or [])[:DIGEST_STANDOUTS]:
        parts.append(_row(
            f"🆕 {_car(item)}: <b>{esc(_money(item.get('price')))}</b> · "
            + esc(f"{int(item.get('discount_pct') or 0)}% ispod medijana {_money(item.get('med_price'))} "
                  f"za {int(item.get('n') or 0)} sličnih · {_amount(item.get('mileage') or 0)} km"),
            optional=True))

    trend = evidence.get("trend")
    hot = evidence.get("hot")
    if trend and trend.get("label") and trend.get("change_pct"):
        pct = int(trend["change_pct"])
        sign = "−" if pct < 0 else "+"
        n = int(trend.get("n_cur") or 0)
        parts.append(_row(esc(
            f"🔥 {trend['label']}: medijan {sign}{abs(pct)}% ove sedmice "
            f"({_money(trend.get('med_cur'))}, {n} {plural_bs(n, 'oglas', 'oglasa', 'oglasa')})"),
            optional=True))
    elif hot and hot.get("make"):
        parts.append(_row(esc(f"🔥 Najviše novih: {hot['make']} ({int(hot.get('n') or 0)} "
                              f"od {int(hot.get('total') or 0)})"), optional=True))

    parts.append(_row("<i>" + esc("Tražene cijene, ne prodajne. Novo = prvi put viđeno kod nas, "
                                  "ne datum objave.") + "</i>"))
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
        hot = None
        trend = _one(connection, TREND_SQL, {
            "as_of": as_of, "mid": cutoff, "start": cutoff - timedelta(days=PRICE_WINDOW_DAYS),
            "min_ads": TREND_MIN_ADS})
        if run and run.get("started_at") and run.get("finished_at"):
            window = {"started": run["started_at"], "finished": run["finished_at"]}
            seen = _all(connection, SEEN_SQL, window)
            hot = _one(connection, HOT_SQL, window)
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
        "hot": hot,
        "trend": trend,
    }


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
