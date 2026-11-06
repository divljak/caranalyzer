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


def _time(value):
    value = _utc(value)
    return value.astimezone(SARAJEVO).strftime("%d %b, %H:%M") if value else "unknown"


def _money(value):
    return f"{int(round(value)):,} KM".replace(",", ".") if value is not None else "price unavailable"


def _one(connection, sql, params=None):
    row = connection.execute(text(sql), params or {}).mappings().first()
    return dict(row) if row else None


def _all(connection, sql, params=None):
    return [dict(row) for row in connection.execute(text(sql), params or {}).mappings()]


def _statement_timeout(connection):
    connection.execute(text("SET LOCAL statement_timeout = '8s'"))


def status_message(engine):
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
        return "Car Analyzer has no finished collector run yet."
    quality = run["status"].upper()
    lines = [
        f"Car Analyzer · {quality}",
        f"Latest attempt: {_time(run['finished_at'] or run['started_at'])}",
        (f"{run['observations']} observations · "
         f"{run['new_listings']} first observed by us · {run['errors']} errors"),
        (f"Source visits: {run['search_attempted']} search · "
         f"{run['detail_attempted']} detail ({run['details_completed']} completed)"),
        f"Latest stored observation: {_time(latest_snapshot)}",
        f"Last fully clean run: {_time(clean)}",
    ]
    if observed and observed["started_at"] != run["started_at"]:
        lines.append(
            f"Latest attempt with observations: {_time(observed['finished_at'])} "
            f"({observed['status']}, {observed['observations']} observations)")
    lines.extend([
        "",
        "Freshness describes our observations, not when ads were posted.",
        "Search absence is not evidence that a car sold.",
    ])
    return "\n".join(lines)


def trends_message(engine, days):
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
        f"Observed asking-price changes · last {days} days",
        f"Sample: {result['sample_size']} listings observed; {result['priced_sample']} with usable prices",
        f"Recorded transitions: {result['drops']} drops · {result['increases']} increases",
        f"Freshness: observations through {_time(result['newest'])}",
        "",
        "This is a bounded observed sample, not a full-market index. Asking prices are not sale prices.",
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


def budget_message(engine, argument):
    parts = argument.strip().split(maxsplit=1)
    amount = _parse_amount(parts[0]) if parts else None
    if amount is None:
        return "Use /budget AMOUNT [make or model], for example: /budget 25000 Golf"
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
    title = f"Recently observed at or below {_money(amount)}"
    if search:
        title += f" · {search}"
    if not matches:
        return title + "\nNo matching listings in the last 90 days."
    lines = [title, f"Showing {len(matches)} database matches:"]
    for item in matches:
        label = " ".join(value for value in (item["make"], item["model"]) if value) or "Unparsed model"
        year = f" · {item['year']}" if item["year"] else ""
        mileage = f" · {int(item['mileage']):,} km".replace(",", ".") if item["mileage"] is not None else ""
        lines.append(
            f"• {label}{year} · {_money(item['price'])}{mileage}\n"
            f"  Observed {_time(item['last_verified_at'])} · https://olx.ba/artikal/{item['listing_id']}")
    lines.extend(["", "These are observed listings, not purchase recommendations or verified availability."])
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


def _cohort_lines(label, result):
    lines = [
        label,
        f"Sample: {result['sample_size']} listings · {result['priced_sample']} priced",
        (f"Median asking: {_money(result['median'])} · range "
         f"{_money(result['minimum'])}–{_money(result['maximum'])}"),
    ]
    if result["priced_sample"] < THIN_SAMPLE:
        lines.append("Thin sample: fewer than 5 priced listings, so treat these figures with caution.")
    return lines


def compare_message(engine, argument):
    labels = [value.strip() for value in re.split(r"\s*(?:\||\bvs\.?\b)\s*", argument, flags=re.I) if value.strip()]
    if len(labels) != 2:
        return "Use /compare MAKE MODEL | MAKE MODEL, for example: /compare VW Golf | Škoda Octavia"
    results = []
    with engine.begin() as connection:
        _statement_timeout(connection)
        for label in labels:
            results.append((label, _cohort_stats(connection, label)))
    lines = ["Database comparison · observed in the last 90 days"]
    newest = None
    for label, result in results:
        newest = max(filter(None, (newest, _utc(result["newest"]))), default=None)
        lines.extend(["", *_cohort_lines(label, result)])
    lines.extend([
        "",
        f"Freshness: observations through {_time(newest)}",
        "Same broad 90-day window; no mileage/year normalization. Asking prices are not sale prices.",
    ])
    return "\n".join(lines)


