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


# Make / model labels "seen in the last 90 days" for the "rows" dataset: the bot's known-model check
# (``_is_known_model``, a LIKE over these) and its typo / dropped-word repair (``resolve_search`` over
# ``_model_labels``) run against this list, as they do against the real table. The v1 harness answered "known"
# for any search, so a city or greeting word that leaked into the model never showed its visible effect
# ("Nisam prepoznao „golf iz mostara“, pa tražim „golf“" or "did you mean"). Display label, listings seen.
CATALOGUE = [
    ("Volkswagen Golf", 900), ("Volkswagen Passat", 700), ("Volkswagen Polo", 300), ("Volkswagen Caddy", 250),
    ("Volkswagen Touran", 200), ("Volkswagen Tiguan", 220), ("Volkswagen Jetta", 80), ("Volkswagen Touareg", 90),
    ("Volkswagen Up", 30), ("Volkswagen Transporter", 120), ("Škoda Octavia", 650), ("Škoda Fabia", 300),
    ("Škoda Superb", 200), ("Škoda Rapid", 90), ("Škoda Kodiaq", 60), ("Opel Astra", 400), ("Opel Corsa", 250),
    ("Opel Insignia", 150), ("Opel Zafira", 90), ("Audi A3", 260), ("Audi A4", 380), ("Audi A6", 300),
    ("Audi Q5", 120), ("Audi Q7", 70), ("BMW 320", 210), ("BMW 520", 160), ("BMW 118", 80), ("BMW X3", 90),
    ("BMW X5", 110), ("Mercedes-Benz C 220", 200), ("Mercedes-Benz E 220", 150), ("Mercedes-Benz A 180", 90),
    ("Renault Clio", 260), ("Renault Megane", 280), ("Renault Captur", 90), ("Peugeot 206", 80),
    ("Peugeot 308", 160), ("Peugeot 3008", 80), ("Citroen C4", 110), ("Fiat Punto", 180), ("Fiat Doblo", 90),
    ("Ford Focus", 240), ("Ford Fiesta", 120), ("Toyota Corolla", 110), ("Toyota Yaris", 90),
    ("Toyota RAV4", 70), ("Hyundai i30", 120), ("Hyundai Tucson", 120), ("Kia Sportage", 130),
    ("Kia Ceed", 90), ("Dacia Sandero", 110), ("Dacia Duster", 140), ("Seat Leon", 160), ("Seat Ibiza", 120),
    ("Mazda 6", 70), ("Nissan Qashqai", 110), ("Suzuki Vitara", 60), ("Land Rover Range Rover", 40),
    ("Lada Niva", 30), ("Zastava Yugo", 10),
]


def _folded_label(label):
    return label.lower().translate(str.maketrans("šđčćž", "sdccz"))


def _like_matches(pattern, value):
    """SQL LIKE with ESCAPE '\\' (``%``, ``_``) against one folded label."""
    regex, i = "", 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            regex += re.escape(pattern[i + 1])
            i += 2
            continue
        regex += ".*" if ch == "%" else "." if ch == "_" else re.escape(ch)
        i += 1
    return re.fullmatch(regex, value, re.S) is not None


GOLF_YEARS = "golf_years="  # dataset "golf_years=2014,2015,2017": one Golf per model year, year bounds applied


