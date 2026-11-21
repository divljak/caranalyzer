"""Offline harness for the bot stress suite. No network, no Gemini, no database, no Telegram.

A message goes through the real ``telegram_bot.process_update`` (parser, follow-up memory, answer
routing, keyboard building) with:
  * ``llm=None, phraser=None``  -> the built-in parser only, Gemini is never called
  * a recording ``call`` function -> nothing is sent to Telegram; the outgoing payloads are captured
  * a fake SQLAlchemy engine      -> every SQL statement is recorded and answered from a tiny canned
                                     dataset (or empty); nothing touches a database
  * ``telegram_log.record`` stubbed -> nothing is written to the interaction log

Secrets: the config uses made-up test-only values; the environment is never read for tokens.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
import re
from unittest.mock import patch

import telegram_bot
from telegram_bot import TelegramConfig, process_update
from telegram_intent import Filters

ENV = {
    "TELEGRAM_BOT_TOKEN": "test-only-token",
    "TELEGRAM_WEBHOOK_SECRET": "test_only_webhook_secret_123",
    "TELEGRAM_CHAT_ID": "-5332163835",
    "TELEGRAM_ALLOWED_USER_IDS": "123, 456",
}
GROUP_CHAT_ID = -5332163835
USER_ID = 123


def make_config():
    return TelegramConfig.from_env(ENV)


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)

    def scalar(self):
        return next(iter(self.rows[0].values())) if self.rows else None


def canned_rows(sql, params, dataset):
    """Listings for the find query only; every other statement is answered empty."""
    if "recent_listings" in sql:  # trends: an aggregate row, so a quiet database still answers
        return [{"sample_size": 120, "priced_sample": 100, "drops": 9, "increases": 4,
                 "newest": datetime.now(timezone.utc) - timedelta(hours=2)}]
    now = datetime.now(timezone.utc) - timedelta(hours=3)
    if dataset == "rows" and re.match(r"\s*SELECT 1\b", sql):  # "is this a known model?": yes
        return [{"known": 1}]
    if dataset == "rows" and "max(observed_at) FROM listing_snapshots" in sql:  # freshness note: data is recent
        return [{"latest": now}]
    if "fresh_total" not in sql:
        if "GROUP BY" not in sql.upper() and "count(" in sql.lower():
            # a plain aggregate always returns one row, even over no listings: a zero-sample row
            aliases = re.findall(r"\bAS (\w+)", sql)
            quiet = {"median", "minimum", "maximum", "newest", "p25", "p75", "low", "high", "med_price", "med_km",
                     "median_price", "median_km", "average"}
            return [{a: (None if a in quiet else 0) for a in aliases}]
        return []
    if dataset == "empty":
        return []
    rows = []
    for i, (make, model, year, price, km) in enumerate([
            ("Volkswagen", "Golf 7", 2016, 17500, 160000),
            ("Škoda", "Octavia", 2015, 14900, 190000),
            ("Opel", "Astra", 2012, 8200, 210000)][: params.get("limit", 5)]):
        rows.append({"listing_id": f"10{i}", "make": make, "model": model, "year": year, "price": price,
                     "mileage": km, "last_verified_at": now, "fresh": True, "suspect": False,
                     "fresh_total": 3, "all_total": 3, "new_hidden": 0, "hint_hit": False,
                     "hint_total": 0, "total": 3,
                     # extra columns a richer row format may read (unused by main today)
                     "fuel": "Dizel", "fuel_type": "Dizel", "transmission": ["Automatik", "Manuelni", None][i],
                     "gearbox": ["Automatik", "Manuelni", None][i], "city": ["Sarajevo", "Tuzla", "Mostar"][i],
                     "location": ["Sarajevo", "Tuzla", "Mostar"][i]})
    return rows


class FakeConnection:
    def __init__(self, log, dataset):
        self.log, self.dataset = log, dataset

    def execute(self, statement, params=None):
        sql = str(statement)
        self.log.append((sql, dict(params or {})))
        return FakeResult(canned_rows(sql, params or {}, self.dataset))


class FakeEngine:
    def __init__(self, dataset="empty"):
        self.sql, self.dataset = [], dataset

    @contextmanager
    def begin(self):
        yield FakeConnection(self.sql, self.dataset)

    connect = begin


def order_by_of(sql):
    if "ORDER BY" not in sql:
        return ""
    tail = sql.rsplit("ORDER BY", 1)[1]  # the outermost (last) ORDER BY
    return " ".join(re.split(r"\bLIMIT\b", tail)[0].split())


def sort_kind(statements):
    """"price" when the listing query's ORDER BY ranks by price before recency, else "recency".

    Heuristic: looks at the ORDER BY of the (last) statement that selects listings with ``fresh_total``."""
    finds = [sql for sql, _ in statements if "fresh_total" in sql and "ORDER BY" in sql]
    if not finds:
        return None
    order = order_by_of(finds[0])
    before_recency = order.split("date(last_verified_at)")[0]
    return "price" if re.search(r"\bprice\b", before_recency) else "recency"


class Session:
    """One person's chat with the bot. ``chat`` is "private" or "group"."""

    def __init__(self, chat="private", dataset="rows", user_id=USER_ID):
        self.chat, self.dataset, self.user_id = chat, dataset, user_id
        self.config = make_config()
        self.engine = FakeEngine(dataset)
        self.update_id = 0
        self.last_bot_message = None

    def say(self, text, reply=False):
        """Send one text message; returns a dict describing what the bot did."""
        if self.update_id == 0:  # a fresh person: forget every earlier conversation
            for memory in (telegram_bot._SCOPES, telegram_bot._HISTORY, telegram_bot._LANGS, telegram_bot._ACTIVE):
                memory.clear()
        self.update_id += 1
        chat = ({"id": self.user_id, "type": "private"} if self.chat == "private"
                else {"id": GROUP_CHAT_ID, "type": "supergroup"})
        message = {"message_id": 10 + self.update_id, "text": text, "chat": chat,
                   "from": {"id": self.user_id, "language_code": "bs"}}
        if self.chat == "group" and "@" not in text and not reply and not text.startswith("/"):
            message["text"] = "@testbot " + text
        if reply and self.last_bot_message:
            message["reply_to_message"] = {"text": self.last_bot_message, "from": {"is_bot": True}}
        return self._run({"update_id": self.update_id, "message": message})

    def _run(self, update):
        sent, intents = [], []
        self.engine.sql.clear()

        def call(config, method, payload, **kw):
            sent.append((method, payload))
            return {"message_id": 900 + len(sent)}

        real_answer = telegram_bot.answer_intent

        def spy(engine, intent, config, lang=telegram_bot.DEFAULT_LANGUAGE):
            intents.append(intent)
            return real_answer(engine, intent, config, lang=lang)

        error = None
        with patch.dict(os.environ, {}, clear=False), patch("telegram_log.record"), \
                patch.object(telegram_bot, "answer_intent", spy):
            for name in ("GEMINI_API_KEY", "TELEGRAM_DEFAULT_LANGUAGE", "TELEGRAM_GROUP_WINDOW_SECONDS"):
                os.environ.pop(name, None)
            try:
                result = process_update(update, self.engine, self.config, call=call, llm=None, phraser=None)
            except Exception as exc:  # a crash is a finding, not a harness failure
                result, error = {"handled": False}, f"{type(exc).__name__}: {exc}"
        replies = [p for m, p in sent if m in {"sendMessage", "editMessageText"}]
        payload = replies[-1] if replies else {}
        text = payload.get("text", "") if payload else ""
        buttons = []
        for row in ((payload.get("reply_markup") or {}).get("inline_keyboard") or []) if payload else []:
            buttons.extend(b.get("text", "") for b in row)
        if text:
            self.last_bot_message = re.sub(r"<[^>]+>", "", text)
        intent = intents[-1] if intents else None
        return {"handled": result.get("handled"), "reason": result.get("reason"), "intent": intent,
                "text": re.sub(r"<[^>]+>", "", text), "buttons": buttons, "sql": list(self.engine.sql),
                "sort": sort_kind(self.engine.sql), "error": error}
