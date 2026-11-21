"""Private Telegram interface backed only by Car Analyzer database evidence."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import difflib
import html
import time
import hmac
import logging
import os
import re
from typing import Any
from urllib.parse import quote as urlquote, urlparse
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
    MAKE_MODELS,
    _TITLES,
    alias_pattern,
    model_hint,
    strip_mentions,
    TITLE_BUDGET,
    TITLE_FIND,
    YEAR_RANGE,
    clean_search,
    context_from_reply,
    is_correction,
    stated_filters,
    wants_recommendation,
    fold,
    hint_patterns,
    looks_like_followup,
    parse_question,
    split_trim,
)
from database.run_outcome import classify as run_outcome
import telegram_llm
import telegram_log


LOG = logging.getLogger("telegram_bot")
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
    extra_chat_ids: frozenset[int] = frozenset()

    @property
    def allowed_chats(self):
        return frozenset({self.chat_id}) | self.extra_chat_ids

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
            chat_ids = [int(value) for value in required["TELEGRAM_CHAT_ID"].split(",") if value.strip()]
            if not chat_ids:
                raise ValueError("no chat id")
            chat_id = chat_ids[0]
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
            extra_chat_ids=frozenset(chat_ids[1:]) - {chat_id},
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
            details_completed,observations,new_listings,errors,report
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
    quality = (t(lang, "status_budget_limited") if run_outcome(run) == "budget_limited"
               else status_label(lang, run["status"]))
    lines = [
        t(lang, "status_title", quality=quality),
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
    aliased = alias_pattern(value)  # "Mercedes C klasa" -> listings stored as "Mercedes-Benz C 220", ...
    if aliased:
        return aliased
    folded = fold(value).strip()
    escaped = folded.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if re.fullmatch(r"[a-z]{1,3}", folded) and folded not in MAKE_MODELS and folded not in SHORT_MAKES:
        # "rs", "su", "up", "ka": a whole word at the start of the model ("Volkswagen Up", "Audi RS3"), never
        # letters inside another name ("Opel Corsa", "Škoda Superb")
        return f"% {escaped}%"
    return f"%{escaped}%"


SHORT_MAKES = frozenset({"vw", "kia", "bmw", "mg", "ds", "uaz", "gaz"})  # a make starts the label: any position


def _cohort_stats(connection, label, year_min=None, year_max=None):
    """Asking-price summary for one model (optionally within model years). New cars at under 1.000 km
    are left out: they are dealer stock, not used-car prices."""
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
        AND (CAST(:year_min AS integer) IS NULL OR c.year>=CAST(:year_min AS integer))
        AND (CAST(:year_max AS integer) IS NULL OR c.year<=CAST(:year_max AS integer))
        AND NOT coalesce({_NEW_LIKE.format(p='c')}, false)
    """, {"pattern": _like_pattern(label), "year_min": year_min, "year_max": year_max})


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


COMPARE_SPREAD = (.1, .9)  # a model's "usual" model years: the 10th to 90th percentile of its listings
COMPARE_SPREAD_MIN = 10  # listings with a model year a model needs before its usual years are trusted
COMPARE_BAND_MIN = 3  # priced listings a model needs in a year band before its median is shown
COMPARE_MAX_BANDS = 4


def _year_spread(connection, search):
    """(low, high) usual model years of a model's recent used listings, or None."""
    row = _one(connection, f"""
      SELECT count(*) AS n, percentile_disc({COMPARE_SPREAD[0]}) WITHIN GROUP (ORDER BY c.year) AS low,
        percentile_disc({COMPARE_SPREAD[1]}) WITHIN GROUP (ORDER BY c.year) AS high
      FROM car_listings c LEFT JOIN dealer_vehicle_labels l USING(listing_id)
      WHERE {_RECENT_WHERE} AND c.year IS NOT NULL
        AND {_LABEL_SQL} LIKE :pattern ESCAPE '\\'
        AND NOT coalesce({_NEW_LIKE.format(p='c')}, false)
    """, {"pattern": _like_pattern(search)})
    if not row or row["low"] is None or row["n"] < COMPARE_SPREAD_MIN:
        return None  # too few listings to say which years are usual: compare all years, as before
    return int(row["low"]), int(row["high"])


def _band_medians(connection, search, start, end, width):
    """{band index: (priced listings, median)} for model years start..end in bands of ``width`` years."""
    rows = _all(connection, f"""
      SELECT (c.year - CAST(:start AS integer)) / CAST(:width AS integer) AS band, count(*) AS n,
        percentile_cont(.5) WITHIN GROUP (ORDER BY c.price) AS median
      FROM car_listings c LEFT JOIN dealer_vehicle_labels l USING(listing_id)
      WHERE {_RECENT_WHERE}
        AND c.year BETWEEN CAST(:start AS integer) AND CAST(:end AS integer)
        AND {_LABEL_SQL} LIKE :pattern ESCAPE '\\'
        AND NOT coalesce({_NEW_LIKE.format(p='c')}, false)
      GROUP BY 1
    """, {"pattern": _like_pattern(search), "start": start, "end": end, "width": width})
    return {int(row["band"]): (int(row["n"]), row["median"]) for row in rows}


def _years_text(low, high, lang):
    if low and high and low == high:
        return str(low)
    return f"{low}–{high or t(lang, 'today_word')}"


def _compare_years(spans, user, spreads):
    """Pick the model years each cohort is measured in, so the two medians compare like with like.

    ``spans`` are the generation years each label names ("golf 7" -> 2012-2020) or (None, None);
    ``user`` the years the message gave; ``spreads`` each model's usual years in the data.
    Returns ([(low, high)] per label, how, which label's generation was used).
    """
    if user[0] or user[1]:
        chosen = []
        for low, high in spans:  # within a named generation, keep to its years
            lo = max(filter(None, (user[0], low)), default=None)
            hi = min(filter(None, (user[1], high)), default=None)
            chosen.append((lo, hi) if not (lo and hi and lo > hi) else user)
        return chosen, "user", None
    named = [index for index, span in enumerate(spans) if span[0]]
    if len(named) == 2:
        return list(spans), "generations", None  # "Golf 7 vs Octavia 3": each in its own generation
    if len(named) == 1:
        return [spans[named[0]]] * 2, "generation", named[0]  # "Golf 7 vs Passat": Passats of those years
    if all(spreads):
        low, high = max(s[0] for s in spreads), min(s[1] for s in spreads)
        if low <= high:
            return [(low, high)] * 2, "overlap", None
    return [(None, None)] * 2, "none", None


def _family_models(searches):
    """The searches (model names) that are on the short family list."""
    return [search for search in searches
            if any(re.search(rf"\b{re.escape(name)}\b", fold(search)) for name in FAMILY_MODELS)]