def canned_rows(sql, params, dataset):
    """Listings for the find query only; every other statement is answered empty."""
    golf_years = None
    if dataset.startswith(GOLF_YEARS):
        golf_years = [int(y) for y in dataset[len(GOLF_YEARS):].split(",") if y]
        dataset = "rows"
    if golf_years is not None and "AS older" in sql and "AS newer" in sql:  # the nearest model years with cars
        target = params["target"]
        older = [y for y in golf_years if y < target]
        newer = [y for y in golf_years if y > target]
        return [{"older": max(older) if older else None, "newer": min(newer) if newer else None}]
    if "recent_listings" in sql:  # trends: an aggregate row, so a quiet database still answers
        return [{"sample_size": 120, "priced_sample": 100, "drops": 9, "increases": 4,
                 "newest": datetime.now(timezone.utc) - timedelta(hours=2)}]
    now = datetime.now(timezone.utc) - timedelta(hours=3)
    if dataset == "rows" and re.match(r"\s*SELECT 1\b", sql):  # "is this a known model?": against the catalogue
        pattern = params.get("pattern")
        if pattern is None:
            return [{"known": 1}]
        hit = any(_like_matches(pattern, _folded_label(label)) for label, _ in CATALOGUE)
        return [{"known": 1}] if hit else []
    if dataset == "rows" and re.search(r"AS folded,\s*min\(.*\) AS label, count\(\*\) AS n\b", sql):  # _model_labels
        return [{"folded": _folded_label(label), "label": label, "n": n} for label, n in CATALOGUE]
    if dataset == "rows" and "market_make) AS make" in sql and "AS n" in sql:  # telegram_follow.resolve_models
        tokens = [value for key, value in sorted(params.items()) if re.fullmatch(r"t[0-9]+", key)]
        hits = [(label, n) for label, n in CATALOGUE
                if tokens and all(_like_matches(t, _folded_label(label)) for t in tokens)]
        return [{"make": label.split(" ", 1)[0], "model": label.split(" ", 1)[1], "n": n}
                for label, n in sorted(hits, key=lambda hit: -hit[1])][: params.get("limit", 3)]
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
    if golf_years is not None:  # the year bounds of the query are applied, unlike the default canned rows
        low, high = params.get("year_min"), params.get("year_max")
        kept = [y for y in sorted(golf_years, reverse=True)
                if (low is None or y >= low) and (high is None or y <= high)]
        listings = [("Volkswagen", "Golf", y, 12000 + (y - 2010) * 500, 150000) for y in kept]
        pattern = params.get("pattern") if params.get("search") else None
        if pattern:
            listings = [row for row in listings if _like_matches(pattern, _folded_label(f"{row[0]} {row[1]}"))]
    else:
        listings = [("Volkswagen", "Golf 7", 2016, 17500, 160000),
                    ("Škoda", "Octavia", 2015, 14900, 190000),
                    ("Opel", "Astra", 2012, 8200, 210000)]
    pattern = params.get("pattern") if params.get("search") else None
    if pattern and golf_years is None:
        # a model search returns only listings whose label matches, as the real LIKE does: the canned ones,
        # else two made-up listings of a matching catalogue model, else nothing ("bembara" finds no car)
        matching = [row for row in listings if _like_matches(pattern, _folded_label(f"{row[0]} {row[1]}"))]
        if not matching:
            label = next((label for label, _ in CATALOGUE if _like_matches(pattern, _folded_label(label))), None)
            if label:
                make, _, model = label.partition(" ")
                matching = [(make, model, 2015, 15900, 185000), (make, model, 2013, 11900, 215000)]
        listings = matching
    rows = []
    total = len(listings)
    for i, (make, model, year, price, km) in enumerate(listings[: params.get("limit", 5)]):
        rows.append({"listing_id": f"10{i}", "make": make, "model": model, "year": year, "price": price,
                     "mileage": km, "last_verified_at": now, "fresh": True, "suspect": False,
                     "fresh_total": total, "all_total": total, "new_hidden": 0, "hint_hit": False,
                     "hint_total": 0, "total": total,
                     # extra columns a richer row format may read (unused by main today)
                     "fuel": "Dizel", "fuel_type": "Dizel", "transmission": ["Automatik", "Manuelni", None][i % 3],
                     "gearbox": ["Automatik", "Manuelni", None][i % 3], "city": ["Sarajevo", "Tuzla", "Mostar"][i % 3],
                     "location": ["Sarajevo", "Tuzla", "Mostar"][i % 3]})
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


def _order_terms(order):
    """Split an ORDER BY clause on its top-level commas."""
    terms, depth, current = [], 0, ""
    for ch in order:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            terms.append(current.strip())
            current = ""
        else:
            current += ch
    return terms + [current.strip()] if current.strip() else terms