def model_stats_message(engine, label):
    with engine.begin() as connection:
        _statement_timeout(connection)
        result = _cohort_stats(connection, label)
        note = _freshness_note(connection)
    lines = [f"Asking prices · {label} · observed in the last 90 days", ""]
    if not result["sample_size"]:
        lines.append("No matching listings in the last 90 days.")
    else:
        lines.extend(_cohort_lines("Database evidence", result))
        lines.append(f"Freshness: observations through {_time(result['newest'])}")
    if note:
        lines.extend(["", note])
    lines.extend(["", "No mileage/year normalization. Asking prices are not sale prices."])
    return "\n".join(lines)


def _freshness_note(connection):
    """Return a warning when evidence is missing, stale or from a partial run."""
    latest = connection.execute(text("SELECT max(observed_at) FROM listing_snapshots")).scalar()
    run = _one(connection, """
      SELECT status FROM website_collection_runs
      WHERE finished_at IS NOT NULL ORDER BY started_at DESC,id DESC LIMIT 1
    """)
    if latest is None:
        return "The database has no stored observations yet, so there is nothing to answer from."
    notes = []
    age = datetime.now(timezone.utc) - _utc(latest)
    if age.total_seconds() > STALE_AFTER_HOURS * 3600:
        notes.append(f"Stale data: the newest stored observation is {age.days} days old "
                     f"({_time(latest)}).")
    if run and run["status"] != "completed":
        notes.append(f"The latest collector attempt was {run['status'].upper()}, so coverage may be incomplete.")
    return " ".join(notes)


def _filter_title(filters):
    parts = [TITLE_FIND]
    if filters.search:
        parts.append(filters.search)
    if filters.budget:
        parts.append(f"up to {_money(filters.budget)}")
    if filters.year_min:
        parts.append(f"from {filters.year_min}")
    if filters.mileage_max:
        parts.append(f"up to {filters.mileage_max:,} km".replace(",", "."))
    return " · ".join(parts)