def compare(engine, argument, lang=DEFAULT_LANGUAGE, year_min=None, year_max=None, family=False):
    """Compare two models' asking prices in the same model years. Returns (text, [(search, low, high)]).

    The years are the ones the user gave; otherwise a named generation's; otherwise the years both
    models have in the data. The answer says which years were compared and, when the span is wide,
    shows the medians per band of model years as well.
    """
    labels = [value.strip() for value in re.split(r"\s*(?:\||\bvs\.?\b)\s*", argument, flags=re.I) if value.strip()]
    if len(labels) != 2:
        return t(lang, "compare_usage"), []
    with engine.begin() as connection:
        _statement_timeout(connection)
        searches, spans = [], []
        for label in labels:
            search, low, high = model_hint(label)  # "golf 7" -> golf, 2012-2020
            search = search or label
            if not _is_known_model(connection, search):
                search = resolve_search(_model_labels(connection), search)[0] or search
            searches.append(search)
            spans.append((low, high))
        user = (year_min, year_max)
        spreads = ([_year_spread(connection, search) for search in searches]
                   if not (any(user) or any(low for low, _ in spans)) else [None, None])
        chosen, how, generation_of = _compare_years(spans, user, spreads)
        results = [_cohort_stats(connection, search, low, high) for search, (low, high) in zip(searches, chosen)]
        bands = []
        low, high = chosen[0]
        if how != "generations" and low:
            high = high or datetime.now(timezone.utc).year
            width = max(2, -(-(high - low + 1) // COMPARE_MAX_BANDS))
            if high - low + 1 > width:
                per_model = [_band_medians(connection, search, low, high, width) for search in searches]
                for band in range((high - low) // width + 1):
                    start = low + band * width
                    bands.append(((start, min(start + width - 1, high)), [m.get(band, (0, None)) for m in per_model]))
    lines = [t(lang, "compare_title")]
    if how == "user":
        lines.append(t(lang, "compare_years_user", years=_years_text(*user, lang)))
    elif how == "overlap":
        lines.append(t(lang, "compare_years_overlap", years=_years_text(*chosen[0], lang)))
    elif how == "generation":
        lines.append(t(lang, "compare_years_generation", years=_years_text(*chosen[0], lang),
                       label=labels[generation_of]))
    elif how == "none" and any(spreads):
        lines.append(t(lang, "compare_years_none"))
    newest = None
    for label, (low, high), result in zip(labels, chosen, results):
        newest = max(filter(None, (newest, _utc(result["newest"]))), default=None)
        shown = f"{label} ({_years_text(low, high, lang)})" if low else label
        lines.extend(["", *_cohort_lines(shown, result, lang)])
    if bands:
        lines.extend(["", t(lang, "compare_bands_h")])
        for (start, end), cells in bands:
            parts = [f"{label} {_money(median, lang) if n >= COMPARE_BAND_MIN else '–'} ({n})"
                     for label, (n, median) in zip(labels, cells)]
            lines.append(t(lang, "compare_band_row", years=_years_text(start, end, lang), cells=" · ".join(parts)))
    if family:  # "za porodicu": say which model is on the family list, and that size is not in the data
        listed = _family_models(searches)
        key = "compare_family_both" if len(listed) == 2 else "compare_family_some" if listed else "compare_family_none"
        lines.extend(["", t(lang, key, names=", ".join(listed))])
    note = "compare_note" if how in {"generations", "none"} else "compare_note_years"
    lines.extend(["", t(lang, "fresh_line", when=_time(newest, lang)), t(lang, note)])
    return "\n".join(lines), [(search, low, high) for search, (low, high) in zip(searches, chosen)]


def compare_message(engine, argument, lang=DEFAULT_LANGUAGE, year_min=None, year_max=None):
    return compare(engine, argument, lang, year_min, year_max)[0]


def model_stats_message(engine, label, lang=DEFAULT_LANGUAGE):
    search, year_min, year_max = model_hint(label)
    with engine.begin() as connection:
        _statement_timeout(connection)
        search, notes, unknown = _stats_search(connection, search or label, lang)
        result = None if unknown else _cohort_stats(connection, search, year_min, year_max)
        note = _freshness_note(connection, lang)
    lines = [t(lang, "stats_title", label=label), ""]
    lines.extend(notes)
    if unknown or not result["sample_size"]:
        lines.append(t(lang, "no_match_90"))
        if unknown:
            lines.append(t(lang, "stats_unknown", term=unknown))
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
      SELECT status,errors,report FROM website_collection_runs
      WHERE finished_at IS NOT NULL ORDER BY started_at DESC,id DESC LIMIT 1
    """)
    if latest is None:
        return t(lang, "fresh_none")
    notes = []
    age = datetime.now(timezone.utc) - _utc(latest)
    if age.total_seconds() > STALE_AFTER_HOURS * 3600:
        notes.append(t(lang, "fresh_stale", days=age.days, when=_time(latest, lang)))
    if run and run_outcome(run) not in {"ok", "budget_limited"}:
        notes.append(t(lang, "fresh_run", status=status_label(lang, run["status"])))
    return " ".join(notes)


def _bound_parts(filters, lang=DEFAULT_LANGUAGE):
    """Title pieces for the price, year and mileage bounds: "50.000–60.000 KM", "2022–2024", "do 60.000 KM".

    Every answer's title is built from these, so the title always states exactly the bounds that
    were queried (and ``telegram_intent.context_from_reply`` can read them back from a reply)."""
    parts = []
    if filters.price_min and filters.budget:
        parts.append(f"{_thousands(filters.price_min)}–{_money(filters.budget, lang)}")
    elif filters.budget:
        parts.append(t(lang, "title_upto", x=_money(filters.budget, lang)))
    elif filters.price_min:
        parts.append(t(lang, "title_from", x=_money(filters.price_min, lang)))
    if filters.year_min and filters.year_max and filters.year_min == filters.year_max:
        parts.append(str(filters.year_min))  # "golf 15": that model year only
    elif filters.year_min and filters.year_max:
        parts.append(f"{filters.year_min}–{filters.year_max}")
    elif filters.year_min:
        parts.append(t(lang, "title_from", x=filters.year_min))
    elif filters.year_max:
        parts.append(t(lang, "title_upto", x=filters.year_max))
    if filters.mileage_min and filters.mileage_max:
        parts.append(f"{_thousands(filters.mileage_min)}–{_thousands(filters.mileage_max)} km")
    elif filters.mileage_max:
        parts.append(t(lang, "title_upto", x=f"{_thousands(filters.mileage_max)} km"))
    elif filters.mileage_min:
        parts.append(t(lang, "title_from", x=f"{_thousands(filters.mileage_min)} km"))
    return parts


def _titles(lang):
    return _TITLES[1] if lang == "bs" else _TITLES[0]


def _attr_parts(filters, lang=DEFAULT_LANGUAGE):
    """Title pieces for fuel, gearbox and "incl. new" (read back by telegram_intent._title_filters)."""
    titles = _titles(lang)
    parts = []
    if filters.fuel in titles["fuel"]:
        parts.append(titles["fuel"][filters.fuel])
    if filters.transmission in titles["gearbox"]:
        parts.append(titles["gearbox"][filters.transmission])
    if filters.new_ok:
        parts.append(titles["new"])
    if filters.low_km:
        parts.append(titles["lowkm"])
    if filters.cheapest:
        parts.append(titles["cheapest"])
    return parts


def _filter_title(filters, lang=DEFAULT_LANGUAGE):
    parts = [t(lang, "find_title")]
    if filters.search:
        parts.append(filters.search)
    parts.extend(_attr_parts(filters, lang))
    parts.extend(_bound_parts(filters, lang))
    if filters.family:
        parts.append(t(lang, "title_family"))
    if filters.include_old:
        parts.append(t(lang, "title_old"))
    return " · ".join(parts)


class Html(str):
    """A reply already written in Telegram HTML (sent with parse_mode=HTML). Plain ``str`` replies
    are sent as plain text, exactly as before."""


def esc(value):
    """Escape a value for Telegram HTML (text and attribute values)."""
    return html.escape(str(value), quote=True)


def html_to_text(value, keep_links=False):
    """Plain text of a Telegram HTML reply: tags removed, entities decoded. Link targets are dropped
    (so no URL reaches Gemini) unless ``keep_links`` appends them as " (url)"."""
    value = str(value)
    if keep_links:
        value = re.sub(r'<a href="([^"]*)">(.*?)</a>', r"\2 (\1)", value, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", "", value))


def _listing_url(listing_id):
    return f"https://olx.ba/artikal/{urlquote(str(listing_id), safe='')}"


def _car_link(label, listing_id):
    return f'<a href="{esc(_listing_url(listing_id))}"><b>{esc(label)}</b></a>'


PAGE_SIZE = 5
MAX_OFFSET = 95

# "Za porodicu": there is no body-type column, so a family search keeps to a short, curated list of
# roomier models (estates, MPVs, SUVs, larger hatchbacks). Matched as whole words on the folded
# make+model label. This narrows the search; it never ranks one car above another.
FAMILY_MODELS = (
    "octavia", "superb", "kodiaq", "karoq", "yeti", "roomster", "passat", "touran", "sharan", "tiguan",
    "caddy", "golf plus", "sportsvan", "variant", "alhambra", "ateca", "tarraco", "zafira", "meriva",
    "insignia", "mondeo", "s-max", "galaxy", "c-max", "kuga", "scenic", "espace", "kadjar", "koleos",
    "3008", "5008", "sw", "partner", "rifter", "berlingo", "picasso", "spacetourer", "c5", "duster",
    "lodgy", "jogger", "mcv", "rav4", "verso", "avensis", "tucson", "ix35", "santa fe", "i40",
    "sportage", "sorento", "carens", "qashqai", "x-trail", "cx-5", "mazda 6", "cr-v", "xc60", "xc90",
    "v60", "v70", "v90", "xc40", "a6", "avant", "q3", "q5", "q7", "x1", "x3", "x5", "glc", "ml",
    "vito", "viano", "doblo", "vitara", "outlander", "forester", "outback", "kombi", "karavan", "touring",
)
FAMILY_REGEX = r"\m(" + "|".join(re.escape(name).replace("\\ ", " ") for name in FAMILY_MODELS) + r")\M"
FAMILY_EXAMPLES = "Octavia, Passat, Touran, Sportage"

# Plausibility: conservative checks that DEMOTE and FLAG a listing, never hide it or change data.
#  * a recent car (<= 5 model years old) with more than 80.000 km per year of age;
#  * a model year far newer than the model's usual years (> 8 years above its 90th percentile,
#    with at least 8 dated listings), e.g. a "2022" Mercedes E210 (built 1995-2003);
#  * a price under 35% of the median for the same model and year (at least 5 listings).
SUSPECT_KM_PER_YEAR = 80_000
SUSPECT_RECENT_YEARS = 5
SUSPECT_YEAR_GAP = 8
SUSPECT_MIN_MODEL = 8
SUSPECT_PRICE_SHARE = 0.35
SUSPECT_MIN_COHORT = 5
_THIS_YEAR = "CAST(extract(year FROM (now() AT TIME ZONE 'UTC')) AS integer)"
_KM_SUSPECT = (f"({{p}}.year >= {_THIS_YEAR} - {SUSPECT_RECENT_YEARS} AND {{p}}.mileage IS NOT NULL"
               f" AND {{p}}.mileage > {SUSPECT_KM_PER_YEAR} * greatest({_THIS_YEAR} - {{p}}.year, 1))")
# Mileage above 999.999 km is a typo or a placeholder, so it is treated as unknown everywhere (rows,
# medians, buttons). A car from last year or newer with under 1.000 km is a new (dealer) car: it is left
# out of used-car lists, medians and deals unless the user asks for new cars. (Last year is included
# because a 2025 car at 1 km in late 2026 is still unsold dealer stock; the sweep found ~5% such ads.)
MAX_PLAUSIBLE_KM = 999_999
NEW_CAR_MAX_KM = 999
_KM = f"CASE WHEN c.mileage BETWEEN 0 AND {MAX_PLAUSIBLE_KM} THEN c.mileage END"
_NEW_LIKE = f"({{p}}.year >= {_THIS_YEAR} - 1 AND {{p}}.mileage BETWEEN 0 AND {NEW_CAR_MAX_KM})"
_ATTR_WHERE = ("""(CAST(:fuel AS text) = '' OR {p}.fuel_type = CAST(:fuel AS text)
              OR (CAST(:fuel AS text) = 'lpg' AND {p}.fuel_type IN ('lpg','gas')))
            AND (CAST(:transmission AS text) = '' OR {p}.transmission = CAST(:transmission AS text))""")

_FIND_SQL = f"""
  WITH base AS (
    SELECT c.listing_id,coalesce(l.make,c.make) AS make,coalesce(l.model,c.model) AS model,
      c.year,c.price,{_KM} AS mileage,c.last_verified_at,{_LABEL_SQL} AS folded,c.fuel_type,c.transmission,
      c.location,
      CASE WHEN cardinality(CAST(:hints AS text[])) > 0
        THEN lower(concat_ws(' ', {_LABEL_SQL}, c.description)) ~* ALL (CAST(:hints AS text[]))
        ELSE false END AS hint_hit
    FROM car_listings c
    LEFT JOIN dealer_vehicle_labels l USING(listing_id)
    WHERE c.source='olx.ba'
      AND c.collection_method IN ('olx_api','olx_website')
      AND c.price_status='priced' AND c.price>0
      AND c.last_verified_at >= (now() AT TIME ZONE 'UTC') - interval '90 days'
  ), matched AS (
    SELECT * FROM base b
    WHERE (CAST(:budget AS integer) IS NULL OR b.price<=CAST(:budget AS integer))
      AND (CAST(:price_min AS integer) IS NULL OR b.price>=CAST(:price_min AS integer))
      AND (CAST(:year_min AS integer) IS NULL OR b.year>=CAST(:year_min AS integer))
      AND (CAST(:year_max AS integer) IS NULL OR b.year<=CAST(:year_max AS integer))
      AND (CAST(:mileage_max AS integer) IS NULL OR b.mileage<=CAST(:mileage_max AS integer))
      AND (CAST(:mileage_min AS integer) IS NULL OR b.mileage>=CAST(:mileage_min AS integer))
      AND (:search='' OR b.folded LIKE :pattern ESCAPE '\\')
      AND (NOT CAST(:family AS boolean) OR b.folded ~ :family_re)
      AND {_ATTR_WHERE.format(p='b')}
  ), model_years AS (
    SELECT folded,count(year) AS n,percentile_cont(.9) WITHIN GROUP (ORDER BY year) AS p90_year
    FROM base WHERE year IS NOT NULL AND folded IN (SELECT folded FROM matched) GROUP BY folded
  ), model_year_prices AS (
    SELECT folded,year,count(*) AS n,percentile_cont(.5) WITHIN GROUP (ORDER BY price) AS median
    FROM base WHERE year IS NOT NULL AND folded IN (SELECT folded FROM matched) GROUP BY folded,year
  ), scored AS (
    SELECT m.*,
      (m.last_verified_at >= (now() AT TIME ZONE 'UTC') - interval '{FRESH_DAYS} days') AS fresh,
      ({_KM_SUSPECT.format(p='m')}
        OR (y.n >= {SUSPECT_MIN_MODEL} AND m.year > y.p90_year + {SUSPECT_YEAR_GAP})
        OR (p.n >= {SUSPECT_MIN_COHORT} AND m.price < {SUSPECT_PRICE_SHARE} * p.median)) AS suspect,
      (CAST(:new_ok AS boolean) OR NOT coalesce({_NEW_LIKE.format(p='m')}, false)) AS keep
    FROM matched m
    LEFT JOIN model_years y ON y.folded=m.folded
    LEFT JOIN model_year_prices p ON p.folded=m.folded AND p.year=m.year
  ), counted AS (
    SELECT *,count(*) FILTER (WHERE fresh AND keep) OVER () AS fresh_total,
      count(*) FILTER (WHERE keep) OVER () AS all_total,
      count(*) FILTER (WHERE NOT keep) OVER () AS new_hidden,
      count(*) FILTER (WHERE hint_hit AND keep AND (fresh OR CAST(:include_old AS boolean))) OVER () AS hint_total
    FROM scored
  )
  SELECT listing_id,make,model,year,price,mileage,last_verified_at,fresh,coalesce(suspect,false) AS suspect,
    fuel_type,transmission,location,fresh_total,all_total,new_hidden,hint_hit,hint_total,
    CASE WHEN CAST(:include_old AS boolean) THEN all_total ELSE fresh_total END AS total
  FROM counted
  WHERE (keep OR CAST(:probe_new AS boolean)) AND (CAST(:include_old AS boolean) OR fresh OR CAST(:probe AS boolean))
  ORDER BY coalesce(suspect,false) ASC,
    coalesce(hint_hit,false) DESC,
    CASE WHEN CAST(:low_km AS boolean) AND mileage >= 1000 THEN mileage END ASC NULLS LAST,
    CASE WHEN :search='' AND CAST(:new_ok AS boolean) THEN NOT coalesce({_NEW_LIKE.format(p='counted')}, false) END ASC,
    CASE WHEN :search='' THEN price END ASC NULLS LAST,
    CASE WHEN CAST(:cheapest AS boolean) THEN price END ASC NULLS LAST,
    date(last_verified_at) DESC,
    year DESC NULLS LAST,
    mileage ASC NULLS LAST,last_verified_at DESC,listing_id
  LIMIT CAST(:limit AS integer) OFFSET CAST(:offset AS integer)
"""


@dataclass(frozen=True)
class Page:
    """What the keyboard needs to know about a find answer."""
    filters: Filters
    next_offset: int | None = None
    min_year: int | None = None
    max_km: int | None = None
    items: tuple = ()  # (listing_id, year, price) of the cars shown, for the save buttons
    older_hidden: int = 0  # matches last seen 14-90 days ago that were left out
    total: int = 0  # all matches (for the interaction log)
    empty: bool = False  # the search found nothing: offer no narrowing buttons


@dataclass(frozen=True)
class Choices:
    """Quick-reply buttons for an answer: rows of (label, callback_data)."""
    rows: tuple


def _new_ok(filters):
    """New cars stay in when asked for, or when the user asks for this year's cars anyway."""
    return bool(filters.new_ok or (filters.year_min and filters.year_min >= datetime.now(timezone.utc).year))


def _bound_params(filters):
    """SQL parameters for the price, year and mileage bounds (both ends of a range), fuel, gearbox and
    whether new cars stay in."""
    return {"budget": filters.budget, "price_min": filters.price_min, "year_min": filters.year_min,
            "year_max": filters.year_max, "mileage_max": filters.mileage_max,
            "mileage_min": filters.mileage_min, "low_km": bool(filters.low_km), "fuel": filters.fuel or "",
            "transmission": filters.transmission or "", "new_ok": _new_ok(filters),
            "cheapest": bool(filters.cheapest)}


def _find_rows(connection, filters, offset, probe=False, probe_new=False, hints=()):
    """One page of matches. ``probe`` ignores the freshness cut to learn how many older ones exist;
    ``probe_new`` also counts the new cars that are normally left out. ``hints`` (engine size, trim) put the
    listings that mention them first; they never filter."""
    return _all(connection, _FIND_SQL, {
        **_bound_params(filters),
        "hints": hint_patterns(hints),
        "probe_new": bool(probe_new),
        "search": filters.search,
        "pattern": _like_pattern(filters.search),
        "family": bool(filters.family),
        "family_re": FAMILY_REGEX,
        "include_old": bool(filters.include_old),
        "probe": bool(probe),
        "limit": 1 if probe else PAGE_SIZE,
        "offset": 0 if probe else offset,
    })


def _is_known_model(connection, search):
    """True when any recent listing's make/model contains ``search`` (so it is a real car name)."""
    return connection.execute(text(f"""
      SELECT 1 FROM car_listings c LEFT JOIN dealer_vehicle_labels l USING(listing_id)
      WHERE c.source='olx.ba' AND c.collection_method IN ('olx_api','olx_website')
        AND c.last_verified_at >= (now() AT TIME ZONE 'UTC') - interval '90 days'
        AND {_LABEL_SQL} LIKE :pattern ESCAPE '\\' LIMIT 1
    """), {"pattern": _like_pattern(search)}).scalar() is not None


def _model_labels(connection):
    """[(folded label, display label, listings)] for every make/model seen in the last 90 days."""
    return [(row["folded"], row["label"], int(row["n"])) for row in _all(connection, f"""
      SELECT {_LABEL_SQL} AS folded, min({_BASE_LABEL}) AS label, count(*) AS n
      FROM car_listings c LEFT JOIN dealer_vehicle_labels l USING(listing_id)
      WHERE c.source='olx.ba' AND c.collection_method IN ('olx_api','olx_website')
        AND c.last_verified_at >= (now() AT TIME ZONE 'UTC') - interval '90 days'
      GROUP BY 1 HAVING {_LABEL_SQL} <> ''
    """)]


SEARCH_TYPO_CUTOFF = 0.8
SEARCH_SUGGEST_CUTOFF = 0.65  # "did you mean" only for near misses: 0.6 offered Scenic / Stonic for "nice"
SEARCH_MAX_CANDIDATES = 3


def _words(value):
    return [word for word in re.split(r"[^a-z0-9]+", fold(value)) if word]


def resolve_search(labels, search):
    """Find what an unrecognised search most likely means, using only names that exist in the data.

    ``labels`` is ``_model_labels()``. Returns (search to use or None, candidate display labels).
    Tries, in order: typo-corrected words ("golff" -> "golf", "pasat" -> "passat"), then fewer words
    (longest first, e.g. "golf 15" -> "golf", "superb skoda" -> "superb"). Never falls back to every
    model: when nothing matches, the caller asks "did you mean ...?" with the closest real names.
    """
    folded_labels = [label for label, _, _ in labels]
    vocabulary = sorted({word for label in folded_labels for word in _words(label) if len(word) > 1})
    known = lambda phrase: any(phrase in label for label in folded_labels)  # noqa: E731 (same as LIKE)
    words = _words(search)
    if not words:
        return None, []
    fixed = []
    for word in words:
        if word in vocabulary or word.isdigit() or len(word) < 4:
            fixed.append(word)
        else:
            close = difflib.get_close_matches(word, vocabulary, n=1, cutoff=SEARCH_TYPO_CUTOFF)
            fixed.append(close[0] if close else word)
    tries = []
    for candidate_words in (fixed, words):
        for size in range(len(candidate_words), 0, -1):
            for index in range(0, len(candidate_words) - size + 1):
                window = candidate_words[index:index + size]
                if all(w.isdigit() or len(w) < 2 for w in window):
                    continue
                if size == 1 and (len(window[0]) < 3 and not re.search(r"[0-9]", window[0])):
                    continue
                tries.append(" ".join(window))
    if len(words) == 2:
        tries.insert(1, f"{fixed[1]} {fixed[0]}")
    for phrase in tries:
        if phrase != fold(search).strip() and known(phrase):
            return phrase, []
    by_count = {label: (display, n) for label, display, n in labels}
    scored = {}
    for word in fixed:
        if word.isdigit() or len(word) < 4:
            continue  # "5" is not a hint for BMW X5 / Audi S5
        for close in difflib.get_close_matches(word, [name for name in vocabulary if len(name) >= 4], n=3,
                                               cutoff=SEARCH_SUGGEST_CUTOFF):
            for label in folded_labels:
                if close in _words(label):
                    scored[label] = max(scored.get(label, 0), by_count[label][1])
    for close in difflib.get_close_matches(fold(search), folded_labels, n=3, cutoff=SEARCH_SUGGEST_CUTOFF):
        scored[close] = max(scored.get(close, 0), by_count[close][1])
    ranked = sorted(scored, key=lambda label: (-scored[label], label))[:SEARCH_MAX_CANDIDATES]
    return None, [by_count[label][0] for label in ranked]


def _assumed_detail(labels, term, resolved, lang):
    """Why a dropped word cannot be searched: how the ads name the model that was found ("Tesla Model" for
    "tesla 3"). Empty unless the search only lost a model number or letter: a stray word like "grad" is not a
    model the ads could be naming."""
    kept = _words(resolved)
    dropped = [word for word in _words(term) if word not in kept]
    if not dropped or any(word not in _words(term) for word in kept) or any(len(word) > 2 for word in dropped):
        return ""
    names = [display for folded, display, _ in sorted(labels, key=lambda row: -row[2]) if resolved in folded][:2]
    if not names:
        return ""
    return t(lang, "assumed_detail", labels=" / ".join(names), dropped=" ".join(dropped))


def _stats_search(connection, search, lang):
    """The model search for a model-level figure (typical price, mileage): engine / trim hints are set aside
    and an unknown name is repaired like a listing search. Returns (search, notes, unknown term or "")."""
    model, hints = split_trim(search)
    notes = []
    if hints:
        notes.append(t(lang, "stats_hints", hints=" ".join(hints), model=model))
    if model and not _is_known_model(connection, model):
        labels = _model_labels(connection)
        resolved, candidates = resolve_search(labels, model)
        if resolved is None:
            if candidates:
                notes.append(t(lang, "did_you_mean", names=_names_list(candidates, lang)))
            return model, notes, model
        notes.append(t(lang, "stats_assumed", term=model, model=resolved))
        detail = _assumed_detail(labels, model, resolved, lang)
        if detail:
            notes.append(detail)
        model = resolved
    return model, notes, ""


def _has_filters(filters):
    return bool(filters.numeric() or filters.family or filters.attributes())


def _real_sql():
    return f"""
  SELECT count(*) AS n, percentile_cont(.5) WITHIN GROUP (ORDER BY c.price) AS median,
    min(c.price) AS low, max(c.price) AS high
  FROM car_listings c LEFT JOIN dealer_vehicle_labels l USING(listing_id)
  WHERE {_RECENT_WHERE} AND {_BOUNDS_WHERE.format(p='c')}
    AND (CAST(:search AS text) = '' OR {_LABEL_SQL} LIKE :pattern ESCAPE '\\')
    AND (NOT CAST(:family AS boolean) OR {_LABEL_SQL} ~ :family_re)
"""


# The cheapest real match, ignoring placeholder prices (under SUSPECT_PRICE_SHARE of the median) and
# implausible mileage, so "1 KM" ads are never offered as the answer.
def _cheapest_sql():
    return f"""
  SELECT c.listing_id, coalesce(l.make,c.make) AS make, coalesce(l.model,c.model) AS model, c.year, c.price,
    {_KM} AS mileage
  FROM car_listings c LEFT JOIN dealer_vehicle_labels l USING(listing_id)
  WHERE {_RECENT_WHERE} AND {_BOUNDS_WHERE.format(p='c')}
    AND (CAST(:search AS text) = '' OR {_LABEL_SQL} LIKE :pattern ESCAPE '\\')
    AND (NOT CAST(:family AS boolean) OR {_LABEL_SQL} ~ :family_re)
    AND c.price >= CAST(:floor AS numeric)
    AND NOT coalesce({_KM_SUSPECT.format(p='c')}, false)
  ORDER BY c.price ASC, c.last_verified_at DESC, c.listing_id LIMIT 1
"""


def _describe(filters, lang):
    """ "tesla · do 10.000 KM · od 2024": what a search asked for, without the title word."""
    parts = ([filters.search] if filters.search else []) + _attr_parts(filters, lang) + _bound_parts(filters, lang)
    if filters.family:
        parts.append(t(lang, "title_family"))
    return " · ".join(parts) or t(lang, "find_title").lower()


def _nearest_real(connection, filters):
    """For a search with no match in 90 days: the same search without its price bounds, then also without
    its year bounds. Returns (relaxed filters, "without_price" | "model", stats, cheapest row) or None."""
    steps = []
    if filters.budget or filters.price_min:
        steps.append((replace(filters, budget=None, price_min=None, include_old=True), "without_price"))
    if filters.year_min or filters.year_max:
        steps.append((replace(filters, budget=None, price_min=None, year_min=None, year_max=None,
                              mileage_max=None, mileage_min=None, include_old=True), "model"))
    for relaxed, how in steps:
        params = {**_bound_params(relaxed), "search": relaxed.search or "", "pattern": _like_pattern(relaxed.search),
                  "family": bool(relaxed.family), "family_re": FAMILY_REGEX}
        stats = _one(connection, _real_sql(), params)
        if stats and stats["n"]:
            floor = SUSPECT_PRICE_SHARE * float(stats["median"]) if stats["n"] >= SUSPECT_MIN_COHORT else 0
            cheapest = _one(connection, _cheapest_sql(), {**params, "floor": floor})
            return relaxed, how, stats, cheapest
    return None


def _nearest_lines(filters, nearest, lang):
    relaxed, how, stats, cheapest = nearest
    key = "unreal_scope_without_price" if how == "without_price" else "unreal_scope_model"
    scope = t(lang, key, what=_describe(replace(relaxed, include_old=False), lang))
    lines = [esc(t(lang, "unreal_none", what=_describe(filters, lang)))]
    if cheapest:
        label = " ".join(value for value in (cheapest["make"], cheapest["model"]) if value) or t(lang, "unparsed_model")
        details = "".join(f" · {value}" for value in (
            cheapest["year"], f"{_thousands(cheapest['mileage'])} km" if cheapest["mileage"] is not None else None)
            if value)
        car = "\x00"
        sentence = esc(t(lang, "unreal_cheapest", scope=scope, car=car, price=_money(cheapest["price"], lang)))
        lines.append(sentence.replace(car, _car_link(label, cheapest["listing_id"]) + esc(details)))
    lines.append(esc(t(lang, "unreal_typical", scope=scope, median=_money(stats["median"], lang), n=stats["n"],
                       low=_money(stats["low"], lang), high=_money(stats["high"], lang))))
    return lines


_NEAREST_YEARS_SQL = (_FIND_SQL[:_FIND_SQL.index("  ), model_years AS (")] + f"""
  )
  SELECT max(m.year) FILTER (WHERE m.year < CAST(:target AS integer)) AS older,
    min(m.year) FILTER (WHERE m.year > CAST(:target AS integer)) AS newer
  FROM matched m
  WHERE (CAST(:new_ok AS boolean) OR NOT coalesce({_NEW_LIKE.format(p='m')}, false))
    AND (CAST(:include_old AS boolean)
         OR m.last_verified_at >= (now() AT TIME ZONE 'UTC') - interval '{FRESH_DAYS} days')
""")
YEAR_STEP_SPAN = 3  # the older / newer buttons search this many model years, starting at the nearest one with cars


def _nearest_years(connection, filters):
    """(older, newer): the closest model years below and above ``filters``' single year that have cars for
    the rest of the search (None when there are none). Used when "Golf 1995" finds nothing."""
    target = filters.year_min
    params = {**_bound_params(filters), "year_min": None, "year_max": None,
              "hints": hint_patterns(()), "search": filters.search, "pattern": _like_pattern(filters.search),
              "family": bool(filters.family), "family_re": FAMILY_REGEX, "include_old": bool(filters.include_old),
              "target": target}
    row = _one(connection, _NEAREST_YEARS_SQL, params)
    return (row["older"], row["newer"]) if row else (None, None)


def _year_choice_ranges(older, newer):
    """The year ranges the older / newer buttons search, clamped to the years the bot accepts."""
    low, high = YEAR_RANGE
    ranges = {}
    if older:
        ranges["older"] = (max(low, older - YEAR_STEP_SPAN + 1), older)
    if newer:
        ranges["newer"] = (newer, min(high, newer + YEAR_STEP_SPAN - 1))
    return ranges


def _row_traits(item, lang):
    """"Dizel · Automatik · Sarajevo": the fuel, gearbox and city of a listing, whichever are recorded."""
    titles = _titles(lang)
    fuel = titles["fuel"].get(item.get("fuel_type") or "")
    gearbox = titles["gearbox"].get(item.get("transmission") or "")
    city = " ".join(str(item.get("location") or "").split())[:30]
    parts = [part[:1].upper() + part[1:] for part in (fuel, gearbox) if part] + ([city] if city else [])
    return " · ".join(parts)


class NoMatch(Html):
    """A find answer that names no listings because the model was not recognised. It is sent as is
    (never rephrased by Gemini, which could hide that nothing was searched)."""


def find_page(engine, filters, offset=0, lang=DEFAULT_LANGUAGE):
    """Return (Telegram HTML text, total matches, stats) for one page of find results.

    By default only listings seen in the last 14 days are shown; ``filters.include_old`` widens
    that to 90 days. Within a page the most recently seen come first, then the newest model year,
    then the lowest mileage; listings with implausible data are flagged and moved to the end.
    New cars (this year or next, under 1.000 km) are left out unless asked for.

    ``stats`` carries what the follow-up buttons need (oldest year, highest mileage, the cars
    shown, how many older matches were hidden) and the filters that were really used. A search word
    that matches no make or model is first repaired (typos, fewer words); if that fails, nothing is
    listed and ``stats["unrecognised"]`` / ``stats["candidates"]`` say what to ask instead.
    """
    offset = max(0, min(int(offset), MAX_OFFSET))
    assumed = unknown = detail = ""
    hint_total = 0
    model_search, hints = split_trim(filters.search)  # "audi a6 3.0 quattro": the hints rank, never filter
    if hints:
        filters = replace(filters, search=model_search)

    def shown(used):  # what the title and the buttons say: the user's own words, hints included
        return replace(used, search=" ".join([used.search, *hints]).strip()) if hints else used
    older_hidden = new_hidden = without_gearbox = 0
    gearbox_days = FRESH_DAYS
    nearest = None
    year_choice = {}
    with engine.begin() as connection:
        _statement_timeout(connection)
        if offset == 0 and filters.search and not _is_known_model(connection, filters.search):
            labels = _model_labels(connection)
            resolved, candidates = resolve_search(labels, filters.search)
            if resolved is None and candidates:
                return _unrecognised_page(shown(filters), candidates, lang)
            if resolved is None:
                unknown = filters.search  # no close name either: the plain "no match" answer, with a hint
            else:
                assumed, detail = filters.search, _assumed_detail(labels, filters.search, resolved, lang)
                filters = replace(filters, search=resolved)
        matches = _find_rows(connection, filters, offset, hints=hints)
        if matches:
            older_hidden = 0 if filters.include_old else int(matches[0]["all_total"] - matches[0]["fresh_total"])
            new_hidden = int(matches[0]["new_hidden"] or 0)
        elif offset == 0:
            probe = _find_rows(connection, filters, 0, probe=True, probe_new=True, hints=hints)
            if probe:
                new_hidden = int(probe[0]["new_hidden"] or 0)
                older_hidden = 0 if filters.include_old else int(probe[0]["all_total"])
            exact_year = bool(filters.year_min and filters.year_min == filters.year_max)
            if exact_year and not older_hidden and not unknown:
                year_choice = _year_choice_ranges(*_nearest_years(connection, filters))
            if not older_hidden and not unknown and not filters.transmission and not year_choice:
                nearest = _nearest_real(connection, filters)  # "Tesla 2024 do 10k": say it doesn't exist
            if filters.transmission:  # the gearbox is rarely recorded: say what dropping it would find
                wider = _find_rows(connection, replace(filters, transmission=""), 0, probe=True, hints=hints)
                if wider:
                    fresh_only = not filters.include_old and int(wider[0]["fresh_total"]) > 0
                    without_gearbox = int(wider[0]["fresh_total" if fresh_only else "all_total"])
                    gearbox_days = FRESH_DAYS if fresh_only else 90
        note = _freshness_note(connection, lang)
    lines = [f"<b>{esc(_filter_title(shown(filters), lang))}</b>"]
    if assumed:
        lines.append(esc(t(lang, "search_assumed", term=assumed, model=filters.search)))
        if detail:
            lines.append(esc(detail))
    if filters.family:
        lines.append(f"<i>{esc(t(lang, 'family_note'))}</i>")
    total = 0
    older = 0
    suspects = 0
    stats = {"filters": shown(filters), "older_hidden": older_hidden, "assumed": filters.search if assumed else "",
             "unknown": unknown}
    if not matches:
        if offset:
            lines.append(esc(t(lang, "no_more")))
        elif older_hidden:
            lines.append(esc(t(lang, "no_fresh", days=FRESH_DAYS)))
        elif nearest:
            lines.append(esc(t(lang, "no_match_90")))
            lines.extend(_nearest_lines(filters, nearest, lang))
            stats["nearest"] = replace(nearest[0], include_old=False)
            stats["nearest_how"] = nearest[1]
        elif year_choice:  # "Golf 1995": none in that year; offer the nearest older / newer years
            lines.append(esc(t(lang, "no_match_year", what=_describe(shown(filters), lang), year=filters.year_min)))
            lines.append(esc(t(lang, "year_ask")))
            stats["year_choice"] = year_choice
        else:
            lines.append(esc(t(lang, "no_match_90")))
        if unknown:
            lines.append(esc(t(lang, "search_unknown_hint", term=unknown)))
    else:
        total = int(matches[0]["total"])
        hint_total = int(matches[0]["hint_total"] or 0)
        first, last = offset + 1, offset + len(matches)
        span = f"{first}" if first == last else f"{first}–{last}"
        key = "find_showing_all" if filters.include_old else "find_showing"
        if (not filters.search or filters.cheapest) and not filters.low_km:
            key += "_cheapest"  # without a model, or "najjeftiniji golf": the cheapest first (see _FIND_SQL)
        lines.append(esc(t(lang, key, span=span, total=total, days=FRESH_DAYS)))
        if hints:
            words = " ".join(hints)
            lines.append(esc(t(lang, "hint_found", hints=words, n=hint_total, total=total) if hint_total
                             else t(lang, "hint_none", hints=words, model=filters.search)))
        years = [item["year"] for item in matches if item["year"]]
        kms = [int(item["mileage"]) for item in matches if item["mileage"] is not None]
        stats.update({"min_year": min(years) if years else None, "max_km": max(kms) if kms else None,
                      "items": tuple((item["listing_id"], item["year"], item["price"],
                                     " ".join(v for v in (item["make"], item["model"]) if v)) for item in matches)})
        lines.append("")
        for item in matches:
            label = " ".join(value for value in (item["make"], item["model"]) if value) or t(lang, "unparsed_model")
            year = f" · {item['year']}" if item["year"] else ""
            # Price and mileage never look alike: 💰 + capital "KM" for the price, 🛣 + lower-case "km" for the road
            mileage = f" · 🛣 {_thousands(item['mileage'])} km" if item["mileage"] is not None else ""
            fresh = _is_fresh(item["last_verified_at"])
            older += not fresh
            seen = t(lang, "seen_row", age=_age(item["last_verified_at"], lang),
                     when=_time(item["last_verified_at"], lang))
            marker = "" if fresh else "⚠ "
            traits = _row_traits(item, lang)
            row = (f"• {_car_link(label, item['listing_id'])}{esc(year)} · 💰 <b>{esc(_money(item['price'], lang))}</b>"
                   f"{esc(mileage)}\n  {esc(traits + ' · ') if traits else ''}{marker}{esc(seen)}")
            if item.get("suspect"):
                suspects += 1
                row += f"\n  {esc(t(lang, 'suspect_row'))}"
            lines.append(row)
    if older_hidden and not matches:
        lines.append(esc(t(lang, "older_hidden", n=older_hidden)))
    # One compact footer line instead of up to eight notes; what is not shown has a button or a ⚠ on the row.
    footer = [t(lang, "f_asking")]
    if older_hidden and matches:
        footer.append(t(lang, "f_older", n=older_hidden))
    if new_hidden and offset == 0:
        footer.append(t(lang, "f_new", n=new_hidden))
    if older:
        footer.append(t(lang, "f_old_rows", days=FRESH_DAYS))
    if suspects:
        footer.append(t(lang, "f_suspect"))
    if filters.transmission and without_gearbox and not matches:
        lines.append(esc(t(lang, "without_gearbox", n=without_gearbox, days=gearbox_days)))  # the answer itself
    if filters.transmission:
        footer.append(t(lang, "f_gearbox") + (t(lang, "f_gearbox_wider", n=without_gearbox, days=gearbox_days)
                                              if without_gearbox and matches else ""))
    stats["without_gearbox"] = without_gearbox
    stats["without_gearbox_old"] = gearbox_days != FRESH_DAYS
    if filters.year_min or filters.year_max or filters.mileage_max or filters.mileage_min:
        footer.append(t(lang, "f_filters"))
    if note:
        footer.append(note)
    lines.extend(["", f"<i>ℹ️ {esc(' · '.join(footer))}</i>"])
    return (NoMatch if unknown else Html)("\n".join(lines)), total, stats


def _names_list(candidates, lang):
    return ", ".join(candidates[:-1]) + (t(lang, "or_word") if len(candidates) > 1 else "") + candidates[-1]


def _unrecognised_page(filters, candidates, lang):
    """The answer for a search that matches no make or model: say so, ask "did you mean ...?", list nothing."""
    term = filters.search
    names = _names_list(candidates, lang)
    lines = [f"<b>{esc(_filter_title(replace(filters, search=''), lang))}</b>",
             esc(t(lang, "search_unknown", term=term)), esc(t(lang, "did_you_mean", names=names))]
    stats = {"filters": filters, "unrecognised": term, "candidates": tuple(candidates)}
    return NoMatch("\n".join(lines)), 0, stats


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
_BOUNDS_WHERE = ("""(CAST(:budget AS integer) IS NULL OR {p}.price<=CAST(:budget AS integer))
            AND (CAST(:price_min AS integer) IS NULL OR {p}.price>=CAST(:price_min AS integer))
            AND (CAST(:year_min AS integer) IS NULL OR {p}.year>=CAST(:year_min AS integer))
            AND (CAST(:year_max AS integer) IS NULL OR {p}.year<=CAST(:year_max AS integer))
            AND (CAST(:mileage_max AS integer) IS NULL OR {p}.mileage<=CAST(:mileage_max AS integer))
            AND (CAST(:mileage_min AS integer) IS NULL OR {p}.mileage>=CAST(:mileage_min AS integer))
            AND """ + _ATTR_WHERE + """
            AND (CAST(:new_ok AS boolean) OR NOT coalesce(""" + _NEW_LIKE.replace("{p}", "{{p}}").format() + """, false))""")


def _suggest_title(filters, lang=DEFAULT_LANGUAGE):
    parts = [t(lang, "suggest_title")] + _attr_parts(filters, lang) + _bound_parts(filters, lang)
    if filters.family:
        parts.append(t(lang, "title_family"))
    return " · ".join(parts)


_STANDOUT_ORDER = {"discount": "discount_pct DESC, r.mileage ASC, r.listing_id",
                   "price": "r.price ASC, discount_pct DESC, r.listing_id"}


def _standouts(connection, filters, days=None, limit=5, order="discount"):
    """Listings priced well below the same model and year: at least 15% under that cohort's median,
    mileage at or under its median, seen in the last ``days`` days. Used by the shortlist and "good deals"."""
    params = {**_bound_params(filters), "family": bool(filters.family), "family_re": FAMILY_REGEX,
              "search": filters.search or "", "pattern": _like_pattern(filters.search or ""),
              "days": days or SUGGEST_FRESH_DAYS, "limit": limit}
    return _all(connection, f"""
          WITH recent AS (
            SELECT c.listing_id, {_BASE_LABEL} AS label, {_LABEL_SQL} AS folded,
              c.year, c.price, {_KM} AS mileage, c.last_verified_at, c.fuel_type, c.transmission
            FROM car_listings c LEFT JOIN dealer_vehicle_labels l USING(listing_id)
            WHERE {_RECENT_WHERE} AND c.year IS NOT NULL AND {_KM} IS NOT NULL
              AND NOT coalesce({_NEW_LIKE.format(p='c')}, false)
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
            AND r.last_verified_at >= (now() AT TIME ZONE 'UTC') - interval '1 day' * CAST(:days AS integer)
            AND r.price <= {SUGGEST_DISCOUNT} * k.med_price AND r.mileage <= k.med_km
            AND NOT coalesce({_KM_SUSPECT.format(p='r')}, false)
            AND (NOT CAST(:family AS boolean) OR r.folded ~ :family_re)
            AND (CAST(:search AS text) = '' OR r.folded LIKE :pattern ESCAPE '\\')
          ORDER BY {_STANDOUT_ORDER[order]} LIMIT CAST(:limit AS integer)
    """, params)


def suggest_message(engine, filters, lang=DEFAULT_LANGUAGE, with_models=False):
    """What the database shows for a budget: most listed models and listings priced under similar ones."""
    params = {**_bound_params(filters), "family": bool(filters.family), "family_re": FAMILY_REGEX}
    with engine.begin() as connection:
        _statement_timeout(connection)
        models = _all(connection, f"""
          SELECT label, count(*) AS n,
            percentile_cont(.5) WITHIN GROUP (ORDER BY price) AS median_price,
            percentile_cont(.5) WITHIN GROUP (ORDER BY mileage) FILTER (WHERE mileage IS NOT NULL) AS median_km,
            max(year) AS newest_year
          FROM (
            SELECT {_BASE_LABEL} AS label, c.price, {_KM} AS mileage, c.year
            FROM car_listings c LEFT JOIN dealer_vehicle_labels l USING(listing_id)
            WHERE {_RECENT_WHERE}
              AND {_BOUNDS_WHERE.format(p='c')}
              AND (NOT CAST(:family AS boolean) OR {_LABEL_SQL} ~ :family_re)
          ) q
          WHERE label <> ''
          GROUP BY label HAVING count(*) >= {SUGGEST_MIN_MODEL_LISTINGS}
          ORDER BY n DESC, label LIMIT 5
        """, params)
        standouts = _standouts(connection, filters)
        note = _freshness_note(connection, lang)
    lines = [_suggest_title(filters, lang)]
    if filters.family:
        lines.append(t(lang, "family_note"))
    lines.extend(["", t(lang, "suggest_models_h")])
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



DEALS_LIMIT = 8


def deals_title(filters, lang=DEFAULT_LANGUAGE):
    parts = [t(lang, "deals_title")]
    if filters.search:
        parts.append(filters.search)
    parts.extend(_attr_parts(filters, lang) + _bound_parts(filters, lang))
    if filters.family:
        parts.append(t(lang, "title_family"))
    return " · ".join(parts)


def deals_message(engine, filters, days=None, lang=DEFAULT_LANGUAGE, cheapest=False):
    """ "dobre ponude", "povoljno", "good deals": the listings priced well below the same model and year
    (the shortlist's standouts section on its own), within any model, price, year or fuel the user gave.
    Returns (text, [(listing_id, year, price)])."""
    days = days or SUGGEST_FRESH_DAYS
    with engine.begin() as connection:
        _statement_timeout(connection)
        if filters.search and not _is_known_model(connection, filters.search):  # "povoljne Octavie"
            filters = replace(filters, search=resolve_search(_model_labels(connection), filters.search)[0]
                              or filters.search)
        rows = _standouts(connection, filters, days=days, limit=DEALS_LIMIT,
                          order="price" if cheapest else "discount")
        note = _freshness_note(connection, lang)
    lines = [deals_title(filters, lang)]
    if filters.family:
        lines.append(t(lang, "family_note"))
    lines.extend(["", t(lang, "deals_h", days=days, order=t(lang, "deals_h_cheapest") if cheapest else "")])
    for item in rows:
        lines.append(
            "• " + t(lang, "suggest_standout_row", label=item["label"], year=item["year"],
                     price=_money(item["price"], lang), pct=int(item["discount_pct"]),
                     med=_money(item["med_price"], lang), n=item["n"],
                     km=f"{int(item['mileage']):,}".replace(",", ".")) + "\n"
            f"  {t(lang, 'seen_row', age=_age(item['last_verified_at'], lang), when=_time(item['last_verified_at'], lang))}"
            f" · https://olx.ba/artikal/{item['listing_id']}")
    if not rows:
        lines.append(t(lang, "deals_none"))
    if note:
        lines.extend(["", note])
    lines.extend(["", t(lang, "deals_foot")])
    return "\n".join(lines), [(item["listing_id"], item["year"], item["price"], item["label"]) for item in rows]


PRICE_CHECK_MIN = 6  # comparable listings needed before the comparison is relaxed / called thin
PRICE_CHECK_KM_BAND = 0.35  # "similar mileage": within 35% of the stated mileage
_PRICE_CHECK_SQL = f"""
  SELECT count(*) AS n,
    percentile_cont(.25) WITHIN GROUP (ORDER BY c.price) AS p25,
    percentile_cont(.5) WITHIN GROUP (ORDER BY c.price) AS median,
    percentile_cont(.75) WITHIN GROUP (ORDER BY c.price) AS p75,
    min(c.price) AS minimum, max(c.price) AS maximum, max(c.last_verified_at) AS newest
  FROM car_listings c LEFT JOIN dealer_vehicle_labels l USING(listing_id)
  WHERE {_RECENT_WHERE}
    AND {_LABEL_SQL} LIKE :pattern ESCAPE '\\'
    AND (CAST(:year_min AS integer) IS NULL OR c.year>=CAST(:year_min AS integer))
    AND (CAST(:year_max AS integer) IS NULL OR c.year<=CAST(:year_max AS integer))
    AND (CAST(:km_low AS integer) IS NULL OR {_KM} BETWEEN CAST(:km_low AS integer) AND CAST(:km_high AS integer))
    AND {_ATTR_WHERE.format(p='c')}
    AND NOT coalesce({_NEW_LIKE.format(p='c')}, false)
    AND NOT coalesce({_KM_SUSPECT.format(p='c')}, false)
"""


def _price_check_steps(intent):
    """The comparison, then each relaxation in order (what is dropped when too few listings match)."""
    f = intent.filters
    current = {"year_min": f.year_min, "year_max": f.year_max, "fuel": f.fuel or "",
               "transmission": f.transmission or "", "km_low": None, "km_high": None}
    if intent.mileage:
        current["km_low"] = int(intent.mileage * (1 - PRICE_CHECK_KM_BAND))
        current["km_high"] = int(intent.mileage * (1 + PRICE_CHECK_KM_BAND))
    steps = [(dict(current), None)]
    if current["km_low"] is not None:
        current.update(km_low=None, km_high=None)
        steps.append((dict(current), "relaxed_km"))
    if current["transmission"]:
        current["transmission"] = ""
        steps.append((dict(current), "relaxed_gearbox"))
    if current["fuel"]:
        current["fuel"] = ""
        steps.append((dict(current), "relaxed_fuel"))
    if intent.year:
        current.update(year_min=intent.year - 2, year_max=intent.year + 2)
        steps.append((dict(current), "relaxed_year"))
    return steps


def price_check_message(engine, intent, lang=DEFAULT_LANGUAGE):
    """Is this a fair asking price? Compared with the same model (year +-1, same fuel / gearbox, similar
    mileage when given) seen in the last 90 days: median, middle half (25th-75th percentile), sample size
    and where the stated price falls. Conditions are relaxed one at a time when too few listings match,
    and the answer says which. The stated price is never used as a cap."""
    f = intent.filters
    result, used, relaxed = None, None, []
    with engine.begin() as connection:
        _statement_timeout(connection)
        search, notes, unknown = _stats_search(connection, f.search, lang)
        pattern = _like_pattern(search)
        f = replace(f, search=search)
        for params, step in ([] if unknown else _price_check_steps(intent)):
            row = _one(connection, _PRICE_CHECK_SQL, {**params, "pattern": pattern})
            if step:
                relaxed.append(step)
            if result is None or int(row["n"]) > int(result["n"]):
                result, used = row, params
            if int(row["n"]) >= PRICE_CHECK_MIN:
                result, used = row, params
                break
        note = _freshness_note(connection, lang)
    n = int(result["n"]) if result else 0
    shown = replace(f, year_min=used["year_min"], year_max=used["year_max"], fuel=used["fuel"],
                    transmission=used["transmission"]) if used else f
    parts = [t(lang, "pc_title"), f.search] + _attr_parts(shown, lang)
    if shown.year_min and shown.year_max:
        parts.append(f"{shown.year_min}–{shown.year_max}")
    elif shown.year_min:  # a generation or "od 2018" keeps its open end
        parts.append(t(lang, "title_from", x=shown.year_min))
    if intent.mileage and used and used["km_low"] is not None:
        parts.append(t(lang, "pc_km_band", low=_thousands(used["km_low"]), high=_thousands(used["km_high"])))
    lines = [" · ".join(str(part) for part in parts), ""]
    lines.extend(notes)
    if not n:
        lines.append(t(lang, "pc_none"))
        if unknown:
            lines.append(t(lang, "stats_unknown", term=unknown))
    else:
        lines.append(t(lang, "pc_sample", n=n))
        lines.append(t(lang, "pc_median", median=_money(result["median"], lang)))
        lines.append(t(lang, "pc_middle", low=_thousands(round(result["p25"])), high=_money(result["p75"], lang)))
        lines.append(t(lang, "pc_range", low=_thousands(result["minimum"]), high=_money(result["maximum"], lang)))
        if intent.price:
            lines.append("")
            lines.append(_price_position(intent.price, result, lang))
        if n < PRICE_CHECK_MIN:
            lines.extend(["", t(lang, "pc_thin", n=n)])
    for step in relaxed if n else []:
        if step == "relaxed_km" and used["km_low"] is None:
            lines.append(t(lang, "pc_relaxed_km"))
        elif step == "relaxed_gearbox" and not used["transmission"]:
            lines.append(t(lang, "pc_relaxed_gearbox"))
        elif step == "relaxed_fuel" and not used["fuel"]:
            lines.append(t(lang, "pc_relaxed_fuel"))
        elif step == "relaxed_year" and intent.year and used["year_min"] == intent.year - 2:
            lines.append(t(lang, "pc_relaxed_year"))
    if f.transmission and used and used["transmission"]:
        lines.append(t(lang, "transmission_note"))
    if note:
        lines.extend(["", note])
    lines.extend(["", t(lang, "pc_note")])
    return "\n".join(lines)


MILEAGE_MIN_KM = 1_000  # a recorded mileage under this is a placeholder on a used car, not a real reading
_MILEAGE_STATS_SQL = f"""
  SELECT count(*) AS n,
    percentile_cont(.25) WITHIN GROUP (ORDER BY c.mileage) AS p25,
    percentile_cont(.5) WITHIN GROUP (ORDER BY c.mileage) AS median,
    percentile_cont(.75) WITHIN GROUP (ORDER BY c.mileage) AS p75,
    min(c.mileage) AS minimum, max(c.mileage) AS maximum
  FROM car_listings c LEFT JOIN dealer_vehicle_labels l USING(listing_id)
  WHERE {_RECENT_WHERE}
    AND {_LABEL_SQL} LIKE :pattern ESCAPE '\\'
    AND (CAST(:year_min AS integer) IS NULL OR c.year>=CAST(:year_min AS integer))
    AND (CAST(:year_max AS integer) IS NULL OR c.year<=CAST(:year_max AS integer))
    AND c.mileage BETWEEN {MILEAGE_MIN_KM} AND {MAX_PLAUSIBLE_KM}
    AND {_ATTR_WHERE.format(p='c')}
    AND NOT coalesce({_NEW_LIKE.format(p='c')}, false)
    AND NOT coalesce({_KM_SUSPECT.format(p='c')}, false)
"""


def mileage_stats_message(engine, intent, lang=DEFAULT_LANGUAGE):
    """"How many km does a 2012 E class usually have": median and middle half of the recorded mileage of the same
    model (year +-1 when one year was named, same fuel / gearbox), with the same relaxations as a price check."""
    f = intent.filters
    result, used, relaxed = None, None, []
    with engine.begin() as connection:
        _statement_timeout(connection)
        search, notes, unknown = _stats_search(connection, f.search, lang)
        pattern = _like_pattern(search)
        f = replace(f, search=search)
        for params, step in ([] if unknown else _price_check_steps(replace(intent, mileage=None))):
            row = _one(connection, _MILEAGE_STATS_SQL, {**{k: v for k, v in params.items() if not k.startswith("km_")},
                                                         "pattern": pattern})
            if step:
                relaxed.append(step)
            if result is None or int(row["n"]) > int(result["n"]):
                result, used = row, params
            if int(row["n"]) >= PRICE_CHECK_MIN:
                result, used = row, params
                break
        note = _freshness_note(connection, lang)
    n = int(result["n"]) if result else 0
    shown = replace(f, year_min=used["year_min"], year_max=used["year_max"], fuel=used["fuel"],
                    transmission=used["transmission"]) if used else f
    parts = [t(lang, "ms_title"), f.search] + _attr_parts(shown, lang)
    if shown.year_min and shown.year_max:
        parts.append(f"{shown.year_min}–{shown.year_max}")
    elif shown.year_min:
        parts.append(t(lang, "title_from", x=shown.year_min))
    lines = [" · ".join(str(part) for part in parts), ""]
    lines.extend(notes)
    if not n:
        lines.append(t(lang, "ms_none"))
        if unknown:
            lines.append(t(lang, "stats_unknown", term=unknown))
    else:
        lines.append(t(lang, "ms_sample", n=n))
        lines.append(t(lang, "ms_median", median=_thousands(round(result["median"]))))
        lines.append(t(lang, "ms_middle", low=_thousands(round(result["p25"])), high=_thousands(round(result["p75"]))))
        lines.append(t(lang, "ms_range", low=_thousands(result["minimum"]), high=_thousands(result["maximum"])))
        if n < PRICE_CHECK_MIN:
            lines.extend(["", t(lang, "pc_thin", n=n)])
    for step in relaxed if n else []:
        if step == "relaxed_gearbox" and not used["transmission"]:
            lines.append(t(lang, "pc_relaxed_gearbox"))
        elif step == "relaxed_fuel" and not used["fuel"]:
            lines.append(t(lang, "pc_relaxed_fuel"))
        elif step == "relaxed_year" and intent.year and used["year_min"] == intent.year - 2:
            lines.append(t(lang, "pc_relaxed_year"))
    if note:
        lines.extend(["", note])
    lines.extend(["", t(lang, "ms_note")])
    return "\n".join(lines)


def _price_position(price, result, lang):
    """Where a stated price falls against the comparison: below / inside / above the middle half."""
    median = float(result["median"])
    pct = round(abs(price / median - 1) * 100) if median else 0
    side = t(lang, "pc_below" if price < median else "pc_above", pct=pct) if pct else t(lang, "pc_at_median")
    if price < float(result["p25"]):
        where = t(lang, "pc_low")
    elif price > float(result["p75"]):
        where = t(lang, "pc_high")
    else:
        where = t(lang, "pc_mid")
    return t(lang, "pc_position", price=_money(price, lang), where=where, side=side)


LIQUIDITY_WINDOW_DAYS = 180
LIQUIDITY_GONE_AFTER_DAYS = 7
LIQUIDITY_MIN_GONE = 5
LIQUIDITY_LIMIT = 6
# The collector does not see every page every day, so many ads are seen once and then "disappear".
# Ads gone within a day of first being seen are that churn, not a sale, and are not counted. When most
# ads vanish anyway (or vanish the same day), the ranking measures the collector, not the market, and
# the answer says so instead of ranking.
LIQUIDITY_MIN_DAYS = 1
LIQUIDITY_CHURN_GONE_SHARE = 0.85
LIQUIDITY_CHURN_SAME_DAY_SHARE = 0.5
LIQUIDITY_PROXY_DAYS = 14  # when the disappearances cannot rank: the most listed models seen this recently
LIQUIDITY_PROXY_LIMIT = 5


def _liquidity_title(filters, lang):
    parts = [t(lang, "liquidity_title")]
    if filters.search:
        parts.append(filters.search)
    parts.extend(_attr_parts(filters, lang))
    parts.extend(_bound_parts(filters, lang))
    if filters.family:
        parts.append(t(lang, "title_family"))
    return " · ".join(parts)


def liquidity_message(engine, filters, lang=DEFAULT_LANGUAGE, with_models=False):
    """How long ads stay on OLX before they leave our search: median days between first and last sighting
    of ads that are no longer seen. Time on OLX only, never presented as a sale. Suppressed when the
    numbers mostly reflect collector churn (see LIQUIDITY_CHURN_*). Then (or when no model has enough
    disappeared ads) the answer offers a proxy instead of a dead end: the most listed models for the same filters."""
    model_search, hints = split_trim(filters.search)
    if hints:
        filters = replace(filters, search=model_search)
    params = {**_bound_params(filters), "search": filters.search, "pattern": _like_pattern(filters.search),
              "family": bool(filters.family), "family_re": FAMILY_REGEX}
    seen_sql = f"""
          WITH seen AS (
            SELECT {_BASE_LABEL} AS label,
              extract(epoch FROM (c.last_verified_at - c.first_seen_at)) / 86400.0 AS days_listed,
              c.last_verified_at < (now() AT TIME ZONE 'UTC') - interval '{LIQUIDITY_GONE_AFTER_DAYS} days' AS gone
            FROM car_listings c LEFT JOIN dealer_vehicle_labels l USING(listing_id)
            WHERE c.source='olx.ba'
              AND c.collection_method IN ('olx_api','olx_website')
              AND c.price_status='priced' AND c.price>0
              AND c.first_seen_at IS NOT NULL AND c.last_verified_at IS NOT NULL
              AND c.first_seen_at >= (now() AT TIME ZONE 'UTC') - interval '{LIQUIDITY_WINDOW_DAYS} days'
              AND {_BOUNDS_WHERE.format(p='c')}
              AND NOT coalesce({_KM_SUSPECT.format(p='c')}, false)
              AND (:search='' OR {_LABEL_SQL} LIKE :pattern ESCAPE '\\')
              AND (NOT CAST(:family AS boolean) OR {_LABEL_SQL} ~ :family_re)
          )"""
    with engine.begin() as connection:
        _statement_timeout(connection)
        overall = _one(connection, seen_sql + """
          SELECT count(*) AS seen_n, count(*) FILTER (WHERE gone) AS gone_n,
            count(*) FILTER (WHERE gone AND days_listed < :min_days) AS same_day_n
          FROM seen""", {**params, "min_days": LIQUIDITY_MIN_DAYS}) or {}
        seen_n, gone_n = int(overall.get("seen_n") or 0), int(overall.get("gone_n") or 0)
        churn = bool(gone_n) and (gone_n >= LIQUIDITY_CHURN_GONE_SHARE * seen_n
                                  or int(overall.get("same_day_n") or 0) >= LIQUIDITY_CHURN_SAME_DAY_SHARE * gone_n)
        rows = [] if churn else _all(connection, seen_sql + f"""
          SELECT label, count(*) AS seen_n, count(*) FILTER (WHERE gone AND days_listed >= :min_days) AS gone_n,
            round(CAST(percentile_cont(.5) WITHIN GROUP (ORDER BY days_listed)
              FILTER (WHERE gone AND days_listed >= :min_days) AS numeric)) AS median_days
          FROM seen WHERE label <> ''
          GROUP BY label HAVING count(*) FILTER (WHERE gone AND days_listed >= :min_days) >= {LIQUIDITY_MIN_GONE}
          ORDER BY median_days ASC, gone_n DESC, label LIMIT {LIQUIDITY_LIMIT}
        """, {**params, "min_days": LIQUIDITY_MIN_DAYS})
        proxy = [] if rows else _all(connection, f"""
          SELECT {_BASE_LABEL} AS label, count(*) AS n, percentile_cont(.5) WITHIN GROUP (ORDER BY c.price) AS median
          FROM car_listings c LEFT JOIN dealer_vehicle_labels l USING(listing_id)
          WHERE {_RECENT_WHERE} AND {_BOUNDS_WHERE.format(p='c')}
            AND c.last_verified_at >= (now() AT TIME ZONE 'UTC') - interval '{LIQUIDITY_PROXY_DAYS} days'
            AND NOT coalesce({_KM_SUSPECT.format(p='c')}, false)
            AND (:search='' OR {_LABEL_SQL} LIKE :pattern ESCAPE '\\')
            AND (NOT CAST(:family AS boolean) OR {_LABEL_SQL} ~ :family_re)
          GROUP BY 1 HAVING {_BASE_LABEL} <> ''
          ORDER BY n DESC, label LIMIT {LIQUIDITY_PROXY_LIMIT}
        """, params)
        # Neither a ranking nor most-listed models (the filters are too narrow for either): never end on
        # "not enough data". Show the newest cars that fit, so the answer is still a list.
        newest = [] if (rows or proxy) else _all(connection, f"""
          SELECT c.listing_id, {_BASE_LABEL} AS label, c.year, c.price, {_KM} AS mileage, c.last_verified_at
          FROM car_listings c LEFT JOIN dealer_vehicle_labels l USING(listing_id)
          WHERE {_RECENT_WHERE} AND {_BOUNDS_WHERE.format(p='c')}
            AND NOT coalesce({_KM_SUSPECT.format(p='c')}, false)
            AND (:search='' OR {_LABEL_SQL} LIKE :pattern ESCAPE '\\')
            AND (NOT CAST(:family AS boolean) OR {_LABEL_SQL} ~ :family_re)
          ORDER BY c.year DESC NULLS LAST, c.price ASC, c.listing_id LIMIT {LIQUIDITY_PROXY_LIMIT}
        """, params)
        note = _freshness_note(connection, lang)
    lines = [f"<b>{esc(_liquidity_title(filters, lang))}</b>"]
    if hints:
        lines.append(esc(t(lang, "stats_hints", hints=" ".join(hints), model=model_search)))
    if filters.family:
        lines.append(f"<i>{esc(t(lang, 'family_note'))}</i>")
    if churn:
        rows = []
        share = round(100 * gone_n / seen_n) if seen_n else 0
        lines.append(esc(t(lang, "liquidity_churn", gone=gone_n, seen=seen_n, share=share)))
    elif rows:
        lines.append(esc(t(lang, "liquidity_how", days=LIQUIDITY_WINDOW_DAYS, n=LIQUIDITY_MIN_GONE)))
        lines.append("")
        for index, row in enumerate(rows, 1):
            lines.append(f"{index}. " + t(lang, "liquidity_row", label=f"<b>{esc(row['label'])}</b>",
                                           d=int(row["median_days"]), gone=row["gone_n"], seen=row["seen_n"]))
    else:
        lines.append(esc(t(lang, "liquidity_none", n=LIQUIDITY_MIN_GONE)))
    if proxy:
        lines.extend(["", esc(t(lang, "liquidity_proxy", days=LIQUIDITY_PROXY_DAYS)), ""])
        for index, row in enumerate(proxy, 1):
            lines.append(f"{index}. " + esc(t(lang, "liquidity_proxy_row", label=row["label"], n=row["n"],
                                              median=_money(row["median"], lang))))
    if newest:
        lines.extend(["", esc(t(lang, "liquidity_newest")), ""])
        for index, row in enumerate(newest, 1):
            parts = [str(row["year"])] if row["year"] else []
            parts.append(f"💰 {_money(row['price'], lang)}")
            if row["mileage"] is not None:
                parts.append(f"🛣 {_thousands(row['mileage'])} km")
            parts.append(t(lang, "seen_row", age=_age(row["last_verified_at"], lang),
                           when=_time(row["last_verified_at"], lang)))
            lines.append(f"{index}. {_car_link(row['label'] or t(lang, 'unparsed_model'), row['listing_id'])} · "
                         + esc(" · ".join(parts)))
    if note:
        lines.extend(["", esc(note)])
    lines.extend(["", f"<i>{esc(t(lang, 'liquidity_caveat'))}</i>"])
    text_value = Html("\n".join(lines))
    models = [row["label"] for row in (rows or proxy)]
    return (text_value, models) if with_models else text_value


def _parse_span(value):
    """(low | None, high | None, is_range) from a button field; raises ValueError when malformed."""
    low, dash, high = value.partition("-")
    if not dash:
        single = int(value)
        return single or None, single or None, False
    return int(low) or None, int(high) or None, True


def _bounds_in_range(filters):
    return ((filters.budget is None or 1_000 <= filters.budget <= 1_000_000)
            and (filters.price_min is None or 1_000 <= filters.price_min <= 1_000_000)
            and (filters.price_min is None or filters.budget is None or filters.price_min <= filters.budget)
            and (filters.year_min is None or 1980 <= filters.year_min <= 2035)
            and (filters.year_max is None or 1980 <= filters.year_max <= 2035)
            and (filters.year_min is None or filters.year_max is None or filters.year_min <= filters.year_max)
            and (filters.mileage_max is None or 1_000 <= filters.mileage_max <= 1_000_000)
            and (filters.mileage_min is None or 1_000 <= filters.mileage_min <= 1_000_000)
            and (filters.mileage_min is None or filters.mileage_max is None
                 or filters.mileage_min <= filters.mileage_max))


def _bound_fields(filters):
    """'price:year:km'. A lone price is the upper bound and a lone year the lower one, exactly as on the
    buttons sent before ranges existed; anything else is written as 'low-high' (0 = open end)."""
    price = (f"{filters.price_min}-{filters.budget or 0}" if filters.price_min else f"{filters.budget or 0}")
    year = (f"{filters.year_min or 0}-{filters.year_max}" if filters.year_max else f"{filters.year_min or 0}")
    if filters.mileage_min:  # a minimum is written as 'low-high' (0 = open end)
        return f"{price}:{year}:{filters.mileage_min}-{filters.mileage_max or 0}"
    return f"{price}:{year}:{filters.mileage_max or 0}"


def _filters_from_fields(search, price, year, mileage, **flags):
    """Filters from the price / year / mileage button fields, or None when malformed."""
    try:
        price_low, price_high, price_range = _parse_span(price)
        year_low, year_high, year_range = _parse_span(year)
        mileage_low, mileage, mileage_range = _parse_span(mileage)
        if not mileage_range:
            mileage_low = None
    except ValueError:
        return None
    price_min, budget = (price_low, price_high) if price_range else (None, price_high)
    if not year_range:  # a single year is the lower bound ("od 2018"), as on every older button
        year_high = None
    return Filters(clean_search(search), budget, year_low, mileage or None, price_min=price_min, year_max=year_high,
                   mileage_min=mileage_low, **flags)


FUEL_CODES = {"diesel": "d", "petrol": "b", "hybrid": "h", "electric": "e", "lpg": "g"}
GEARBOX_CODES = {"automatic": "a", "manual": "m"}
_FLAG_TAIL = r"(n?)([dbheg]?)([am]?)(k?)(c?)"


def _attr_flags(filters):
    """Button flag letters for "incl. new", fuel and gearbox (after the older o / p letters)."""
    return (("n" if filters.new_ok else "") + FUEL_CODES.get(filters.fuel, "")
            + GEARBOX_CODES.get(filters.transmission, "") + ("k" if filters.low_km else "")
            + ("c" if filters.cheapest else ""))


def _attr_values(new, fuel, gearbox, low_km="", cheapest=""):
    fuels = {code: value for value, code in FUEL_CODES.items()}
    gearboxes = {code: value for value, code in GEARBOX_CODES.items()}
    return {"new_ok": bool(new), "fuel": fuels.get(fuel, ""), "transmission": gearboxes.get(gearbox, ""),
            "low_km": bool(low_km), "cheapest": bool(cheapest)}


def liquidity_callback(filters):
    data = f"l{'p' if filters.family else ''}{_attr_flags(filters)}:{_bound_fields(filters)}:{filters.search}"
    return data if len(data.encode()) <= 64 else None


def parse_liquidity_callback(data):
    """Return Filters from an 'l[p][n][fuel][gearbox]:price:year:km:search' button, or None. A price or
    year field is either one number (the upper price / the lower year) or 'low-high'."""
    parts = (data or "").split(":", 4)
    flags = re.fullmatch(r"l(p?)" + _FLAG_TAIL, parts[0]) if parts else None
    if len(parts) != 5 or not flags:
        return None
    filters = _filters_from_fields(parts[4], *parts[1:4], family=bool(flags.group(1)),
                                   **_attr_values(*flags.group(2, 3, 4, 5, 6)))
    return filters if filters is not None and _bounds_in_range(filters) else None


def unit_choices(intent, lang):
    """One-tap answer to "KM (price) or km (mileage)?"."""
    amount = intent.offset
    make = liquidity_callback if "liquidity" in intent.labels else refine_callback
    rows = []
    if 1_000 <= amount <= 1_000_000:
        rows.append(((t(lang, "btn_unit_price", amount=_thousands(amount)),
                      make(replace(intent.filters, budget=amount))),))
        rows.append(((t(lang, "btn_unit_km", amount=_thousands(amount)),
                      make(replace(intent.filters, mileage_max=amount))),))
    return Choices(tuple(rows))


def short_number_choices(intent, lang):
    """One-tap answer to "what does 20 mean: year 2020, price 20.000 KM or mileage 20.000 km?". Only the readings
    the message has not already filled (a price when it has a budget, ...) are offered."""
    n, f = intent.offset, intent.filters
    amount = n * 1000
    row = [(t(lang, "btn_short_year", year=2000 + n), refine_callback(replace(f, year_min=2000 + n, year_max=2000 + n)))]
    if 1_000 <= amount <= 1_000_000 and not (f.budget or f.price_min):
        row.append((t(lang, "btn_short_price", amount=_thousands(amount)), refine_callback(replace(f, budget=amount))))
    if 1_000 <= amount <= 1_000_000 and not (f.mileage_max or f.mileage_min):
        row.append((t(lang, "btn_short_km", amount=_thousands(amount)),
                    refine_callback(replace(f, mileage_max=amount))))
    return Choices((tuple(row),))


def chitchat_message(lang=DEFAULT_LANGUAGE, topic=""):
    """Small talk without Gemini: a short reply to what was said, then what the bot can do."""
    if topic in {"greeting", "weather", "how", "about", "joke", "scope"}:
        return t(lang, f"smalltalk_{topic}") + "\n\n" + t(lang, "smalltalk_hint")
    return t(lang, "chitchat")


def clarify_message(note="", question="", lang=DEFAULT_LANGUAGE, labels=()):
    if question:
        return question + "\n\n" + t(lang, "clarify_suffix")
    if "more" in labels:
        return t(lang, "clarify_more")
    if "vague" in labels:
        return t(lang, "clarify_vague")
    if note == "mileage_stats":
        return t(lang, "clarify_mileage_stats")
    if note == "currency":
        return t(lang, "clarify_currency")
    if note == "correction":
        return t(lang, "clarify_correction")
    if note == "over_budget":
        return t(lang, "clarify_over_budget")
    if note in {"budget", "newer", "compare"}:
        return t(lang, f"clarify_{note}")
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
    if command == "drops":
        import telegram_digest  # lazy: telegram_digest imports this module
        return telegram_digest.drops_message(engine, lang)
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
    """'f[o][p][n][fuel][gearbox]:price:year:km:offset:search' — 'o' includes older listings, 'p' is a
    family search, 'n' keeps new cars, then one fuel letter (d b h e g) and one gearbox letter (a m).
    A price or year field is one number (upper price / lower year) or a 'low-high' range."""
    flags = ("o" if filters.include_old else "") + ("p" if filters.family else "") + _attr_flags(filters)
    data = f"f{flags}:{_bound_fields(filters)}:{offset}:{filters.search}"
    return data if len(data.encode()) <= 64 else None


def parse_refine_page(data):
    """Return (Filters, offset) from a find button, or None. Accepts the older 5-field form."""
    parts = (data or "").split(":", 5)
    flags = re.fullmatch(r"f(o?)(p?)" + _FLAG_TAIL, parts[0]) if parts else None
    if not flags or len(parts) not in (5, 6):
        return None
    try:
        offset = int(parts[4]) if len(parts) == 6 else 0
    except ValueError:
        return None
    filters = _filters_from_fields(parts[-1], *parts[1:4], family=bool(flags.group(2)),
                                   include_old=bool(flags.group(1)), **_attr_values(*flags.group(3, 4, 5, 6, 7)))
    if filters is None:
        return None
    in_range = _bounds_in_range(filters) and 0 <= offset <= MAX_OFFSET
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


def follow_callback(make, model):
    data = f"p:{make}|{model}"
    return data if len(data.encode()) <= 64 else None


def parse_follow_callback(data):
    """Return (make, model) from a 'p:<make>|<model>' button, or None."""
    if not isinstance(data, str) or not data.startswith("p:") or data.count("|") != 1:
        return None
    make, model = (part.strip() for part in data[2:].split("|"))
    if not make or not model or len(make) > 80 or len(model) > 100 or re.search(r"[\x00-\x1f]", data):
        return None
    return make, model


def _valid_listing_id(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9A-Za-z_-]{1,50}", value) is not None


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


def save_callback(listing_id):
    data = f"c:{listing_id}"
    return data if len(data.encode()) <= 64 else None


SAVE_LABEL_MAX = 40


def _save_rows(page, lang):
    """⭐ buttons for the cars on screen, two to a row."""
    buttons = []
    for item in page.items:
        listing_id, year, price = item[:3]
        name = item[3] if len(item) > 3 else ""
        data = save_callback(listing_id) if _valid_listing_id(listing_id) else None
        if data:
            label = _save_label(name, year, price, lang) or listing_id[:12]
            buttons.append(_button(t(lang, "btn_save", label=label), data))
    return [[button] for button in buttons]  # make, model, year and price are too long for two to a row


def _save_label(name, year, price, lang):
    """"Volkswagen Golf · 2022 · 134.900 KM": what the ⭐ button saves, make and model first."""
    tail = " · ".join(str(part) for part in (year, _money(price, lang) if price else None) if part)
    name = " ".join(str(name or "").split())
    room = SAVE_LABEL_MAX - len(tail) - 3 if tail else SAVE_LABEL_MAX
    if len(name) > room > 3:
        name = name[:room - 1].rstrip() + "…"
    return " · ".join(part for part in (name, tail) if part)


def _find_refinements(filters, page, lang):
    """Contextual rows for a find answer: more, budget, newer, lower mileage."""
    rows = []
    if page.older_hidden and not filters.include_old:
        data = refine_callback(replace(filters, include_old=True))
        if data:
            rows.append([_button(t(lang, "btn_include_old", n=page.older_hidden), data)])
    if page.next_offset is not None:
        data = refine_callback(filters, page.next_offset)
        if data:
            key = "btn_show_first" if page.next_offset == 0 else "btn_more"
            rows.append([_button(t(lang, key, a=page.next_offset + 1, b=page.next_offset + PAGE_SIZE), data)])
    if page.empty:
        # nothing matched: narrowing (cheaper, newer, fewer km) can only find nothing again
        data = refine_callback(replace(filters, budget=_round_budget(filters.budget * 1.2))) if filters.budget else None
        if data:
            rows.append([_button(t(lang, "btn_pricier"), data)])
        return rows
    if filters.budget:
        row = []
        for key, factor in (("btn_cheaper", 0.9), ("btn_pricier", 1.1)):
            low = _round_budget(filters.price_min * factor) if filters.price_min else None  # a range moves as a whole
            data = refine_callback(replace(filters, budget=_round_budget(filters.budget * factor), price_min=low))
            if data:
                row.append(_button(t(lang, key), data))
        if row:
            rows.append(row)
    else:
        row = []
        floor = filters.price_min or 0
        amounts = ((10_000, 20_000, 30_000) if not floor
                   else tuple(_round_budget(floor * factor) for factor in (1.25, 1.5, 2)))
        for amount in amounts:
            data = refine_callback(replace(filters, budget=amount))
            if data:
                row.append(_button(t(lang, "btn_budget", amount=_thousands(amount)), data))
        if row:
            rows.append(row)
    row = []
    base_year = filters.year_min or page.min_year
    if base_year:
        newer = min(datetime.now(timezone.utc).year, base_year + 2)
        if filters.year_max:
            newer = min(newer, filters.year_max)
        data = refine_callback(replace(filters, year_min=newer))
        if newer > (filters.year_min or 0) and data:
            row.append(_button(t(lang, "btn_newer", year=newer), data))
    base_km = filters.mileage_max or page.max_km
    if base_km:
        lower = max(10_000, int(base_km * 0.7) // 10_000 * 10_000)
        data = refine_callback(replace(filters, mileage_max=lower))
        if lower < base_km and lower > (filters.mileage_min or 0) and data:
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
        rows = _save_rows(page, lang) + _find_refinements(page.filters, page, lang)
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
    if chat_id in config.allowed_chats:
        return True
    return chat.get("type") == "private" and chat_id == user_id


# Best-effort memory of the last answer's scope. It lives only in this process (serverless
# instances may not share it), is keyed by chat AND user, and expires. In a private chat it
# carries ordinary follow-ups ("a od 2018?"); in a group it is used only when the same person
# corrects their own previous question ("mislim da sam stavio 50-60k"). Other group follow-ups
# must reply to a bot message.
SCOPE_TTL_SECONDS = 1800
SCOPE_MAX_ENTRIES = 64
_SCOPES = {}


# The last budget a person stated, remembered next to the scope: the scope holds one intent, and a later turn
# ("koji auto?", a trends question) must not make the person say their budget again.
_BUDGETS = {}


def _remember_budget(key, budget):
    if not budget:
        return
    now = time.monotonic()
    for stale in [k for k, (_, at) in _BUDGETS.items() if now - at > SCOPE_TTL_SECONDS]:
        _BUDGETS.pop(stale, None)
    if len(_BUDGETS) >= SCOPE_MAX_ENTRIES:
        _BUDGETS.pop(min(_BUDGETS, key=lambda k: _BUDGETS[k][1]), None)
    _BUDGETS[key] = (int(budget), now)


def _recall_budget(key):
    entry = _BUDGETS.get(key)
    if not entry or time.monotonic() - entry[1] > SCOPE_TTL_SECONDS:
        _BUDGETS.pop(key, None)
        return None
    return entry[0]


def _remember_scope(key, intent):
    if intent.kind not in {"find", "trends", "suggest", "liquidity", "deals"}:
        return
    _remember_budget(key, intent.filters.budget)
    now = time.monotonic()
    for stale in [k for k, (_, at) in _SCOPES.items() if now - at > SCOPE_TTL_SECONDS]:
        _SCOPES.pop(stale, None)
    if len(_SCOPES) >= SCOPE_MAX_ENTRIES:
        _SCOPES.pop(min(_SCOPES, key=lambda k: _SCOPES[k][1]), None)
    _SCOPES[key] = (intent, now)


def _with_page(intent, markup):
    """The intent to remember: a find also records how many matches its page had, so "pokaži još" knows when
    the last page was shown."""
    if intent.kind == "find" and isinstance(markup, Page):
        return replace(intent, page_total=markup.total)
    return intent


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


def _follow_answer(engine, intent, lang):
    """Follow / unfollow / list. State changes happen only from a confirm button (follow_do, unfollow)."""
    import telegram_follow  # lazy: telegram_follow imports this module

    if intent.kind == "follow":
        query = intent.filters.search
        candidates = telegram_follow.resolve_models(engine, query)
        rows = [((t(lang, "btn_follow", label=f"{c['make']} {c['model']}"[:40]),
                  follow_callback(c["make"], c["model"])),) for c in candidates]
        rows = [row for row in rows if row[0][1]]
        if not rows:
            return t(lang, "follow_none", query=query), False
        key = "follow_ask_one" if len(candidates) == 1 else "follow_ask"
        return t(lang, key, label=f"{candidates[0]['make']} {candidates[0]['model']}"), Choices(tuple(rows))
    if intent.kind == "follow_do":
        make, model = intent.labels
        label = f"{make} {model}"
        try:
            result = telegram_follow.follow_model(engine, make, model)
        except SQLAlchemyError:
            return t(lang, "follow_failed"), False
        if result is None:
            return t(lang, "follow_none", query=label), False
        search_id, created = result
        text_value = t(lang, "follow_done" if created else "follow_exists", label=label)
        return text_value, Choices((
            ((t(lang, "btn_unfollow", label=label[:30]), f"u:{search_id}"),),
            ((t(lang, "btn_following"), "following"),),
        ))
    if intent.kind == "unfollow":
        try:
            label = telegram_follow.unfollow(engine, intent.note)
        except SQLAlchemyError:
            return t(lang, "follow_failed"), False
        if label is None:
            return t(lang, "unfollow_gone"), Choices((((t(lang, "btn_following"), "following"),),))
        return t(lang, "unfollow_done", label=label), Choices((((t(lang, "btn_following"), "following"),),))
    text_value, follows = telegram_follow.following_message(engine, lang)
    rows = tuple(((t(lang, "btn_unfollow", label=label[:30]), f"u:{search_id}"),) for search_id, label in follows[:4])
    return text_value, (Choices(rows) if rows else False)


def _shortlist_answer(engine, intent, lang):
    """Save / remove / list shortlisted cars. Writes happen only from a button tap."""
    import telegram_shortlist as sl  # lazy: telegram_shortlist imports this module

    my_list = Choices((((t(lang, "btn_shortlist"), "shortlist"),),))
    if intent.kind == "shortlist":
        text_value, items = sl.shortlist_message(engine, lang)
        rows = tuple(((f"✖ {label[:40]}", f"x:{listing_id}"),) for listing_id, label in items[:4])
        return text_value, (Choices(rows) if rows else False)
    try:
        if intent.kind == "save_car":
            result = sl.save_listing(engine, intent.note)
            if result is None:
                return t(lang, "save_gone"), False
            row, created = result
            label = sl.label_for(row, lang)
            return t(lang, "save_done" if created else "save_exists", label=label), Choices((
                ((t(lang, "btn_remove"), f"x:{row['listing_id']}"),), ((t(lang, "btn_shortlist"), "shortlist"),)))
        row, outcome = sl.remove_listing(engine, intent.note)
    except sl.NotEnabled:
        return t(lang, "save_not_enabled"), False
    except SQLAlchemyError:
        return t(lang, "save_failed"), False
    label = sl.label_for(row, lang) if row.get("make") or row.get("model") else intent.note
    if outcome == "removed":
        return t(lang, "remove_done", label=label), my_list
    if outcome == "kept":
        return t(lang, "remove_kept", label=label), my_list
    return t(lang, "remove_gone"), my_list


def _did_you_mean_choices(filters, candidates, lang):
    """🔎 one button per likely model (same filters), or "search without it" when there is no guess."""
    rows = [((f"🔎 {name[:30]}", refine_callback(replace(filters, search=clean_search(fold(name))))),)
            for name in candidates]
    if not candidates and _has_filters(filters):
        rows.append(((t(lang, "btn_search_without", term=filters.search[:20]),
                      refine_callback(replace(filters, search=""))),))
    return Choices(tuple(rows)) if rows else False


def _model_filters(model, filters):
    """A 🔎 model button keeps the question's price, year and mileage bounds (not family / older)."""
    return Filters(model, filters.budget, filters.year_min, filters.mileage_max, price_min=filters.price_min,
                   year_max=filters.year_max, fuel=filters.fuel, transmission=filters.transmission,
                   mileage_min=filters.mileage_min, low_km=filters.low_km)


SKIPPABLE = ("find", "trends", "liquidity", "suggest", "deals", "compare", "price_check", "model_stats",
             "mileage_stats", "status", "latest", "follow", "following", "shortlist")


def intent_notes(intent, lang=DEFAULT_LANGUAGE):
    """One-line notes an answer must carry: "kn" read as KM, a budget reused from earlier, and what a mixed
    message asked for that was skipped."""
    notes = []
    if "kn" in intent.labels:
        notes.append(t(lang, "note_kn"))
    if "budget_reused" in intent.labels and intent.filters.budget:
        notes.append(t(lang, "note_budget_reused", amount=_thousands(intent.filters.budget)))
    if "same_budget" in intent.labels and intent.filters.budget:
        notes.append(t(lang, "note_same_budget", amount=_thousands(intent.filters.budget)))
    if "location" in intent.labels:
        notes.append(t(lang, "note_location"))
    if "body" in intent.labels:
        notes.append(t(lang, "note_body"))
    skipped = [label.partition(":")[2] for label in intent.labels if label.startswith("skipped:")]
    if skipped:
        names = [t(lang, f"skip_{kind}") if kind in SKIPPABLE else kind for kind in skipped]
        notes.append(t(lang, "note_skipped", what=", ".join(names)))
    return notes


def _with_notes(text_value, intent, lang):
    """``text_value`` with the intent's notes after it (once: a text that already has a note is left alone)."""
    notes = [note for note in intent_notes(intent, lang) if note not in html_to_text(text_value)]
    if not notes:
        return text_value
    if isinstance(text_value, Html):
        added = type(text_value)(text_value + "\n\n" + "\n".join(f"<i>ℹ️ {esc(note)}</i>" for note in notes))
        if hasattr(text_value, "must_mention"):
            added.must_mention = text_value.must_mention
        return added
    return f"{text_value}\n\n" + "\n".join(f"ℹ️ {note}" for note in notes)


def answer_intent(engine, intent, config, lang=DEFAULT_LANGUAGE):
    """Map a parsed Intent onto the read-only helpers. Returns (text, buttons), with the intent's notes
    (see ``intent_notes``) after the text.

    ``buttons`` is None (general navigation), False (no buttons), a Page (search follow-ups)
    or Choices (quick replies that fit this answer).
    """
    text_value, buttons = _answer_intent(engine, intent, config, lang)
    return _with_notes(text_value, intent, lang), buttons


def _answer_intent(engine, intent, config, lang=DEFAULT_LANGUAGE):
    if intent.kind == "status":
        return status_message(engine, lang), None
    if intent.kind == "latest":
        return latest_message(engine, lang), None
    if intent.kind == "trends":
        return trends_message(engine, intent.days, lang), None
    if intent.kind == "find":
        offset = max(0, min(intent.offset, MAX_OFFSET))
        text_value, total, stats = find_page(engine, intent.filters, offset, lang)
        if "registration" in intent.labels:  # "Golf sa registracijom": searched as a Golf, said so
            text_value = type(text_value)(f"{text_value}\n\n<i>{esc(t(lang, 'registration_note'))}</i>")
        if stats.get("unrecognised") or stats.get("unknown"):
            return text_value, _did_you_mean_choices(stats["filters"], stats.get("candidates", ()), lang)
        if stats.get("assumed"):
            text_value.must_mention = stats["assumed"]  # a phrased reply must keep saying which model it is
        if stats.get("nearest"):  # nothing like it exists: offer the real ones instead of narrowing buttons
            nearest = stats["nearest"]
            label = (t(lang, "btn_unreal", what=_describe(nearest, lang)) if stats["nearest_how"] == "without_price"
                     else f"🔎 {_describe(nearest, lang)}")
            return text_value, Choices((((label[:60], refine_callback(nearest)),),))
        if stats.get("year_choice"):  # nothing in that exact year: older or newer?
            base = stats["filters"]
            row = []
            for way, (low, high) in stats["year_choice"].items():
                data = refine_callback(replace(base, year_min=low, year_max=high))
                if data:
                    span = str(low) if low == high else f"{low}–{high}"
                    row.append((t(lang, f"btn_year_{way}", years=span), data))
            if row:
                return text_value, Choices((tuple(row),))
        following = offset + PAGE_SIZE
        if stats.get("without_gearbox"):
            data = refine_callback(replace(stats["filters"], transmission="",
                                           include_old=stats["filters"].include_old or stats["without_gearbox_old"]))
            rows = [((t(lang, "btn_without_gearbox", n=stats["without_gearbox"]), data),)]
            if stats.get("older_hidden") and not stats["filters"].include_old:
                rows.append(((t(lang, "btn_include_old", n=stats["older_hidden"]),
                              refine_callback(replace(stats["filters"], include_old=True))),))
            return text_value, Choices(tuple(rows))
        return text_value, Page(stats.get("filters", intent.filters),
                                following if following < total and following <= MAX_OFFSET else None,
                                stats.get("min_year"), stats.get("max_km"), stats.get("items", ()),
                                stats.get("older_hidden", 0), total, empty=not total and not offset)
    if intent.kind == "price_check":
        f = intent.filters
        listings = Filters(f.search, year_min=f.year_min, year_max=f.year_max, fuel=f.fuel,
                           transmission=f.transmission)
        return (price_check_message(engine, intent, lang),
                Choices((((t(lang, "btn_show_listings"), refine_callback(listings)),),)))
    if intent.kind == "mileage_stats":
        f = intent.filters
        listings = Filters(f.search, year_min=f.year_min, year_max=f.year_max, fuel=f.fuel,
                           transmission=f.transmission)
        return (mileage_stats_message(engine, intent, lang),
                Choices((((t(lang, "btn_show_listings"), refine_callback(listings)),),)))
    if intent.kind == "no_more":
        f = intent.filters
        rows = [] if f.include_old else [((t(lang, "btn_include_old_plain"),
                                           refine_callback(replace(f, include_old=True))),)]
        return t(lang, "no_more_last"), (Choices(tuple(rows)) if rows else example_choices(lang))
    if intent.kind == "model_stats":
        label = intent.filters.search
        rows = ((t(lang, "btn_show_listings"), refine_callback(Filters(label))),)
        return model_stats_message(engine, label, lang), Choices((rows,))
    if intent.kind == "suggest":
        text_value, models = suggest_message(engine, intent.filters, lang, with_models=True)
        f = intent.filters
        buttons = tuple((f"🔎 {model[:24]}", refine_callback(_model_filters(model, f)))
                        for model in models[:3])
        return text_value, (Choices((buttons,)) if buttons else False)
    if intent.kind == "liquidity":
        text_value, models = liquidity_message(engine, intent.filters, lang, with_models=True)
        f = intent.filters
        buttons = tuple((f"🔎 {model[:24]}", refine_callback(_model_filters(model, f)))
                        for model in models[:3])
        if not buttons:  # too little data to rank models: still offer a next step, never a dead end
            listings = ((t(lang, "btn_show_listings"), refine_callback(f)) if not f.is_empty()
                        else (t(lang, "btn_latest"), "latest"))  # an empty search button would do nothing
            buttons = (listings,
                       (t(lang, "btn_suggest_budget", amount=_thousands(f.budget)), suggest_callback(f.budget))
                       if f.budget else (t(lang, "btn_trends30"), "trends:30"))
        return text_value, Choices((buttons,))
    if intent.kind == "compare":
        text_value, used = compare(engine, " | ".join(intent.labels), lang, intent.filters.year_min,
                                   intent.filters.year_max, family=intent.filters.family)
        buttons = tuple((f"🔎 {label[:24]}", refine_callback(Filters(search, year_min=low, year_max=high)))
                        for label, (search, low, high) in zip(intent.labels, used or [(l, None, None) for l in intent.labels]))
        return text_value, Choices((buttons,))
    if intent.kind == "deals":
        text_value, items = deals_message(engine, intent.filters, intent.days, lang,
                                          cheapest="cheapest" in intent.labels)
        saves = [(t(lang, "btn_save", label=_save_label(item[3] if len(item) > 3 else "", item[1], item[2], lang)),
                  save_callback(item[0])) for item in items[:6] if _valid_listing_id(item[0])]
        rows = tuple((save,) for save in saves)
        return text_value, (Choices(rows) if rows else False)
    if intent.kind in {"follow", "follow_do", "unfollow", "following"}:
        return _follow_answer(engine, intent, lang)
    if intent.kind in {"save_car", "remove_car", "shortlist"}:
        return _shortlist_answer(engine, intent, lang)
    if intent.kind in {"chitchat", "clarify", "unsupported"}:
        if intent.kind == "clarify" and intent.note == "short_number":
            return (t(lang, "clarify_short_number", n=intent.offset, year=2000 + intent.offset,
                      price=_thousands(intent.offset * 1000)), short_number_choices(intent, lang))
        if intent.kind == "clarify" and intent.note == "km_unit":
            return t(lang, "clarify_km_unit", amount=_thousands(intent.offset)), unit_choices(intent, lang)
        if intent.reply:
            text_value = intent.reply
        elif intent.kind == "chitchat":
            text_value = chitchat_message(lang, intent.labels[0] if intent.labels else "")
        elif intent.kind == "clarify":
            text_value = clarify_message(intent.note, intent.question, lang, intent.labels)
        else:
            text_value = unsupported_message(intent.note, lang)
        if intent.kind == "clarify" and intent.note == "price_check" and not intent.reply:
            return t(lang, "clarify_price_check"), False
        if intent.kind == "clarify" and "more" in intent.labels and not intent.reply:
            return text_value, example_choices(lang)
        if intent.kind == "clarify" and intent.note == "mileage_stats" and not intent.reply:
            return text_value, False
        if intent.kind == "clarify" and intent.note in {"newer", "compare"} and not intent.reply:
            return text_value, False
        if intent.kind == "clarify" and intent.note not in {"currency", "correction", "price_check"}:
            return text_value, budget_choices(lang)
        if intent.kind == "clarify" and intent.note == "correction":
            return text_value, False
        if intent.kind == "chitchat" and not intent.reply and intent.labels:
            # small talk the parser recognised (no Gemini): a short reply, what the bot can do, examples
            return text_value, example_choices(lang)
        if intent.kind == "chitchat":
            return text_value, False  # small talk ("thanks") needs no buttons
        return text_value, example_choices(lang)
    return unsupported_message(intent.note, lang), example_choices(lang)


_DEFAULT_LLM = object()
DB_KINDS = {"find", "model_stats", "price_check", "mileage_stats", "compare", "trends", "status", "latest", "suggest", "liquidity",
            "deals"}

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
        llm = (lambda message, previous, history=None, language="", chat_kind="":
               telegram_llm.interpret(message, previous, llm_config, history=history, language=language,
                                      chat_kind=chat_kind)
               ) if llm_config else None
    if phraser is _DEFAULT_LLM:
        phraser = (lambda message, facts, history=None, mode="", language="", chat_kind="":
                   telegram_llm.phrase(message, facts, llm_config, history=history, mode=mode, language=language,
                                       chat_kind=chat_kind)
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
    wrote in, else what was last written in this chat, else their Telegram app language, else
    TELEGRAM_DEFAULT_LANGUAGE (English when unset). Bosnian, Croatian and Serbian all map to Bosnian."""
    detected = note_language(scope_key, text_value) if isinstance(scope_key, tuple) else detect_language(text_value)
    if detected:
        _remember_language(scope_key, detected)
        return detected
    chat_key = ("chat", scope_key[0]) if isinstance(scope_key, tuple) and scope_key else None
    for key in (scope_key, chat_key):
        entry = _LANGS.get(key) if key else None
        if entry and time.monotonic() - entry[1] <= LANGUAGE_TTL_SECONDS:
            return entry[0]
    return telegram_language((sender or {}).get("language_code")) or default_language()


def note_language(scope_key, text_value):
    """Remember the language of a message for this person and for the whole chat (group fallback)."""
    detected = detect_language(text_value)
    if detected and isinstance(scope_key, tuple) and scope_key:
        _remember_language(scope_key, detected)
        _remember_language(("chat", scope_key[0]), detected)
    return detected


def _hint_unconfigured_chat(chat, user_id, message, callback, config, call):
    """Tell an *allowed* person that this group is not configured, and which id to add.

    Strangers, private chats, button presses and ordinary group chatter stay completely silent.
    The reply carries only the chat id, which the person can already see in Telegram.
    """
    if callback or user_id not in config.allowed_user_ids or chat.get("type") not in {"group", "supergroup"}:
        return
    body = message.get("text")
    migrated = message.get("migrate_from_chat_id") in config.allowed_chats  # the group was upgraded
    addressed = isinstance(body, str) and body.strip() and (
        body.lstrip().startswith("/") or is_addressed(message, config))
    if not (migrated or addressed):
        return
    LOG.warning("telegram chat not configured: chat_type=%s chat_id=%s migrated=%s",
                chat.get("type"), chat.get("id"), bool(migrated))
    sender = message.get("from") or {}
    lang = detect_language(body if isinstance(body, str) else "") or telegram_language(
        sender.get("language_code")) or default_language()
    try:
        call(config, "sendMessage", {"chat_id": chat["id"], "text": t(lang, "chat_not_set_up", chat_id=chat["id"]),
                                     "disable_web_page_preview": True})
    except TelegramAPIError:
        pass


# In a group the bot normally answers only when tagged or replied to. After it has answered someone it keeps
# listening to *that person* for a few minutes, so a conversation doesn't need a tag on every line. In-process
# and best-effort like the other memory: per (chat, user), bounded, expiring. Private chats are always open.
GROUP_WINDOW_SECONDS = 300
_ACTIVE = {}


def group_window_seconds():
    raw = (os.environ.get("TELEGRAM_GROUP_WINDOW_SECONDS") or "").strip()
    try:
        return max(0, min(int(raw), 3600)) if raw else GROUP_WINDOW_SECONDS
    except ValueError:
        return GROUP_WINDOW_SECONDS


def _mark_conversation(key):
    window = group_window_seconds()
    if not key or window <= 0:
        return
    now = time.monotonic()
    for stale in [k for k, at in _ACTIVE.items() if now - at > window]:
        _ACTIVE.pop(stale, None)
    if len(_ACTIVE) >= SCOPE_MAX_ENTRIES and key not in _ACTIVE:
        _ACTIVE.pop(min(_ACTIVE, key=_ACTIVE.get), None)
    _ACTIVE[key] = now


def _in_conversation(key):
    window = group_window_seconds()
    at = _ACTIVE.get(key)
    return bool(key) and window > 0 and at is not None and time.monotonic() - at <= window


# Unanswered group messages, kept briefly so that a vague mention ("@bot hey", "probaj ovo") can
# answer what the person asked just before. In-process, per (chat, user), at most a few, expiring
# with the group window. Only messages from allowlisted people in configured groups get here.
PENDING_MAX = 3
_PENDING = {}


def _pending_ttl():
    return group_window_seconds() or GROUP_WINDOW_SECONDS


def _remember_pending(key, text_value):
    now = time.monotonic()
    for stale in [k for k, items in _PENDING.items() if not items or now - items[-1][1] > _pending_ttl()]:
        _PENDING.pop(stale, None)
    if len(_PENDING) >= SCOPE_MAX_ENTRIES and key not in _PENDING:
        _PENDING.pop(min(_PENDING, key=lambda k: _PENDING[k][-1][1]), None)
    items = [item for item in _PENDING.get(key, []) if now - item[1] <= _pending_ttl()]
    _PENDING[key] = (items + [((text_value or "")[:MAX_QUESTION_CHARS], now)])[-PENDING_MAX:]


def _take_pending(key):
    now = time.monotonic()
    return [text_value for text_value, at in _PENDING.pop(key, []) if now - at <= _pending_ttl()]


MAX_QUESTION_CHARS = 400
_VAGUE_KINDS = {"unsupported", "chitchat", "clarify", "ignore"}
_STRONG_KINDS = {"suggest", "liquidity", "compare", "model_stats", "price_check", "trends", "deals"}
_CAR_CUE = re.compile(r"\b(?:auto|auta|autom|automobil\w*|kola|car|cars|oglas\w*|vozil\w*)\b")


def looks_like_car_question(text_value):
    """Deterministic and cheap: is this group message clearly about cars (not chat between people)?"""
    intent = parse_question(text_value)
    if intent.kind in _STRONG_KINDS or (intent.kind == "clarify" and intent.note == "km_unit"):
        return True
    f = intent.filters
    if intent.kind == "find":
        return bool(f.numeric() or f.family or _CAR_CUE.search(fold(strip_mentions(text_value or ""))))
    return False


def with_pending_question(key, text_value):
    """For a vague mention, the person's most recent unanswered car question (else the text itself)."""
    stripped = strip_mentions(text_value or "")
    stripped = re.sub(r"^/ask(?:@\w+)?\s*", "", stripped, flags=re.I).strip()
    intent = parse_question(stripped)
    vague = intent.kind in _VAGUE_KINDS and not intent.note and len(stripped.split()) <= 8
    pending = _take_pending(key)
    if not vague or not pending:
        return text_value
    for candidate in reversed(pending):
        if looks_like_car_question(candidate):
            return candidate
    for candidate in reversed(pending):
        if parse_question(candidate).kind not in _VAGUE_KINDS:
            return candidate
    return text_value


# A group counts as "open" (car questions are answered without a tag) only when Telegram confirms
# that every member except this bot is on TELEGRAM_ALLOWED_USER_IDS and the group is small.
# Checked with getChatMemberCount + getChatMember (read-only), cached per group for an hour.
OPEN_GROUP_MAX_MEMBERS = 10
OPEN_GROUP_TTL_SECONDS = 3600
_OPEN = {}
_MEMBER_STATUSES = {"creator", "administrator", "member", "restricted"}


def is_open_group(chat, config, call):
    chat_id = chat.get("id")
    if chat.get("type") not in {"group", "supergroup"} or chat_id not in config.allowed_chats:
        return False
    cached = _OPEN.get(chat_id)
    now = time.monotonic()
    if cached and now - cached[1] <= OPEN_GROUP_TTL_SECONDS:
        return cached[0]
    verdict = False
    try:
        count = call(config, "getChatMemberCount", {"chat_id": chat_id})
        if isinstance(count, int) and not isinstance(count, bool) and 2 <= count <= OPEN_GROUP_MAX_MEMBERS:
            members = 0
            for user_id in sorted(config.allowed_user_ids):
                member = call(config, "getChatMember", {"chat_id": chat_id, "user_id": user_id})
                if isinstance(member, dict) and member.get("status") in _MEMBER_STATUSES:
                    members += 1
            verdict = members + 1 == count  # everyone else is allowlisted (+1 for this bot)
    except TelegramAPIError:
        verdict = False
    if len(_OPEN) >= SCOPE_MAX_ENTRIES:
        _OPEN.clear()
    _OPEN[chat_id] = (verdict, now)
    return verdict


ANONYMOUS_ADMIN_ID = 1087968824  # Telegram's "GroupAnonymousBot": what an anonymous group admin appears as


def _handle_unauthorized(chat, user_id, message, callback, config, call):
    """Stay silent, except to help someone who is clearly trying to use the bot.

    Strangers, private chats, buttons and chatter get nothing. A line (no ids, no text) is logged when an
    addressed message is ignored, so "why didn't it answer?" can be answered from the logs.
    """
    if callback:
        return
    body = message.get("text")
    addressed = isinstance(body, str) and body.strip() and (
        body.lstrip().startswith("/") or is_addressed(message, config))
    in_group = chat.get("type") in {"group", "supergroup"}
    if user_id == ANONYMOUS_ADMIN_ID and in_group and chat.get("id") in config.allowed_chats and addressed:
        LOG.warning("telegram update ignored: anonymous admin in a configured group")
        lang = detect_language(body) or default_language()
        try:
            call(config, "sendMessage", {"chat_id": chat["id"], "text": t(lang, "anonymous_admin"),
                                         "disable_web_page_preview": True})
        except TelegramAPIError:
            pass
        return
    if user_id in config.allowed_user_ids and in_group:
        _hint_unconfigured_chat(chat, user_id, message, callback, config, call)
        return
    if addressed:
        LOG.warning("telegram update ignored: chat_type=%s chat_configured=%s user_allowed=%s",
                    chat.get("type"), chat.get("id") in config.allowed_chats, user_id in config.allowed_user_ids)


def _send_placeholder(call, config, chat_id, lang):
    """Send "looking…" right away and return its message id, so the answer can replace it."""
    try:
        result = call(config, "sendMessage", {"chat_id": chat_id, "text": t(lang, "looking"),
                                              "disable_web_page_preview": True})
    except TelegramAPIError:
        return None
    return result.get("message_id") if isinstance(result, dict) else None


def message_payload(chat_id, text_value):
    """sendMessage fields for a reply. ``Html`` goes out with parse_mode=HTML (or as plain text if it
    would not fit, since cutting HTML could break a tag); link previews are always off."""
    payload = {"chat_id": chat_id, "disable_web_page_preview": True,
               "link_preview_options": {"is_disabled": True}}
    if isinstance(text_value, Html) and len(text_value) <= MAX_MESSAGE:
        payload.update(text=str(text_value), parse_mode="HTML")
    else:
        plain = html_to_text(text_value) if isinstance(text_value, Html) else str(text_value)
        payload["text"] = plain[:MAX_MESSAGE]
    return payload


def _deliver(call, config, chat_id, text_value, markup, replace_id, delete_on_failure=True):
    """Replace an existing message with the answer; if that fails, send a new message instead.

    ``delete_on_failure`` removes the old message after a failed edit. It is right for the
    "looking…" placeholder and wrong for a message the user is still looking at.
    """
    payload = message_payload(chat_id, text_value)
    if markup:
        payload["reply_markup"] = markup
    if replace_id is not None:
        try:
            call(config, "editMessageText", {**payload, "message_id": replace_id})
            return
        except TelegramAPIError:
            if delete_on_failure:
                try:
                    call(config, "deleteMessage", {"chat_id": chat_id, "message_id": replace_id})
                except TelegramAPIError:
                    pass
    try:
        call(config, "sendMessage", payload)
    except TelegramAPIError:
        if "parse_mode" not in payload:
            raise
        # never lose an answer to a formatting problem: resend it as plain text
        plain = message_payload(chat_id, html_to_text(text_value))
        if "reply_markup" in payload:
            plain["reply_markup"] = payload["reply_markup"]
        call(config, "sendMessage", plain)


def process_update(update: dict[str, Any], engine, config: TelegramConfig, *, call=telegram_call,
                   llm=_DEFAULT_LLM, phraser=_DEFAULT_LLM):
    started = time.monotonic()
    budget = telegram_llm.start_message(started=started)  # Gemini gets a few seconds, then the DB answer goes out
    try:
        return _process_update(update, engine, config, call, llm, phraser, started)
    finally:
        telegram_llm.end_message(budget)


def _process_update(update, engine, config, call, llm, phraser, started):
    if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
        return {"handled": False, "reason": "invalid_update"}
    chat, user_id, message, callback = _identity(update)
    if not is_authorized(chat, user_id, config):
        _handle_unauthorized(chat, user_id, message, callback, config, call)
        return {"handled": False, "reason": "unauthorized"}
    chat_id = chat["id"]
    private = chat.get("type") == "private"
    chat_kind = "private" if private else "group"
    scope_key = (chat_id, user_id)
    history_key = scope_key if private else None
    sender = callback.get("from") or message.get("from") or {}
    callback_data = callback.get("data") if callback else None
    edit_id = None  # a button tap that refines a search edits that message instead of adding a new one
    trace = {"path": "button" if callback else "command"}
    intent = None
    free_text = None
    command, argument = "help", ""
    llm, phraser = _resolve_llm(llm, phraser)
    if callback_data:
        refine = parse_refine_page(callback_data)
        liquidity = parse_liquidity_callback(callback_data)
        suggest_budget = parse_suggest_callback(callback_data)
        follow_target = parse_follow_callback(callback_data)
        if follow_target:
            intent = Intent("follow_do", labels=follow_target)
            command = "follow"
        elif callback_data.startswith("u:"):
            intent = Intent("unfollow", note=callback_data[2:])
            command = "unfollow"
        elif callback_data == "following":
            intent = Intent("following")
            command = "following"
        elif callback_data.startswith("c:") and _valid_listing_id(callback_data[2:]):
            intent = Intent("save_car", note=callback_data[2:])
            command = "save"
        elif callback_data.startswith("x:") and _valid_listing_id(callback_data[2:]):
            intent = Intent("remove_car", note=callback_data[2:])
            command = "remove"
        elif callback_data == "shortlist":
            intent = Intent("shortlist")
            command = "shortlist"
        elif refine:
            intent = Intent("find", refine[0], offset=refine[1])
            command = "find"
            if refine[1] == 0 and isinstance(message.get("message_id"), int):
                edit_id = message["message_id"]
        elif liquidity is not None:
            intent = Intent("liquidity", liquidity)
            command = "liquidity"
        elif suggest_budget:
            intent = Intent("suggest", Filters(budget=suggest_budget))
            command = "suggest"
        elif callback_data.startswith("trends:"):
            command, argument = "trends", callback_data.partition(":")[2]
        elif callback_data in {"status", "latest", "drops"}:
            command, argument = callback_data, ""
        elif callback_data == "search":
            intent = Intent("clarify")
            command = "search"
        call(config, "answerCallbackQuery", {"callback_query_id": callback.get("id")})
    else:
        body = message.get("text")
        if not isinstance(body, str) or not body.strip():
            return {"handled": False, "reason": "no_text"}
        if body.lstrip().startswith("/"):
            command, argument = command_for(body)
            if command == "llm":
                intent = Intent("llm_check")
            elif command == "follow":
                intent = (Intent("follow", Filters(search=clean_search(argument))) if clean_search(argument)
                          else Intent("following"))
            elif command == "following":
                intent = Intent("following")
            elif command == "shortlist":
                intent = Intent("shortlist")
            elif command == "ask":
                free_text = argument
        else:
            addressed = is_addressed(message, config)
            if not private:
                note_language(scope_key, strip_mentions(body))
            if addressed and not private:
                free_text = with_pending_question(scope_key, body)
                if free_text != body:
                    trace["pending"] = True
            elif addressed or (not private and _in_conversation(scope_key)):
                free_text = body
            elif looks_like_car_question(body) and is_open_group(chat, config, call):
                free_text = body
            else:
                if not private:
                    _remember_pending(scope_key, body)
                return {"handled": False, "reason": "not_addressed"}
    lang = resolve_language(scope_key, free_text, sender)
    placeholder_id = None
    if free_text is not None:
        if llm is not None or phraser is not None:
            placeholder_id = _send_placeholder(call, config, chat_id, lang)
        intent = _understand(free_text, message, scope_key, llm, _recall_history(history_key), lang,
                             chat_kind, trace)
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
    trace["phrasing"] = "off"
    try:
        if intent is not None and intent.kind == "llm_check":
            response = telegram_llm.check_message(telegram_llm.LLMConfig.from_env())
            command, markup = "llm", False
        elif intent is not None:
            response, markup = answer_intent(engine, intent, config, lang=lang)
            if private or free_text is not None:
                _remember_scope(scope_key, _with_page(intent, markup))
            if (free_text is not None and phraser is not None and intent.kind in DB_KINDS
                    and not isinstance(response, NoMatch)):  # "did you mean ...?" is sent as written
                facts = html_to_text(response) if isinstance(response, Html) else response
                must_mention = getattr(response, "must_mention", "")
                telegram_llm.PHRASE_STATUS.set("")
                phrased = phraser(free_text, facts, _recall_history(history_key), mode=intent.kind,
                                  language=lang, chat_kind=chat_kind)
                if phrased and not telegram_llm.reply_keeps_range(phrased[0], intent.filters):
                    phrased = None  # it narrowed "50.000–60.000 KM" to one end: send the database answer
                    telegram_llm.PHRASE_STATUS.set("rejected: range not kept")
                if phrased and must_mention and not all(word in fold(phrased[0]) for word in fold(must_mention).split()):
                    phrased = None  # it hid which model was searched after a typo was repaired
                    telegram_llm.PHRASE_STATUS.set("rejected: searched model not named")
                trace["phrasing"] = "used" if phrased else "fallback"
                trace["phrasing_reason"] = "" if phrased else telegram_llm.PHRASE_STATUS.get()
                if phrased:
                    chat_text, show_details = phrased
                    # the user wants to see them; a resale answer always ends with its list, never a chat reply alone
                    show_details = show_details or intent.kind in {"suggest", "deals", "liquidity"}
                    if isinstance(response, Html):
                        response = Html(esc(chat_text) + ("\n\n" + response if show_details else ""))
                    else:
                        response = chat_text + ("\n\n" + response if show_details else "")
                    response = _with_notes(response, intent, lang)  # a rewritten reply still says what was skipped
                    if intent.kind == "find" and not show_details and isinstance(markup, Page) and markup.items:
                        # the listings are not on screen, so the first button should show page one
                        markup = Page(markup.filters, 0, markup.min_year, markup.max_km,
                                      older_hidden=markup.older_hidden, total=markup.total)
                        _remember_scope(scope_key, replace(intent, offset=-PAGE_SIZE, page_total=markup.total))
            elif free_text is not None and intent.reply:
                chat_text = intent.reply
        else:
            response = render_command(engine, command, argument, lang)
            if command == "start":
                markup = example_choices(lang)
    except SQLAlchemyError as error:
        response, markup = t(lang, "db_unavailable"), False
        trace["error"] = type(error).__name__
    try:
        _deliver(call, config, chat_id, response, keyboard(config, markup, lang),
                 placeholder_id if placeholder_id is not None else edit_id,
                 delete_on_failure=placeholder_id is not None)
    except TelegramAPIError:
        trace["error"] = "TelegramAPIError"
        _log_interaction(engine, config, trace, started, chat_kind, user_id, free_text, callback_data, intent,
                         command, markup)
        raise
    _log_interaction(engine, config, trace, started, chat_kind, user_id, free_text, callback_data, intent,
                     command, markup)
    if free_text is not None:
        _remember_turn(history_key, free_text, chat_text or html_to_text(response).split("\n", 1)[0])
    if not private:
        _mark_conversation(scope_key)  # keep listening to this person for a few minutes
    return {"handled": True, "command": command}


def _log_interaction(engine, config, trace, started, chat_kind, user_id, free_text, button, intent, command,
                     markup):
    """Hand one interaction to the best-effort log (after the reply is out). Never raises."""
    try:
        path = trace.get("path", "command")
        if trace.get("pending"):
            path = "pending_" + path
        result = shown = None
        if isinstance(markup, Page):
            result, shown = markup.total, len(markup.items)
        if intent is None:
            intent = Intent(command or "command")
        telegram_log.record(engine, telegram_log.build_record(
            chat_kind=chat_kind, user_id=user_id, secret=telegram_log.salt(config), message_text=free_text,
            button=button, intent=intent, path=path, result_count=result, shown_count=shown,
            phrasing=trace.get("phrasing"), phrasing_reason=trace.get("phrasing_reason"),
            latency_ms=(time.monotonic() - started) * 1000, error=trace.get("error"),
            bot_username=getattr(config, "bot_username", None)))
    except Exception as error:
        LOG.warning("bot interaction not logged: %s", type(error).__name__)


def _needs_interpreter(intent, text_value):
    """True when Gemini should decide: unreadable, conversational, or a bare search word.

    A message with a price, year or mileage is a clear search the free parser reads itself.
    A search-only message ("golf", "hvala ti", "bok") is ambiguous, so Gemini picks between
    a search and small talk. Without Gemini the parser's reading is used as before.
    """
    if intent.note == "more":
        return False  # "pokaži još" is the next page of the last search, whatever Gemini would make of it
    if intent.kind in {"ignore", "clarify", "chitchat", "unsupported"}:
        return not intent.note
    # a bare model name or a price-statistics question without numbers ("koliko je X") may be small talk
    return intent.kind in {"find", "model_stats"} and not intent.filters.numeric()


def _understand(text_value, message, scope_key, llm, history=None, lang=DEFAULT_LANGUAGE, chat_kind="", trace=None):
    """Deterministic parse first; Gemini for anything conversational or unreadable."""
    trace = {} if trace is None else trace
    trace["path"] = "parser"
    context = reply_context(message)
    remembered = _recall_scope(scope_key) if scope_key else None
    correction = is_correction(text_value)
    if chat_kind == "group" and not correction:
        remembered = None  # in a group only a correction may reuse the person's previous question
    if context is None and remembered is not None and (correction or looks_like_followup(text_value)):
        context = remembered
    intent = parse_question(text_value, context)
    stated = stated_filters(text_value, context).budget
    budget = _recall_budget(scope_key) if scope_key else None
    if (budget and intent.kind in {"clarify", "unsupported"} and not intent.note and not intent.reply and not stated
            and wants_recommendation(text_value)):
        # "Da ali koji auto, koji modeli": the budget was given earlier, so use it instead of asking again
        intent = Intent("suggest", Filters(budget=budget), labels=("budget_reused",))
    ask_gemini = llm is not None and _needs_interpreter(intent, text_value)  # decided on the message itself
    if budget and not stated and chat_kind != "group" and _reuses_budget(intent):
        # "imam 20k" ... "a šta se brzo prodaje" / "ok a dizel": the same budget, said once (a private chat
        # only: in a group only a correction may reuse the person's earlier question)
        intent = replace(intent, filters=replace(intent.filters, budget=budget),
                         labels=intent.labels + ("budget_reused",))
    if scope_key:
        _remember_budget(scope_key, intent.filters.budget or stated)
    if ask_gemini:
        interpreted = llm(text_value, context or remembered, history or [], language=lang, chat_kind=chat_kind)
        interpreted = _grounded(interpreted, text_value, context or remembered)
        if interpreted is not None:
            trace["path"] = "gemini"
            return interpreted
        trace["path"] = "parser_after_gemini_failed"
    return intent


def _reuses_budget(intent):
    """True for an answer that should use a budget stated earlier in the chat: a resale, shortlist or deals
    question, or a search without a model (fuel, gearbox, year only), when it states no price bound itself."""
    filters = intent.filters
    if filters.budget or filters.price_min or intent.note in {"correction", "more"}:
        return False
    if intent.kind in {"liquidity", "deals"}:
        return True
    return intent.kind == "find" and not filters.search and not intent.offset and not filters.is_empty()


def _grounded(interpreted, text_value, previous):
    """Gemini's reading is used only when its price and year bounds agree with what the message
    states (as the free parser reads it); otherwise the parser's own reading is used."""
    if interpreted is None or interpreted.kind not in {"find", "suggest", "liquidity"}:
        return interpreted
    return interpreted if telegram_llm.bounds_match(interpreted.filters, text_value, previous) else None