def _term_is_live(term, params):
    """False for a CASE term whose bind-parameter guard is false for these params, so it orders nothing:
    ``CASE WHEN :search='' THEN price END`` is inert for a search with a model, and
    ``CASE WHEN CAST(:low_km AS boolean) ...`` is inert without the low-mileage wish."""
    if ":search=''" in term.replace(" ", "") and (params.get("search") or ""):
        return False
    for flag in re.findall(r"CAST\(:(\w+) AS boolean\)", term):
        if not params.get(flag):
            return False
    return True


def sort_kind(statements):
    """"price" when the listing query's ORDER BY ranks by price before recency, else "recency".

    Heuristic: looks at the ORDER BY of the first statement that selects listings with ``fresh_total``,
    skipping terms whose bind-parameter guard is off for that query (see ``_term_is_live``)."""
    finds = [(sql, params) for sql, params in statements if "fresh_total" in sql and "ORDER BY" in sql]
    if not finds:
        return None
    sql, params = finds[0]
    for term in _order_terms(order_by_of(sql)):
        if not _term_is_live(term, params):
            continue
        if "last_verified_at" in term:
            return "recency"
        if re.search(r"\bprice\b", term):
            return "price"
    return "recency"


# Every in-process memory telegram_bot keeps between messages. A case must start with all of them empty, or a
# budget / scope / pending question from the previous case leaks in (main gained _BUDGETS in #53; the v1
# harness cleared only the first four and so reported "šta da kupim" in a fresh chat as answered with the
# previous case's budget). Names missing on an older branch are skipped.
BOT_MEMORIES = ("_SCOPES", "_HISTORY", "_LANGS", "_ACTIVE", "_BUDGETS", "_PENDING", "_OPEN")


def reset_bot_memory():
    for name in BOT_MEMORIES:
        memory = getattr(telegram_bot, name, None)
        if memory is not None:
            memory.clear()


class Session:
    """One person's chat with the bot. ``chat`` is "private" or "group"."""

    def __init__(self, chat="private", dataset="rows", user_id=USER_ID):
        self.chat, self.dataset, self.user_id = chat, dataset, user_id
        self.config = make_config()
        self.engine = FakeEngine(dataset)
        self.update_id = 0
        self.last_bot_message = None

    def say(self, text, reply=False, untagged=False, user_id=None):
        """Send one text message; returns a dict describing what the bot did.

        In a group the message is tagged ``@testbot`` unless it already mentions someone, is a reply or a
        command, or ``untagged`` is set (a plain group message the bot should normally ignore). ``user_id``
        sends this one message as another allowlisted person (default: the session's user)."""
        if self.update_id == 0:  # a fresh person: forget every earlier conversation
            reset_bot_memory()
        self.update_id += 1
        sender = user_id or self.user_id
        chat = ({"id": sender, "type": "private"} if self.chat == "private"
                else {"id": GROUP_CHAT_ID, "type": "supergroup"})
        message = {"message_id": 10 + self.update_id, "text": text, "chat": chat,
                   "from": {"id": sender, "language_code": "bs"}}
        if (self.chat == "group" and not untagged and "@" not in text and not reply
                and not text.startswith("/")):
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

        real_understand, understood = telegram_bot._understand, []

        def understand_spy(text_value, *args, **kwargs):
            intent = real_understand(text_value, *args, **kwargs)
            understood.append((text_value, intent))
            return intent

        error = None
        with patch.dict(os.environ, {}, clear=False), patch("telegram_log.record"), \
                patch.object(telegram_bot, "answer_intent", spy), \
                patch.object(telegram_bot, "_understand", understand_spy):
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
        # Would production (Gemini configured) have asked Gemini instead of trusting this parser reading?
        # True means a parser miss here may be softened in production; False means the parser's answer is
        # exactly what the user gets (a message with a price / year / mileage never reaches Gemini).
        gemini = bool(understood) and telegram_bot._needs_interpreter(understood[-1][1], understood[-1][0])
        return {"handled": result.get("handled"), "reason": result.get("reason"), "intent": intent,
                "text": re.sub(r"<[^>]+>", "", text), "buttons": buttons, "sql": list(self.engine.sql),
                "sort": sort_kind(self.engine.sql), "error": error, "gemini": gemini}