def find_message(engine, filters):
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
          ORDER BY c.last_verified_at DESC,c.year DESC NULLS LAST,c.price DESC,c.listing_id
          LIMIT 5
        """, {
            "budget": filters.budget,
            "year_min": filters.year_min,
            "mileage_max": filters.mileage_max,
            "search": filters.search,
            "pattern": _like_pattern(filters.search),
        })
        note = _freshness_note(connection)
    lines = [_filter_title(filters)]
    if not matches:
        lines.append("No matching listings in the last 90 days.")
        if filters.search and not (filters.budget or filters.year_min or filters.mileage_max):
            lines.append("If that was not a car make or model, send /help to see what I can answer.")
    else:
        total = matches[0]["total"]
        lines.append(f"Showing {len(matches)} of {total} database matches, most recently observed first:")
        for item in matches:
            label = " ".join(value for value in (item["make"], item["model"]) if value) or "Unparsed model"
            year = f" · {item['year']}" if item["year"] else ""
            mileage = f" · {int(item['mileage']):,} km".replace(",", ".") if item["mileage"] is not None else ""
            lines.append(
                f"• {label}{year} · {_money(item['price'])}{mileage}\n"
                f"  Observed {_time(item['last_verified_at'])} · https://olx.ba/artikal/{item['listing_id']}")
    if filters.year_min or filters.mileage_max:
        lines.extend(["", "Listings without a recorded year or mileage are not included when those filters are used."])
    if note:
        lines.extend(["", note])
    lines.extend(["", "These are observed asking prices, not recommendations or verified availability."])
    return "\n".join(lines)


def chitchat_message():
    return ("Hi! I answer questions about used cars from our olx.ba database, for example "
            "“Golf under 15000 KM”, “how much is an Octavia” or “Golf vs Octavia”. What are you looking for?")


def clarify_message(note="", question=""):
    if question:
        return question + "\n\n(I can only show what our database has observed; I can't pick a car for you.)"
    if note == "currency":
        return "Prices in our database are asking prices in KM. Please give the amount in KM, e.g. “Golf under 15000 KM”."
    return "\n".join([
        "I can't pick a car for you; I can only show what our database has observed.",
        "",
        "Tell me a budget and, if you like, a model, year or mileage limit. For example:",
        "• Golf under 15000 KM",
        "• Octavia from 2016 under 150000 km",
        "• compare Golf vs Octavia",
    ])


def unsupported_message(note=""):
    if note == "forecast":
        return ("I don't forecast prices or markets. I can only report what our database has "
                "observed, e.g. “trends last 30 days” or “how much is a Golf”.")
    return "\n".join([
        "I couldn't match that to something our database can answer, so I won't guess.",
        "",
        "I can answer questions about: status/freshness, latest finds, price changes (e.g. “trends last 30 days”), "
        "listings under a budget (“Golf under 15000 KM”), a model's asking prices (“how much is an Octavia”), "
        "and comparisons (“Golf vs Octavia”). Reply to one of my answers to refine it.",
    ])


def latest_message(engine):
    with engine.begin() as connection:
        _statement_timeout(connection)
        run = _one(connection, """
          SELECT status,started_at,finished_at,observations,new_listings,errors
          FROM website_collection_runs
          WHERE finished_at IS NOT NULL AND observations>0
          ORDER BY started_at DESC,id DESC LIMIT 1
        """)
        if not run:
            return "No collector run with observations is available."
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
        f"Latest observed run · {run['status'].upper()} · {_time(run['finished_at'])}",
        (f"{run['observations']} observations · {run['new_listings']} IDs first observed by us "
         f"· {run['errors']} errors"),
    ]
    if listings:
        lines.append("\nA few IDs first observed by us in this run:")
        for item in listings:
            label = " ".join(value for value in (item["make"], item["model"]) if value) or "Unparsed model"
            year = f" · {item['year']}" if item["year"] else ""
            lines.append(
                f"• {label}{year} · {_money(item['price'])}\n"
                f"  https://olx.ba/artikal/{item['listing_id']}")
    else:
        lines.append("\nNo first-observed IDs were attributable to this run window.")
    lines.extend(["", "First observed by us does not mean newly posted on OLX."])
    return "\n".join(lines)


def help_message():
    return "\n".join([
        "Car Analyzer reads our Supabase evidence only.",
        "",
        "/status — collector health and freshness",
        "/latest — latest observations and a few finds",
        "/trends 7 — observed price changes (1–90 days)",
        "/budget 25000 Golf — recently observed matches",
        "/compare Volkswagen Golf | Škoda Octavia — broad database comparison",
        "/ask Golf under 15000 KM from 2016 — ask in plain English or Bosnian",
        "/help — show this guide",
        "",
        "You can also @mention me or reply to one of my answers with a question, e.g. “Octavia under 20000 KM”, "
        "“how much is a Golf”, “Golf vs Octavia”, or a refinement such as “from 2018”. "
        "Database coverage is bounded; the bot never treats first seen as posted or absence as sold.",
    ])


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


def render_command(engine, command, argument):
    if command in {"start", "help"}:
        return help_message()
    if command == "status":
        return status_message(engine)
    if command == "latest":
        return latest_message(engine)
    if command == "trends":
        try:
            days = int(argument or "7")
        except ValueError:
            return "Use /trends DAYS with a number from 1 to 90."
        return trends_message(engine, days)
    if command == "budget":
        return budget_message(engine, argument)
    if command == "compare":
        return compare_message(engine, argument)
    return help_message()


def _round_budget(value):
    return max(1_000, int(round(value / 500.0)) * 500)


def refine_callback(filters):
    data = f"f:{filters.budget or 0}:{filters.year_min or 0}:{filters.mileage_max or 0}:{filters.search}"
    return data if len(data.encode()) <= 64 else None


def parse_refine_callback(data):
    parts = (data or "").split(":", 4)
    if len(parts) != 5 or parts[0] != "f":
        return None
    try:
        budget, year, mileage = (int(value) for value in parts[1:4])
    except ValueError:
        return None
    filters = Filters(clean_search(parts[4]), budget or None, year or None, mileage or None)
    in_range = (
        (filters.budget is None or 1_000 <= filters.budget <= 1_000_000)
        and (filters.year_min is None or 1980 <= filters.year_min <= 2035)
        and (filters.mileage_max is None or 1_000 <= filters.mileage_max <= 1_000_000)
    )
    return filters if in_range and not filters.is_empty() else None


def keyboard(config, filters=None):
    buttons = [
        [
            {"text": "Status", "callback_data": "status"},
            {"text": "Latest", "callback_data": "latest"},
        ],
        [
            {"text": "7-day trends", "callback_data": "trends:7"},
            {"text": "30-day trends", "callback_data": "trends:30"},
        ],
    ]
    if filters is not None and filters.budget:
        row = []
        for label, factor in (("Budget −10%", 0.9), ("Budget +10%", 1.1)):
            data = refine_callback(Filters(
                filters.search, _round_budget(filters.budget * factor),
                filters.year_min, filters.mileage_max))
            if data:
                row.append({"text": label, "callback_data": data})
        if row:
            buttons.insert(0, row)
    if config.market_url:
        buttons.append([{"text": "Open Market", "url": config.market_url}])
    return {"inline_keyboard": buttons}


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


def answer_intent(engine, intent, config):
    """Map a parsed Intent onto the existing read-only helpers. Returns (text, markup filters)."""
    if intent.kind == "status":
        return status_message(engine), None
    if intent.kind == "latest":
        return latest_message(engine), None
    if intent.kind == "trends":
        return trends_message(engine, intent.days), None
    if intent.kind == "find":
        return find_message(engine, intent.filters), intent.filters
    if intent.kind == "model_stats":
        return model_stats_message(engine, intent.filters.search), None
    if intent.kind == "compare":
        return compare_message(engine, " | ".join(intent.labels)), None
    if intent.kind == "chitchat":
        return chitchat_message(), None
    if intent.kind == "clarify":
        return clarify_message(intent.note, intent.question), None
    return unsupported_message(intent.note), None


_DEFAULT_LLM = object()


def _default_llm():
    config = telegram_llm.LLMConfig.from_env()
    if config is None:
        return None
    return lambda message, previous: telegram_llm.interpret(message, previous, config)


def process_update(update: dict[str, Any], engine, config: TelegramConfig, *, call=telegram_call,
                   llm=_DEFAULT_LLM):
    if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
        return {"handled": False, "reason": "invalid_update"}
    chat, user_id, message, callback = _identity(update)
    if not is_authorized(chat, user_id, config):
        return {"handled": False, "reason": "unauthorized"}
    chat_id = chat["id"]
    private = chat.get("type") == "private"
    scope_key = (chat_id, user_id)
    callback_data = callback.get("data") if callback else None
    intent = None
    command, argument = "help", ""
    if llm is _DEFAULT_LLM:
        llm = _default_llm()
    if callback_data:
        refine = parse_refine_callback(callback_data)
        if refine:
            intent = Intent("find", refine)
            command = "find"
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
                intent = _understand(argument, message, private and scope_key, llm)
                command = intent.kind
        elif is_addressed(message, config):
            intent = _understand(body, message, private and scope_key, llm)
            command = intent.kind
        else:
            return {"handled": False, "reason": "not_addressed"}
    if intent is not None and intent.kind == "ignore":
        return {"handled": False, "reason": "acknowledgement"}
    markup_filters = None
    try:
        if intent is not None and intent.kind == "llm_check":
            response = telegram_llm.check_message(telegram_llm.LLMConfig.from_env())
            command = "llm"
        elif intent is not None:
            response, markup_filters = answer_intent(engine, intent, config)
            if private:
                _remember_scope(scope_key, intent)
        else:
            response = render_command(engine, command, argument)
    except SQLAlchemyError:
        response = DB_UNAVAILABLE
    call(config, "sendMessage", {
        "chat_id": chat_id,
        "text": response[:MAX_MESSAGE],
        "disable_web_page_preview": True,
        "reply_markup": keyboard(config, markup_filters),
    })
    return {"handled": True, "command": command}


def _needs_interpreter(intent, text_value):
    """True when the parse is unreadable or just a loose sentence turned into a search term."""
    if intent.kind == "unsupported":
        return not intent.note
    if intent.kind != "find":
        return False
    f = intent.filters
    if f.budget or f.year_min or f.mileage_max:
        return False
    return len(f.search.split()) >= 4 or len((text_value or "").split()) > 5


def _understand(text_value, message, scope_key, llm):
    """Deterministic parse first; Gemini only when that cannot read the message."""
    context = reply_context(message)
    remembered = _recall_scope(scope_key) if scope_key else None
    if context is None and remembered is not None and looks_like_followup(text_value):
        context = remembered
    intent = parse_question(text_value, context)
    if llm is not None and _needs_interpreter(intent, text_value):
        interpreted = llm(text_value, context or remembered)
        if interpreted is not None:
            return interpreted
    return intent
