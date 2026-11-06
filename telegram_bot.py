"""Private Telegram interface backed only by Car Analyzer database evidence."""

from dataclasses import dataclass
from datetime import datetime, timezone
import time
import hmac
import os
import re
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from telegram_i18n import (
    BS_MONTHS, DEFAULT_LANGUAGE, LANGUAGES, detect_language, plural_bs, status_label, t, telegram_language,
)
from telegram_intent import (
    Filters,
    Intent,
    TITLE_BUDGET,
    TITLE_FIND,
    clean_search,
    context_from_reply,
    fold,
    looks_like_followup,
    parse_question,
)
import telegram_llm


SARAJEVO = ZoneInfo("Europe/Sarajevo")
TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE = 3900


class TelegramConfigurationError(RuntimeError):
    """Configuration failure whose message never contains a secret value."""


class TelegramAPIError(RuntimeError):
    """Telegram API failure whose message never contains the bot token."""


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    webhook_secret: str
    chat_id: int
    allowed_user_ids: frozenset[int]
    market_url: str | None = None
    bot_username: str | None = None

    @classmethod
    def from_env(cls, environ=None):
        values = os.environ if environ is None else environ
        required = {
            name: (values.get(name) or "").strip()
            for name in (
                "TELEGRAM_BOT_TOKEN",
                "TELEGRAM_WEBHOOK_SECRET",
                "TELEGRAM_CHAT_ID",
                "TELEGRAM_ALLOWED_USER_IDS",
            )
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise TelegramConfigurationError(
                "Missing Telegram configuration: " + ", ".join(sorted(missing)))
        try:
            chat_id = int(required["TELEGRAM_CHAT_ID"])
            users = frozenset(
                int(value.strip())
                for value in required["TELEGRAM_ALLOWED_USER_IDS"].split(",")
                if value.strip()
            )
        except ValueError as exc:
            raise TelegramConfigurationError(
                "Telegram chat and user IDs must be integers") from exc
        if not users:
            raise TelegramConfigurationError("At least one allowed Telegram user is required")
        secret = required["TELEGRAM_WEBHOOK_SECRET"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", secret):
            raise TelegramConfigurationError(
                "Telegram webhook secret must be 16-256 letters, numbers, underscores, or dashes")
        market_url = (values.get("TELEGRAM_MARKET_URL") or "").strip() or None
        if market_url:
            parsed = urlparse(market_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise TelegramConfigurationError("Telegram market URL must use HTTPS")
        username = (values.get("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@") or None
        if username and not re.fullmatch(r"[A-Za-z0-9_]{3,64}", username):
            raise TelegramConfigurationError("Telegram bot username is invalid")
        return cls(
            bot_token=required["TELEGRAM_BOT_TOKEN"],
            webhook_secret=secret,
            chat_id=chat_id,
            allowed_user_ids=users,
            market_url=market_url,
            bot_username=username,
        )

    def accepts_secret(self, candidate):
        return bool(candidate) and hmac.compare_digest(self.webhook_secret, candidate)


def _utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _time(value, lang=DEFAULT_LANGUAGE):
    value = _utc(value)
    if not value:
        return t(lang, "unknown")
    local = value.astimezone(SARAJEVO)
    if lang == "bs":
        return f"{local.day}. {BS_MONTHS[local.month - 1]}, {local.strftime('%H:%M')}"
    return local.strftime("%d %b, %H:%M")


FRESH_DAYS = 14


def _age(value, lang=DEFAULT_LANGUAGE):
    """Plain-language age of an observation, e.g. 'today', '3 days ago', '4 weeks ago'."""
    value = _utc(value)
    if lang == "bs":
        if value is None:
            return "u nepoznato vrijeme"
        days = max(0, (datetime.now(timezone.utc) - value).days)
        if days == 0:
            return "danas"
        if days == 1:
            return "juče"
        if days < FRESH_DAYS:
            return f"prije {days} {plural_bs(days, 'dan', 'dana', 'dana')}"
        if days < 60:
            weeks = days // 7
            return f"prije {weeks} {plural_bs(weeks, 'sedmicu', 'sedmice', 'sedmica')}"
        months = days // 30
        return f"prije {months} {plural_bs(months, 'mjesec', 'mjeseca', 'mjeseci')}"
    if value is None:
        return "at an unknown time"
    days = max(0, (datetime.now(timezone.utc) - value).days)
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < FRESH_DAYS:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    return f"{days // 30} months ago"


def _is_fresh(value):
    value = _utc(value)
    return value is not None and (datetime.now(timezone.utc) - value).days < FRESH_DAYS


def _money(value, lang=DEFAULT_LANGUAGE):
    return f"{int(round(value)):,} KM".replace(",", ".") if value is not None else t(lang, "price_unavailable")


def _one(connection, sql, params=None):
    row = connection.execute(text(sql), params or {}).mappings().first()
    return dict(row) if row else None


def _all(connection, sql, params=None):
    return [dict(row) for row in connection.execute(text(sql), params or {}).mappings()]


def _statement_timeout(connection):
    connection.execute(text("SET LOCAL statement_timeout = '8s'"))


def status_message(engine, lang=DEFAULT_LANGUAGE):
    with engine.begin() as connection:
        _statement_timeout(connection)
        run = _one(connection, """
          SELECT status,started_at,finished_at,search_attempted,detail_attempted,
            details_completed,observations,new_listings,errors
          FROM website_collection_runs
          WHERE finished_at IS NOT NULL
          ORDER BY started_at DESC,id DESC LIMIT 1
        """)
        observed = _one(connection, """
          SELECT status,started_at,finished_at,observations,errors
          FROM website_collection_runs
          WHERE finished_at IS NOT NULL AND observations>0
          ORDER BY started_at DESC,id DESC LIMIT 1
        """)
        clean = connection.execute(text("""
          SELECT max(end_time) FROM scraping_logs
          WHERE status='completed' AND coalesce(errors_count,0)=0
        """)).scalar()
        latest_snapshot = connection.execute(text(
            "SELECT max(observed_at) FROM listing_snapshots")).scalar()
    if not run:
        return t(lang, "status_none")
    lines = [
        t(lang, "status_title", quality=status_label(lang, run["status"])),
        t(lang, "status_latest_attempt", when=_time(run["finished_at"] or run["started_at"], lang)),
        t(lang, "status_counts", obs=run["observations"], new=run["new_listings"], errors=run["errors"]),
        t(lang, "status_visits", search=run["search_attempted"], detail=run["detail_attempted"],
          done=run["details_completed"]),
        t(lang, "status_latest_obs", when=_time(latest_snapshot, lang)),
        t(lang, "status_clean", when=_time(clean, lang)),
    ]
    if observed and observed["started_at"] != run["started_at"]:
        lines.append(t(lang, "status_observed_attempt", when=_time(observed["finished_at"], lang),
                       status=observed["status"] if lang != "bs" else status_label(lang, observed["status"]),
                       n=observed["observations"]))
    lines.extend(["", t(lang, "status_note1"), t(lang, "status_note2")])
    return "\n".join(lines)


def trends_message(engine, days, lang=DEFAULT_LANGUAGE):
    days = max(1, min(int(days), 90))
    with engine.begin() as connection:
        _statement_timeout(connection)
        result = _one(connection, """
          WITH unique_observations AS (
            SELECT DISTINCT ON (listing_id,observed_at)
              listing_id,observed_at,asking_price,price_status
            FROM listing_snapshots
            ORDER BY listing_id,observed_at,id DESC
          ), transitions AS (
            SELECT *,lag(asking_price) OVER (
              PARTITION BY listing_id ORDER BY observed_at) AS previous_price
            FROM unique_observations
            WHERE price_status='priced' AND asking_price>0
          ), recent_listings AS (
            SELECT DISTINCT listing_id FROM unique_observations
            WHERE observed_at >= (now() AT TIME ZONE 'UTC') - make_interval(days=>:days)
          )
          SELECT
            (SELECT count(*) FROM recent_listings) AS sample_size,
            count(DISTINCT listing_id) FILTER (WHERE
              observed_at >= (now() AT TIME ZONE 'UTC') - make_interval(days=>:days)) AS priced_sample,
            count(*) FILTER (WHERE
              observed_at >= (now() AT TIME ZONE 'UTC') - make_interval(days=>:days)
              AND asking_price<previous_price) AS drops,
            count(*) FILTER (WHERE
              observed_at >= (now() AT TIME ZONE 'UTC') - make_interval(days=>:days)
              AND asking_price>previous_price) AS increases,
            max(observed_at) FILTER (WHERE
              observed_at >= (now() AT TIME ZONE 'UTC') - make_interval(days=>:days)) AS newest
          FROM transitions
        """, {"days": days})
    return "\n".join([
        t(lang, "trends_title", days=days),
        t(lang, "trends_sample", sample=result["sample_size"], priced=result["priced_sample"]),
        t(lang, "trends_transitions", drops=result["drops"], rises=result["increases"]),
        t(lang, "fresh_line", when=_time(result["newest"], lang)),
        "",
        t(lang, "trends_note"),
    ])


def _parse_amount(value):
    match = re.fullmatch(r"([0-9][0-9.,]*)(k)?", value.strip().lower())
    if not match:
        return None
    digits = re.sub(r"[^0-9]", "", match.group(1))
    if not digits:
        return None
    amount = int(digits)
    if match.group(2):
        amount *= 1000
    return amount if 1_000 <= amount <= 1_000_000 else None


def budget_message(engine, argument, lang=DEFAULT_LANGUAGE):
    parts = argument.strip().split(maxsplit=1)
    amount = _parse_amount(parts[0]) if parts else None
    if amount is None:
        return t(lang, "budget_usage")
    search = parts[1].strip() if len(parts) == 2 else ""
    with engine.begin() as connection:
        _statement_timeout(connection)
        matches = _all(connection, """
          SELECT c.listing_id,coalesce(l.make,c.make) AS make,
            coalesce(l.model,c.model) AS model,c.year,c.price,c.mileage,c.last_verified_at
          FROM car_listings c
          LEFT JOIN dealer_vehicle_labels l USING(listing_id)
          WHERE c.source='olx.ba'
            AND c.collection_method IN ('olx_api','olx_website')
            AND c.price_status='priced' AND c.price>0 AND c.price<=:budget
            AND c.last_verified_at >= (now() AT TIME ZONE 'UTC') - interval '90 days'
            AND (:search='' OR lower(concat_ws(' ',coalesce(l.make,c.make),
              coalesce(l.model,c.model))) LIKE :pattern)
          ORDER BY c.last_verified_at DESC,c.year DESC NULLS LAST,c.price DESC,c.listing_id
          LIMIT 5
        """, {
            "budget": amount,
            "search": search,
            "pattern": f"%{search.lower()}%",
        })
    title = t(lang, "budget_title", amount=_money(amount, lang))
    if search:
        title += f" · {search}"
    if not matches:
        return title + "\n" + t(lang, "no_match_90")
    lines = [title, t(lang, "budget_showing", n=len(matches))]
    for item in matches:
        label = " ".join(value for value in (item["make"], item["model"]) if value) or t(lang, "unparsed_model")
        year = f" · {item['year']}" if item["year"] else ""
        mileage = f" · {int(item['mileage']):,} km".replace(",", ".") if item["mileage"] is not None else ""
        lines.append(
            f"• {label}{year} · {_money(item['price'], lang)}{mileage}\n"
            + t(lang, "observed_row", when=_time(item["last_verified_at"], lang),
                url=f"https://olx.ba/artikal/{item['listing_id']}"))
    lines.extend(["", t(lang, "budget_footer")])
    return "\n".join(lines)


STALE_AFTER_HOURS = 48
THIN_SAMPLE = 5
_LABEL_SQL = ("translate(lower(concat_ws(' ',coalesce(l.make,c.make),coalesce(l.model,c.model))),"
              "'šđčćž','sdccz')")


def _like_pattern(value):
    escaped = fold(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _cohort_stats(connection, label):
    return _one(connection, f"""
      SELECT count(*) AS sample_size,
        count(*) FILTER (WHERE c.price_status='priced' AND c.price>0) AS priced_sample,
        percentile_cont(.5) WITHIN GROUP (ORDER BY c.price)
          FILTER (WHERE c.price_status='priced' AND c.price>0) AS median,
        min(c.price) FILTER (WHERE c.price_status='priced' AND c.price>0) AS minimum,
        max(c.price) FILTER (WHERE c.price_status='priced' AND c.price>0) AS maximum,
        max(c.last_verified_at) AS newest
      FROM car_listings c
      LEFT JOIN dealer_vehicle_labels l USING(listing_id)
      WHERE c.source='olx.ba'
        AND c.collection_method IN ('olx_api','olx_website')
        AND c.last_verified_at >= (now() AT TIME ZONE 'UTC') - interval '90 days'
        AND {_LABEL_SQL} LIKE :pattern ESCAPE '\\'
    """, {"pattern": _like_pattern(label)})


def _cohort_lines(label, result, lang=DEFAULT_LANGUAGE):
    lines = [
        label,
        t(lang, "cohort_sample", n=result["sample_size"], priced=result["priced_sample"]),
        t(lang, "cohort_median", median=_money(result["median"], lang), low=_money(result["minimum"], lang),
          high=_money(result["maximum"], lang)),
    ]
    if result["priced_sample"] < THIN_SAMPLE:
        lines.append(t(lang, "cohort_thin"))
    return lines


def compare_message(engine, argument, lang=DEFAULT_LANGUAGE):
    labels = [value.strip() for value in re.split(r"\s*(?:\||\bvs\.?\b)\s*", argument, flags=re.I) if value.strip()]
    if len(labels) != 2:
        return t(lang, "compare_usage")
    results = []
    with engine.begin() as connection:
        _statement_timeout(connection)
        for label in labels:
            results.append((label, _cohort_stats(connection, label)))
    lines = [t(lang, "compare_title")]
    newest = None
    for label, result in results:
        newest = max(filter(None, (newest, _utc(result["newest"]))), default=None)
        lines.extend(["", *_cohort_lines(label, result, lang)])
    lines.extend(["", t(lang, "fresh_line", when=_time(newest, lang)), t(lang, "compare_note")])
    return "\n".join(lines)


def model_stats_message(engine, label, lang=DEFAULT_LANGUAGE):
    with engine.begin() as connection:
        _statement_timeout(connection)
        result = _cohort_stats(connection, label)
        note = _freshness_note(connection, lang)
    lines = [t(lang, "stats_title", label=label), ""]
    if not result["sample_size"]:
        lines.append(t(lang, "no_match_90"))
    else:
        lines.extend(_cohort_lines(t(lang, "stats_evidence"), result, lang))
        lines.append(t(lang, "fresh_line", when=_time(result["newest"], lang)))
    if note:
        lines.extend(["", note])
    lines.extend(["", t(lang, "stats_note")])
    return "\n".join(lines)


def _freshness_note(connection, lang=DEFAULT_LANGUAGE):
    """Return a warning when evidence is missing, stale or from a partial run."""
    latest = connection.execute(text("SELECT max(observed_at) FROM listing_snapshots")).scalar()
    run = _one(connection, """
      SELECT status FROM website_collection_runs
      WHERE finished_at IS NOT NULL ORDER BY started_at DESC,id DESC LIMIT 1
    """)
    if latest is None:
        return t(lang, "fresh_none")
    notes = []
    age = datetime.now(timezone.utc) - _utc(latest)
    if age.total_seconds() > STALE_AFTER_HOURS * 3600:
        notes.append(t(lang, "fresh_stale", days=age.days, when=_time(latest, lang)))
    if run and run["status"] != "completed":
        notes.append(t(lang, "fresh_run", status=status_label(lang, run["status"])))
    return " ".join(notes)


def _filter_title(filters, lang=DEFAULT_LANGUAGE):
    parts = [t(lang, "find_title")]
    if filters.search:
        parts.append(filters.search)
    if filters.budget:
        parts.append(t(lang, "title_upto", x=_money(filters.budget, lang)))
    if filters.year_min:
        parts.append(t(lang, "title_from", x=filters.year_min))
    if filters.mileage_max:
        parts.append(t(lang, "title_upto", x=f"{filters.mileage_max:,} km".replace(",", ".")))
    return " · ".join(parts)


PAGE_SIZE = 5
MAX_OFFSET = 95


@dataclass(frozen=True)
class Page:
    """What the keyboard needs to know about a find answer."""
    filters: Filters
    next_offset: int | None = None
    min_year: int | None = None
    max_km: int | None = None


@dataclass(frozen=True)
class Choices:
    """Quick-reply buttons for an answer: rows of (label, callback_data)."""
    rows: tuple


def find_page(engine, filters, offset=0, lang=DEFAULT_LANGUAGE):
    """Return (message text, total matches, stats) for one page of find results.

    ``stats`` carries what the follow-up buttons need (oldest year and highest mileage shown).
    """
    offset = max(0, min(int(offset), MAX_OFFSET))
    with engine.begin() as connection:
        _statement_timeout(connection)
        matches = _all(connection, f"""
          SELECT c.listing_id,coalesce(l.make,c.make) AS make,
            coalesce(l.model,c.model) AS model,c.year,c.price,c.mileage,c.last_verified_at,
            count(*) OVER () AS total
          FROM car_listings c
          LEFT JOIN dealer_vehicle_labels l USING(listing_id)
          WHERE c.source='olx.ba'
            AND c.collection_method IN ('olx_api','olx_website')
            AND c.price_status='priced' AND c.price>0
            AND c.last_verified_at >= (now() AT TIME ZONE 'UTC') - interval '90 days'
            AND (CAST(:budget AS integer) IS NULL OR c.price<=CAST(:budget AS integer))
            AND (CAST(:year_min AS integer) IS NULL OR c.year>=CAST(:year_min AS integer))
            AND (CAST(:mileage_max AS integer) IS NULL OR c.mileage<=CAST(:mileage_max AS integer))
            AND (:search='' OR {_LABEL_SQL} LIKE :pattern ESCAPE '\\')
          ORDER BY c.year DESC NULLS LAST,
            (c.last_verified_at < (now() AT TIME ZONE 'UTC') - interval '14 days') ASC,
            c.mileage ASC NULLS LAST,c.last_verified_at DESC,c.listing_id
          LIMIT {PAGE_SIZE} OFFSET CAST(:offset AS integer)
        """, {
            "budget": filters.budget,
            "year_min": filters.year_min,
            "mileage_max": filters.mileage_max,
            "search": filters.search,
            "pattern": _like_pattern(filters.search),
            "offset": offset,
        })
        note = _freshness_note(connection, lang)
    lines = [_filter_title(filters, lang)]
    total = 0
    older = 0
    stats = {}
    if not matches:
        lines.append(t(lang, "no_more") if offset else t(lang, "no_match_90"))
        if offset == 0 and filters.search and not (filters.budget or filters.year_min or filters.mileage_max):
            lines.append(t(lang, "find_hint"))
    else:
        total = matches[0]["total"]
        first, last = offset + 1, offset + len(matches)
        span = f"{first}" if first == last else f"{first}–{last}"
        lines.append(t(lang, "find_showing", span=span, total=total))
        years = [item["year"] for item in matches if item["year"]]
        kms = [int(item["mileage"]) for item in matches if item["mileage"] is not None]
        stats = {"min_year": min(years) if years else None, "max_km": max(kms) if kms else None}
        for item in matches:
            label = " ".join(value for value in (item["make"], item["model"]) if value) or t(lang, "unparsed_model")
            year = f" · {item['year']}" if item["year"] else ""
            mileage = f" · {int(item['mileage']):,} km".replace(",", ".") if item["mileage"] is not None else ""
            fresh = _is_fresh(item["last_verified_at"])
            older += not fresh
            seen = t(lang, "seen_row", age=_age(item["last_verified_at"], lang),
                     when=_time(item["last_verified_at"], lang))
            marker = "" if fresh else "⚠ "
            lines.append(
                f"• {label}{year} · {_money(item['price'], lang)}{mileage}\n"
                f"  {marker}{seen} · https://olx.ba/artikal/{item['listing_id']}")
    if older:
        lines.extend(["", t(lang, "old_warning", days=FRESH_DAYS)])
    if filters.year_min or filters.mileage_max:
        lines.extend(["", t(lang, "filters_note")])
    if note:
        lines.extend(["", note])
    lines.extend(["", t(lang, "find_footer")])
    return "\n".join(lines), total, stats


def find_message(engine, filters, offset=0, lang=DEFAULT_LANGUAGE):
    return find_page(engine, filters, offset, lang)[0]


SUGGEST_FRESH_DAYS = 14
SUGGEST_MIN_MODEL_LISTINGS = 3
SUGGEST_MIN_COHORT = 4
SUGGEST_DISCOUNT = 0.85
_BASE_LABEL = "trim(concat_ws(' ',coalesce(l.make,c.make),coalesce(l.model,c.model)))"
_RECENT_WHERE = """c.source='olx.ba'
            AND c.collection_method IN ('olx_api','olx_website')
            AND c.price_status='priced' AND c.price>0
            AND c.last_verified_at >= (now() AT TIME ZONE 'UTC') - interval '90 days'"""
_BOUNDS_WHERE = """(CAST(:budget AS integer) IS NULL OR {p}.price<=CAST(:budget AS integer))
            AND (CAST(:year_min AS integer) IS NULL OR {p}.year>=CAST(:year_min AS integer))
            AND (CAST(:mileage_max AS integer) IS NULL OR {p}.mileage<=CAST(:mileage_max AS integer))"""


def _suggest_title(filters, lang=DEFAULT_LANGUAGE):
    parts = [t(lang, "suggest_title"), t(lang, "title_upto", x=_money(filters.budget, lang))]
    if filters.year_min:
        parts.append(t(lang, "title_from", x=filters.year_min))
    if filters.mileage_max:
        parts.append(t(lang, "title_upto", x=f"{filters.mileage_max:,} km".replace(",", ".")))
    return " · ".join(parts)


def suggest_message(engine, filters, lang=DEFAULT_LANGUAGE, with_models=False):
    """What the database shows for a budget: most listed models and listings priced under similar ones."""
    params = {"budget": filters.budget, "year_min": filters.year_min, "mileage_max": filters.mileage_max}
    with engine.begin() as connection:
        _statement_timeout(connection)
        models = _all(connection, f"""
          SELECT label, count(*) AS n,
            percentile_cont(.5) WITHIN GROUP (ORDER BY price) AS median_price,
            percentile_cont(.5) WITHIN GROUP (ORDER BY mileage) FILTER (WHERE mileage IS NOT NULL) AS median_km,
            max(year) AS newest_year
          FROM (
            SELECT {_BASE_LABEL} AS label, c.price, c.mileage, c.year
            FROM car_listings c LEFT JOIN dealer_vehicle_labels l USING(listing_id)
            WHERE {_RECENT_WHERE}
              AND {_BOUNDS_WHERE.format(p='c')}
          ) q
          WHERE label <> ''
          GROUP BY label HAVING count(*) >= {SUGGEST_MIN_MODEL_LISTINGS}
          ORDER BY n DESC, label LIMIT 5
        """, params)
        standouts = _all(connection, f"""
          WITH recent AS (
            SELECT c.listing_id, {_BASE_LABEL} AS label, c.year, c.price, c.mileage, c.last_verified_at
            FROM car_listings c LEFT JOIN dealer_vehicle_labels l USING(listing_id)
            WHERE {_RECENT_WHERE} AND c.year IS NOT NULL AND c.mileage IS NOT NULL
          ), cohort AS (
            SELECT label, year, count(*) AS n,
              percentile_cont(.5) WITHIN GROUP (ORDER BY price) AS med_price,
              percentile_cont(.5) WITHIN GROUP (ORDER BY mileage) AS med_km
            FROM recent WHERE label <> '' GROUP BY label, year
            HAVING count(*) >= {SUGGEST_MIN_COHORT}
          )
          SELECT r.listing_id, r.label, r.year, r.price, r.mileage, r.last_verified_at, k.n, k.med_price,
            round((1 - CAST(r.price AS numeric) / CAST(k.med_price AS numeric)) * 100) AS discount_pct
          FROM recent r JOIN cohort k ON k.label = r.label AND k.year = r.year
          WHERE {_BOUNDS_WHERE.format(p='r')}
            AND r.last_verified_at >= (now() AT TIME ZONE 'UTC') - interval '{SUGGEST_FRESH_DAYS} days'
            AND r.price <= {SUGGEST_DISCOUNT} * k.med_price AND r.mileage <= k.med_km
          ORDER BY discount_pct DESC, r.mileage ASC, r.listing_id LIMIT 5
        """, params)
        note = _freshness_note(connection, lang)
    lines = [_suggest_title(filters, lang), "", t(lang, "suggest_models_h")]
    if models:
        for item in models:
            km = (t(lang, "suggest_km", km=f"{int(round(item['median_km'])):,}".replace(",", "."))
                  if item["median_km"] is not None else "")
            lines.append("• " + t(lang, "suggest_model_row", label=item["label"], n=item["n"],
                                  price=_money(item["median_price"], lang), km=km,
                                  year=item["newest_year"] or t(lang, "unknown")))
    else:
        lines.append(t(lang, "suggest_notenough", n=SUGGEST_MIN_MODEL_LISTINGS))
    lines.extend(["", t(lang, "suggest_standouts_h", days=SUGGEST_FRESH_DAYS)])
    if standouts:
        for item in standouts:
            lines.append(
                "• " + t(lang, "suggest_standout_row", label=item["label"], year=item["year"],
                         price=_money(item["price"], lang), pct=int(item["discount_pct"]),
                         med=_money(item["med_price"], lang), n=item["n"],
                         km=f"{int(item['mileage']):,}".replace(",", ".")) + "\n"
                f"  {t(lang, 'seen_row', age=_age(item['last_verified_at'], lang), when=_time(item['last_verified_at'], lang))}"
                f" · https://olx.ba/artikal/{item['listing_id']}")
    else:
        lines.append(t(lang, "suggest_none"))
    if note:
        lines.extend(["", note])
    lines.extend(["", t(lang, "suggest_foot1"), t(lang, "suggest_foot2")])
    text_value = "\n".join(lines)
    return (text_value, [item["label"] for item in models]) if with_models else text_value


def chitchat_message(lang=DEFAULT_LANGUAGE):
    return t(lang, "chitchat")


def clarify_message(note="", question="", lang=DEFAULT_LANGUAGE):
    if question:
        return question + "\n\n" + t(lang, "clarify_suffix")
    if note == "currency":
        return t(lang, "clarify_currency")
    return t(lang, "clarify_default")


def unsupported_message(note="", lang=DEFAULT_LANGUAGE):
    if note == "forecast":
        return t(lang, "unsupported_forecast")
    return t(lang, "unsupported_default")


def latest_message(engine, lang=DEFAULT_LANGUAGE):
    with engine.begin() as connection:
        _statement_timeout(connection)
        run = _one(connection, """
          SELECT status,started_at,finished_at,observations,new_listings,errors
          FROM website_collection_runs
          WHERE finished_at IS NOT NULL AND observations>0
          ORDER BY started_at DESC,id DESC LIMIT 1
        """)
        if not run:
            return t(lang, "latest_none")
        listings = _all(connection, """
          SELECT c.listing_id,coalesce(l.make,c.make) AS make,
            coalesce(l.model,c.model) AS model,c.year,
            CASE WHEN c.price_status='priced' AND c.price>0 THEN c.price END AS price,
            c.first_seen_at,c.last_verified_at
          FROM car_listings c
          LEFT JOIN dealer_vehicle_labels l USING(listing_id)
          WHERE c.source='olx.ba'
            AND c.collection_method IN ('olx_api','olx_website')
            AND c.first_seen_at BETWEEN :started AND :finished
          ORDER BY c.last_verified_at DESC,c.listing_id LIMIT 5
        """, {"started": run["started_at"], "finished": run["finished_at"]})
    lines = [
        t(lang, "latest_title", status=status_label(lang, run["status"]), when=_time(run["finished_at"], lang)),
        t(lang, "latest_counts", obs=run["observations"], new=run["new_listings"], errors=run["errors"]),
    ]
    if listings:
        lines.append("\n" + t(lang, "latest_few"))
        for item in listings:
            label = " ".join(value for value in (item["make"], item["model"]) if value) or t(lang, "unparsed_model")
            year = f" · {item['year']}" if item["year"] else ""
            lines.append(
                f"• {label}{year} · {_money(item['price'], lang)}\n"
                f"  https://olx.ba/artikal/{item['listing_id']}")
    else:
        lines.append("\n" + t(lang, "latest_no_ids"))
    lines.extend(["", t(lang, "latest_note")])
    return "\n".join(lines)


def help_message(lang=DEFAULT_LANGUAGE):
    return t(lang, "help")


def command_for(text_value):
    value = (text_value or "").strip()
    if not value:
        return "help", ""
    first, *rest = value.split(maxsplit=1)
    command = first.lower().split("@", 1)[0]
    argument = rest[0] if rest else ""
    if command.startswith("/"):
        command = command[1:]
        return command, argument
    lowered = value.lower()
    if any(word in lowered for word in ("status", "fresh", "update", "today")):
        return "status", ""
    if any(word in lowered for word in ("latest", "finds", "interesting")):
        return "latest", ""
    if "trend" in lowered or "price change" in lowered:
        match = re.search(r"\b([1-9][0-9]?)\b", lowered)
        return "trends", match.group(1) if match else "7"
    return "help", ""


def render_command(engine, command, argument, lang=DEFAULT_LANGUAGE):
    if command == "start":
        return t(lang, "welcome")
    if command == "help":
        return help_message(lang)
    if command == "status":
        return status_message(engine, lang)
    if command == "latest":
        return latest_message(engine, lang)
    if command == "trends":
        try:
            days = int(argument or "7")
        except ValueError:
            return t(lang, "trends_usage")
        return trends_message(engine, days, lang)
    if command == "budget":
        return budget_message(engine, argument, lang)
    if command == "compare":
        return compare_message(engine, argument, lang)
    return help_message(lang)


def _round_budget(value):
    return max(1_000, int(round(value / 500.0)) * 500)


def refine_callback(filters, offset=0):
    data = (f"f:{filters.budget or 0}:{filters.year_min or 0}:{filters.mileage_max or 0}:"
            f"{offset}:{filters.search}")
    return data if len(data.encode()) <= 64 else None


def parse_refine_page(data):
    """Return (Filters, offset) from a find button, or None. Accepts the older 5-field form."""
    parts = (data or "").split(":", 5)
    if not parts or parts[0] != "f" or len(parts) not in (5, 6):
        return None
    try:
        budget, year, mileage = (int(value) for value in parts[1:4])
        offset = int(parts[4]) if len(parts) == 6 else 0
    except ValueError:
        return None
    filters = Filters(clean_search(parts[-1]), budget or None, year or None, mileage or None)
    in_range = (
        (filters.budget is None or 1_000 <= filters.budget <= 1_000_000)
        and (filters.year_min is None or 1980 <= filters.year_min <= 2035)
        and (filters.mileage_max is None or 1_000 <= filters.mileage_max <= 1_000_000)
        and 0 <= offset <= MAX_OFFSET
    )
    return (filters, offset) if in_range and not filters.is_empty() else None


def parse_refine_callback(data):
    parsed = parse_refine_page(data)
    return parsed[0] if parsed else None


def suggest_callback(budget):
    return f"s:{int(budget)}"


def parse_suggest_callback(data):
    """Return a budget in KM from an 's:<budget>' button, or None."""
    if not isinstance(data, str) or not data.startswith("s:"):
        return None
    try:
        budget = int(data[2:])
    except ValueError:
        return None
    return budget if 1_000 <= budget <= 1_000_000 else None


def _thousands(value):
    return f"{int(value):,}".replace(",", ".")


def _button(label, data):
    return {"text": label, "callback_data": data}


def example_choices(lang):
    """The three example questions offered on /start and after small talk."""
    return Choices(((
        (t(lang, "btn_ex_find"), refine_callback(Filters("golf", 15000))),
        (t(lang, "btn_ex_suggest"), suggest_callback(30000)),
        (t(lang, "btn_ex_latest"), "latest"),
    ),))


def budget_choices(lang):
    amounts = (10_000, 20_000, 30_000, 50_000)
    return Choices((tuple((t(lang, "btn_budget", amount=_thousands(a)), suggest_callback(a)) for a in amounts),))


def _find_refinements(filters, page, lang):
    """Contextual rows for a find answer: more, budget, newer, lower mileage."""
    rows = []
    if page.next_offset is not None:
        data = refine_callback(filters, page.next_offset)
        if data:
            key = "btn_show_first" if page.next_offset == 0 else "btn_more"
            rows.append([_button(t(lang, key, a=page.next_offset + 1, b=page.next_offset + PAGE_SIZE), data)])
    if filters.budget:
        row = []
        for key, factor in (("btn_cheaper", 0.9), ("btn_pricier", 1.1)):
            data = refine_callback(Filters(filters.search, _round_budget(filters.budget * factor),
                                           filters.year_min, filters.mileage_max))
            if data:
                row.append(_button(t(lang, key), data))
        if row:
            rows.append(row)
    else:
        row = []
        for amount in (10_000, 20_000, 30_000):
            data = refine_callback(Filters(filters.search, amount, filters.year_min, filters.mileage_max))
            if data:
                row.append(_button(t(lang, "btn_budget", amount=_thousands(amount)), data))
        if row:
            rows.append(row)
    row = []
    base_year = filters.year_min or page.min_year
    if base_year:
        newer = min(datetime.now(timezone.utc).year, base_year + 2)
        data = refine_callback(Filters(filters.search, filters.budget, newer, filters.mileage_max))
        if newer > (filters.year_min or 0) and data:
            row.append(_button(t(lang, "btn_newer", year=newer), data))
    base_km = filters.mileage_max or page.max_km
    if base_km:
        lower = max(10_000, int(base_km * 0.7) // 10_000 * 10_000)
        data = refine_callback(Filters(filters.search, filters.budget, filters.year_min, lower))
        if lower < base_km and data:
            row.append(_button(t(lang, "btn_lowkm", km=_thousands(lower)), data))
    if row:
        rows.append(row)
    return rows


def keyboard(config, page=None, lang=DEFAULT_LANGUAGE):
    """Inline buttons for an answer.

    ``None`` gives the general navigation row; ``False`` gives no buttons (e.g. after "thanks");
    a ``Page`` gives the follow-ups that make sense for search results; ``Choices`` gives
    quick replies. A bare ``Filters`` is treated as a ``Page`` for older callers.
    """
    if page is False:
        return None
    if isinstance(page, Choices):
        rows = [[_button(label, data) for label, data in row if data] for row in page.rows]
        rows = [row for row in rows if row]
        return {"inline_keyboard": rows} if rows else None
    if isinstance(page, Filters):
        page = Page(page)
    if isinstance(page, Page):
        rows = _find_refinements(page.filters, page, lang)
        if config.market_url:
            rows.append([{"text": t(lang, "btn_market"), "url": config.market_url}])
        return {"inline_keyboard": rows} if rows else None
    rows = [
        [_button(t(lang, "btn_status"), "status"), _button(t(lang, "btn_latest"), "latest")],
        [_button(t(lang, "btn_trends7"), "trends:7"), _button(t(lang, "btn_trends30"), "trends:30")],
    ]
    if config.market_url:
        rows.append([{"text": t(lang, "btn_market"), "url": config.market_url}])
    return {"inline_keyboard": rows}


def telegram_call(config, method, payload, *, post=requests.post):
    try:
        response = post(
            f"{TELEGRAM_API}/bot{config.bot_token}/{method}",
            json=payload,
            timeout=12,
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise TelegramAPIError(f"Telegram {method} request failed") from exc
    if not result.get("ok"):
        raise TelegramAPIError(f"Telegram {method} returned an error")
    return result.get("result")


def _identity(update):
    callback = update.get("callback_query") or {}
    message = callback.get("message") or update.get("message") or {}
    sender = callback.get("from") or message.get("from") or {}
    chat = message.get("chat") or {}
    return chat, sender.get("id"), message, callback


def is_authorized(chat, user_id, config):
    """Allowlisted user, in the configured group or in their own private chat with the bot."""
    if not isinstance(chat, dict) or user_id not in config.allowed_user_ids:
        return False
    chat_id = chat.get("id")
    if chat_id == config.chat_id:
        return True
    return chat.get("type") == "private" and chat_id == user_id


# Best-effort memory of the last answer's scope in a private chat. It lives only in this
# process (serverless instances may not share it), is keyed by chat AND user, expires, and
# is never used for the group: group follow-ups must reply to a bot message instead.
SCOPE_TTL_SECONDS = 1800
SCOPE_MAX_ENTRIES = 64
_SCOPES = {}


def _remember_scope(key, intent):
    if intent.kind not in {"find", "trends"}:
        return
    now = time.monotonic()
    for stale in [k for k, (_, at) in _SCOPES.items() if now - at > SCOPE_TTL_SECONDS]:
        _SCOPES.pop(stale, None)
    if len(_SCOPES) >= SCOPE_MAX_ENTRIES:
        _SCOPES.pop(min(_SCOPES, key=lambda k: _SCOPES[k][1]), None)
    _SCOPES[key] = (intent, now)


def _recall_scope(key):
    entry = _SCOPES.get(key)
    if not entry or time.monotonic() - entry[1] > SCOPE_TTL_SECONDS:
        _SCOPES.pop(key, None)
        return None
    return entry[0]


DB_UNAVAILABLE = "The database is temporarily unavailable, so I can't answer right now. Please try again shortly."


def is_addressed(message, config):
    """Free text counts only when it is a reply to the bot or mentions it."""
    text_value = message.get("text") or ""
    if (message.get("chat") or {}).get("type") == "private":
        return True
    reply_from = (message.get("reply_to_message") or {}).get("from") or {}
    if reply_from.get("is_bot") and (
            not config.bot_username
            or (reply_from.get("username") or "").lower() == config.bot_username.lower()):
        return True
    if config.bot_username:
        return bool(re.search(rf"@{re.escape(config.bot_username)}\b", text_value, re.I))
    return bool(re.search(r"@\w*bot\b", text_value, re.I))


def reply_context(message):
    """Scope recovered only from the bot answer this user explicitly replied to."""
    reply = message.get("reply_to_message") or {}
    if not (reply.get("from") or {}).get("is_bot"):
        return None
    return context_from_reply(reply.get("text"))


def answer_intent(engine, intent, config, lang=DEFAULT_LANGUAGE):
    """Map a parsed Intent onto the read-only helpers. Returns (text, buttons).

    ``buttons`` is None (general navigation), False (no buttons), a Page (search follow-ups)
    or Choices (quick replies that fit this answer).
    """
    if intent.kind == "status":
        return status_message(engine, lang), None
    if intent.kind == "latest":
        return latest_message(engine, lang), None
    if intent.kind == "trends":
        return trends_message(engine, intent.days, lang), None
    if intent.kind == "find":
        offset = max(0, min(intent.offset, MAX_OFFSET))
        text_value, total, stats = find_page(engine, intent.filters, offset, lang)
        following = offset + PAGE_SIZE
        return text_value, Page(intent.filters, following if following < total and following <= MAX_OFFSET else None,
                                stats.get("min_year"), stats.get("max_km"))
    if intent.kind == "model_stats":
        label = intent.filters.search
        rows = ((t(lang, "btn_show_listings"), refine_callback(Filters(label))),)
        return model_stats_message(engine, label, lang), Choices((rows,))
    if intent.kind == "suggest":
        text_value, models = suggest_message(engine, intent.filters, lang, with_models=True)
        f = intent.filters
        buttons = tuple((f"🔎 {model[:24]}", refine_callback(Filters(model, f.budget, f.year_min, f.mileage_max)))
                        for model in models[:3])
        return text_value, (Choices((buttons,)) if buttons else False)
    if intent.kind == "compare":
        buttons = tuple((f"🔎 {label[:24]}", refine_callback(Filters(label))) for label in intent.labels)
        return compare_message(engine, " | ".join(intent.labels), lang), Choices((buttons,))
    if intent.kind in {"chitchat", "clarify", "unsupported"}:
        if intent.reply:
            text_value = intent.reply
        elif intent.kind == "chitchat":
            text_value = chitchat_message(lang)
        elif intent.kind == "clarify":
            text_value = clarify_message(intent.note, intent.question, lang)
        else:
            text_value = unsupported_message(intent.note, lang)
        if intent.kind == "clarify" and intent.note != "currency":
            return text_value, budget_choices(lang)
        if intent.kind == "chitchat":
            return text_value, False  # small talk ("thanks") needs no buttons
        return text_value, example_choices(lang)
    return unsupported_message(intent.note, lang), example_choices(lang)


_DEFAULT_LLM = object()
DB_KINDS = {"find", "model_stats", "compare", "trends", "status", "latest", "suggest"}

# Best-effort memory of the last few chat turns in a private chat (see SCOPE_* above): the
# same rules apply. Group chats keep no history.
HISTORY_MAX_TURNS = 6
_HISTORY = {}


def _remember_turn(key, user_text, bot_text):
    if not key:
        return
    now = time.monotonic()
    for stale in [k for k, (_, at) in _HISTORY.items() if now - at > SCOPE_TTL_SECONDS]:
        _HISTORY.pop(stale, None)
    if len(_HISTORY) >= SCOPE_MAX_ENTRIES and key not in _HISTORY:
        _HISTORY.pop(min(_HISTORY, key=lambda k: _HISTORY[k][1]), None)
    turns = list(_HISTORY.get(key, ([], 0))[0])
    turns += [{"role": "user", "text": (user_text or "")[:300]},
              {"role": "bot", "text": (bot_text or "")[:300]}]
    _HISTORY[key] = (turns[-HISTORY_MAX_TURNS:], now)


def _recall_history(key):
    entry = _HISTORY.get(key) if key else None
    if not entry or time.monotonic() - entry[1] > SCOPE_TTL_SECONDS:
        _HISTORY.pop(key, None)
        return []
    return list(entry[0])


def _resolve_llm(llm, phraser):
    """Default to Gemini from the environment; None means the built-in parser only."""
    llm_config = None
    if llm is _DEFAULT_LLM or phraser is _DEFAULT_LLM:
        llm_config = telegram_llm.LLMConfig.from_env()
    if llm is _DEFAULT_LLM:
        llm = (lambda message, previous, history=None, language="":
               telegram_llm.interpret(message, previous, llm_config, history=history, language=language)
               ) if llm_config else None
    if phraser is _DEFAULT_LLM:
        phraser = (lambda message, facts, history=None, mode="", language="":
                   telegram_llm.phrase(message, facts, llm_config, history=history, mode=mode, language=language)
                   ) if llm_config else None
    return llm, phraser


# Best-effort memory of the language each person writes in (same rules as the scope memory:
# in-process, per chat and user, bounded). Used for buttons and commands that carry no text.
LANGUAGE_TTL_SECONDS = 6 * 3600
_LANGS = {}


def default_language():
    value = (os.environ.get("TELEGRAM_DEFAULT_LANGUAGE") or "").strip().lower()
    return value if value in LANGUAGES else DEFAULT_LANGUAGE


def _remember_language(key, lang):
    if not key or lang not in LANGUAGES:
        return
    now = time.monotonic()
    for stale in [k for k, (_, at) in _LANGS.items() if now - at > LANGUAGE_TTL_SECONDS]:
        _LANGS.pop(stale, None)
    if len(_LANGS) >= SCOPE_MAX_ENTRIES and key not in _LANGS:
        _LANGS.pop(min(_LANGS, key=lambda k: _LANGS[k][1]), None)
    _LANGS[key] = (lang, now)


def resolve_language(scope_key, text_value, sender):
    """The language to answer in: what this message is written in, else what this person last
    wrote in, else their Telegram app language, else the configured default."""
    detected = detect_language(text_value)
    if detected:
        _remember_language(scope_key, detected)
        return detected
    entry = _LANGS.get(scope_key)
    if entry and time.monotonic() - entry[1] <= LANGUAGE_TTL_SECONDS:
        return entry[0]
    return telegram_language((sender or {}).get("language_code")) or default_language()


def _send_placeholder(call, config, chat_id, lang):
    """Send "looking…" right away and return its message id, so the answer can replace it."""
    try:
        result = call(config, "sendMessage", {"chat_id": chat_id, "text": t(lang, "looking"),
                                              "disable_web_page_preview": True})
    except TelegramAPIError:
        return None
    return result.get("message_id") if isinstance(result, dict) else None


def _deliver(call, config, chat_id, text_value, markup, placeholder_id):
    """Replace the placeholder with the answer; if that fails, send a new message instead."""
    payload = {"chat_id": chat_id, "text": text_value[:MAX_MESSAGE], "disable_web_page_preview": True}
    if markup:
        payload["reply_markup"] = markup
    if placeholder_id is not None:
        try:
            call(config, "editMessageText", {**payload, "message_id": placeholder_id})
            return
        except TelegramAPIError:
            try:
                call(config, "deleteMessage", {"chat_id": chat_id, "message_id": placeholder_id})
            except TelegramAPIError:
                pass
    call(config, "sendMessage", payload)


def process_update(update: dict[str, Any], engine, config: TelegramConfig, *, call=telegram_call,
                   llm=_DEFAULT_LLM, phraser=_DEFAULT_LLM):
    if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
        return {"handled": False, "reason": "invalid_update"}
    chat, user_id, message, callback = _identity(update)
    if not is_authorized(chat, user_id, config):
        return {"handled": False, "reason": "unauthorized"}
    chat_id = chat["id"]
    private = chat.get("type") == "private"
    scope_key = (chat_id, user_id)
    history_key = scope_key if private else None
    sender = callback.get("from") or message.get("from") or {}
    callback_data = callback.get("data") if callback else None
    intent = None
    free_text = None
    command, argument = "help", ""
    llm, phraser = _resolve_llm(llm, phraser)
    if callback_data:
        refine = parse_refine_page(callback_data)
        suggest_budget = parse_suggest_callback(callback_data)
        if refine:
            intent = Intent("find", refine[0], offset=refine[1])
            command = "find"
        elif suggest_budget:
            intent = Intent("suggest", Filters(budget=suggest_budget))
            command = "suggest"
        elif callback_data.startswith("trends:"):
            command, argument = "trends", callback_data.partition(":")[2]
        elif callback_data in {"status", "latest"}:
            command, argument = callback_data, ""
        call(config, "answerCallbackQuery", {"callback_query_id": callback.get("id")})
    else:
        body = message.get("text")
        if not isinstance(body, str) or not body.strip():
            return {"handled": False, "reason": "no_text"}
        if body.lstrip().startswith("/"):
            command, argument = command_for(body)
            if command == "llm":
                intent = Intent("llm_check")
            elif command == "ask":
                free_text = argument
        elif is_addressed(message, config):
            free_text = body
        else:
            return {"handled": False, "reason": "not_addressed"}
    lang = resolve_language(scope_key, free_text, sender)
    placeholder_id = None
    if free_text is not None:
        if llm is not None or phraser is not None:
            placeholder_id = _send_placeholder(call, config, chat_id, lang)
        intent = _understand(free_text, message, private and scope_key, llm, _recall_history(history_key), lang)
        command = intent.kind
    if intent is not None and intent.kind == "ignore":
        if placeholder_id is not None:
            try:
                call(config, "deleteMessage", {"chat_id": chat_id, "message_id": placeholder_id})
            except TelegramAPIError:
                pass
        return {"handled": False, "reason": "acknowledgement"}
    markup = None
    chat_text = None
    try:
        if intent is not None and intent.kind == "llm_check":
            response = telegram_llm.check_message(telegram_llm.LLMConfig.from_env())
            command, markup = "llm", False
        elif intent is not None:
            response, markup = answer_intent(engine, intent, config, lang=lang)
            if private:
                _remember_scope(scope_key, intent)
            if free_text is not None and phraser is not None and intent.kind in DB_KINDS:
                phrased = phraser(free_text, response, _recall_history(history_key), mode=intent.kind,
                                  language=lang)
                if phrased:
                    chat_text, show_details = phrased
                    show_details = show_details or intent.kind == "suggest"  # the user wants to see them
                    response = chat_text + ("\n\n" + response if show_details else "")
                    if intent.kind == "find" and not show_details and isinstance(markup, Page):
                        # the listings are not on screen, so the first button should show page one
                        markup = Page(markup.filters, 0, markup.min_year, markup.max_km)
            elif free_text is not None and intent.reply:
                chat_text = intent.reply
        else:
            response = render_command(engine, command, argument, lang)
            if command == "start":
                markup = example_choices(lang)
    except SQLAlchemyError:
        response, markup = t(lang, "db_unavailable"), False
    _deliver(call, config, chat_id, response, keyboard(config, markup, lang), placeholder_id)
    if free_text is not None:
        _remember_turn(history_key, free_text, chat_text or response.split("\n", 1)[0])
    return {"handled": True, "command": command}


def _needs_interpreter(intent, text_value):
    """True when Gemini should decide: unreadable, conversational, or a bare search word.

    A message with a price, year or mileage is a clear search the free parser reads itself.
    A search-only message ("golf", "hvala ti", "bok") is ambiguous, so Gemini picks between
    a search and small talk. Without Gemini the parser's reading is used as before.
    """
    if intent.kind in {"ignore", "clarify", "chitchat", "unsupported"}:
        return not intent.note
    return intent.kind == "find" and not (
        intent.filters.budget or intent.filters.year_min or intent.filters.mileage_max)


def _understand(text_value, message, scope_key, llm, history=None, lang=DEFAULT_LANGUAGE):
    """Deterministic parse first; Gemini for anything conversational or unreadable."""
    context = reply_context(message)
    remembered = _recall_scope(scope_key) if scope_key else None
    if context is None and remembered is not None and looks_like_followup(text_value):
        context = remembered
    intent = parse_question(text_value, context)
    if llm is not None and _needs_interpreter(intent, text_value):
        interpreted = llm(text_value, context or remembered, history or [], language=lang)
        if interpreted is not None:
            return interpreted
    return intent
