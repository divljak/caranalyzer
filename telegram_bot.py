"""Private Telegram interface backed only by Car Analyzer database evidence."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hmac
import os
import re
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import text


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
        return cls(
            bot_token=required["TELEGRAM_BOT_TOKEN"],
            webhook_secret=secret,
            chat_id=chat_id,
            allowed_user_ids=users,
            market_url=market_url,
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


def compare_message(engine, argument):
    labels = [value.strip() for value in re.split(r"\s*(?:\||\bvs\.?\b)\s*", argument, flags=re.I) if value.strip()]
    if len(labels) != 2:
        return "Use /compare MAKE MODEL | MAKE MODEL, for example: /compare VW Golf | Škoda Octavia"
    results = []
    with engine.begin() as connection:
        _statement_timeout(connection)
        for label in labels:
            result = _one(connection, """
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
                AND lower(concat_ws(' ',coalesce(l.make,c.make),coalesce(l.model,c.model))) LIKE :pattern
            """, {"pattern": f"%{label.lower()}%"})
            results.append((label, result))
    lines = ["Database comparison · observed in the last 90 days"]
    newest = None
    for label, result in results:
        newest = max(filter(None, (newest, _utc(result["newest"]))), default=None)
        lines.extend([
            "",
            label,
            f"Sample: {result['sample_size']} listings · {result['priced_sample']} priced",
            (f"Median asking: {_money(result['median'])} · range "
             f"{_money(result['minimum'])}–{_money(result['maximum'])}"),
        ])
    lines.extend([
        "",
        f"Freshness: observations through {_time(newest)}",
        "Same broad 90-day window; no mileage/year normalization. Asking prices are not sale prices.",
    ])
    return "\n".join(lines)


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
        "/help — show this guide",
        "",
        "You can also say “status”, “latest”, or “trends”. Database coverage is bounded; the bot never treats first seen as posted or absence as sold.",
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


def keyboard(config):
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
    return chat.get("id"), sender.get("id"), message, callback


def process_update(update: dict[str, Any], engine, config: TelegramConfig, *, call=telegram_call):
    if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
        return {"handled": False, "reason": "invalid_update"}
    chat_id, user_id, message, callback = _identity(update)
    if chat_id != config.chat_id or user_id not in config.allowed_user_ids:
        return {"handled": False, "reason": "unauthorized"}
    callback_data = callback.get("data") if callback else None
    if callback_data:
        if callback_data.startswith("trends:"):
            command, argument = "trends", callback_data.partition(":")[2]
        elif callback_data in {"status", "latest"}:
            command, argument = callback_data, ""
        else:
            command, argument = "help", ""
        call(config, "answerCallbackQuery", {"callback_query_id": callback.get("id")})
    else:
        command, argument = command_for(message.get("text"))
    response = render_command(engine, command, argument)
    call(config, "sendMessage", {
        "chat_id": config.chat_id,
        "text": response[:MAX_MESSAGE],
        "disable_web_page_preview": True,
        "reply_markup": keyboard(config),
    })
    return {"handled": True, "command": command}
