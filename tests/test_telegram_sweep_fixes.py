"""Fixes for the 15 Nov 2026 use-case sweep (docs/qa/bot-usecase-sweep-2026-11-15.md).

Every message below is a real message from that sweep; the case id from the report is in the test name
or a comment. The parser and Gemini tests need nothing; the database tests need a throwaway PostgreSQL.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
import re
import unittest
from unittest.mock import Mock, patch

import requests
from sqlalchemy import create_engine, text

import telegram_bot
import telegram_i18n
import telegram_llm
from telegram_bot import TelegramConfig, process_update
from telegram_intent import Filters, Intent, context_from_reply, parse_question
from telegram_llm import bounds_match, intent_from_plan

ENV = {
    "TELEGRAM_BOT_TOKEN": "test-only-token",
    "TELEGRAM_WEBHOOK_SECRET": "test_only_webhook_secret_123",
    "TELEGRAM_CHAT_ID": "-5332163835",
    "TELEGRAM_ALLOWED_USER_IDS": "123, 456",
}
CONFIG = TelegramConfig.from_env(ENV)
THIS_YEAR = datetime.now(timezone.utc).year


def setUpModule():
    patcher = patch.dict(os.environ, {}, clear=False)
    patcher.start()
    for name in ("GEMINI_API_KEY", "TELEGRAM_DEFAULT_LANGUAGE", "TELEGRAM_GROUP_WINDOW_SECONDS"):
        os.environ.pop(name, None)
    globals()["_env_patch"] = patcher


def tearDownModule():
    _env_patch.stop()


def clear_memory():
    for store in (telegram_bot._SCOPES, telegram_bot._HISTORY, telegram_bot._LANGS, telegram_bot._ACTIVE,
                  telegram_bot._PENDING, telegram_bot._OPEN):
        store.clear()


def conversation(*messages):
    """Parse messages the way a private chat does: each one sees the previous answer's scope."""
    intents, context = [], None
    for message in messages:
        intent = parse_question(message, context)
        intents.append(intent)
        if intent.kind in {"find", "suggest", "liquidity"}:
            context = intent
    return intents


# ---- 1. model search ------------------------------------------------------------------------------

class ModelSearchParsingTests(unittest.TestCase):
    def test_question_words_never_reach_the_search(self):
        for message, search in (
                ("Golf 7 2017 dizel, koja je normalna cijena?", "golf"),  # S1
                ("koliko košta prosječno Škoda Superb 2019?", "škoda superb"),  # E11
                ("BMW X5 2020 automatic, what's a fair price?", "bmw x5"),  # E7
                ("What's the cheapest Golf 7 diesel under 15k?", "golf")):  # E5
            self.assertEqual(search, parse_question(message).filters.search, message)

    def test_a_generation_number_becomes_model_years(self):
        intent = parse_question("What's the cheapest Golf 7 diesel under 15k?")  # E5
        self.assertEqual(("find", "golf", 2012, 2020, 15000, "diesel"),
                         (intent.kind, intent.filters.search, intent.filters.year_min, intent.filters.year_max,
                          intent.filters.budget, intent.filters.fuel))

    def test_a_stated_year_wins_over_the_generation(self):
        intent = parse_question("Golf 7 2017 dizel, koja je normalna cijena?")  # S1
        self.assertEqual((2016, 2018, 2017), (intent.filters.year_min, intent.filters.year_max, intent.year))

    def test_typos_in_model_fuel_and_thousands_are_read(self):
        intent = parse_question("golff 7 dizl do 18 iljada")  # E1
        self.assertEqual(Filters("golf", 18000, 2012, year_max=2020, fuel="diesel"), intent.filters)

    def test_c_klasa_and_serija_3_are_aliases(self):
        intent = parse_question("Mercedes C klasa 2015-2018 do 30 hiljada, automatik")  # E12
        self.assertEqual(Filters("mercedes c klasa", 30000, 2015, year_max=2018, transmission="automatic"),
                         intent.filters)
        self.assertEqual("%benz c _%", telegram_bot._like_pattern(intent.filters.search))
        self.assertEqual("bmw serija 3", parse_question("BMW serija 3 do 20000 KM").filters.search)
        self.assertEqual("%bmw 3%", telegram_bot._like_pattern("bmw serija 3"))
        self.assertEqual("golf", parse_question("vw golf do 10000 KM").filters.search)

    def test_compare_keeps_generation_numbers_for_the_cohorts(self):
        intent = parse_question("uporedi Golf 7 i Octavia 3")  # E10
        self.assertEqual(("compare", ("golf 7", "octavia 3")), (intent.kind, intent.labels))
        self.assertEqual(("golf", 2012, 2020), telegram_bot.model_hint("golf 7"))
        self.assertEqual(("octavia", 2012, 2020), telegram_bot.model_hint("octavia 3"))

    def test_two_models_with_ili_and_a_question_mark_are_compared(self):
        intent = parse_question("Octavia ili Passat za porodicu?")  # S2
        self.assertEqual(("compare", ("octavia", "passat")), (intent.kind, intent.labels))
        self.assertNotEqual("compare", parse_question("a automatik ili manuelni?").kind)  # not two models


LABELS = [("volkswagen golf", "Volkswagen Golf", 541), ("volkswagen passat", "Volkswagen Passat", 311),
          ("skoda superb", "Škoda Superb", 51), ("skoda octavia", "Škoda Octavia", 231),
          ("mercedes-benz c 220", "Mercedes-Benz C 220", 37), ("bmw x5", "BMW X5", 52)]


class ResolveSearchTests(unittest.TestCase):
    """resolve_search only ever returns names that exist in the data."""

    def test_typos_are_repaired(self):
        self.assertEqual(("golf", []), telegram_bot.resolve_search(LABELS, "golff"))
        self.assertEqual(("passat", []), telegram_bot.resolve_search(LABELS, "pasat"))
        self.assertEqual(("octavia", []), telegram_bot.resolve_search(LABELS, "oktavija"))

    def test_fewer_words_are_tried(self):
        self.assertEqual(("golf", []), telegram_bot.resolve_search(LABELS, "golf 15"))  # E9
        self.assertEqual(("skoda superb", []), telegram_bot.resolve_search(LABELS, "superb skoda"))
        self.assertEqual(("superb", []), telegram_bot.resolve_search(LABELS, "superb kombi"))

    def test_no_match_gives_close_candidates_not_everything(self):
        resolved, candidates = telegram_bot.resolve_search(LABELS, "pasatt")
        self.assertEqual("passat", resolved)
        self.assertEqual((None, []), telegram_bot.resolve_search(LABELS, "vrijeme"))
        resolved, candidates = telegram_bot.resolve_search(LABELS, "gulff")
        self.assertIsNone(resolved)
        self.assertEqual(["Volkswagen Golf"], candidates)


# ---- 2. "preko / iznad / over / above" ------------------------------------------------------------

class AboveIsALowerBoundTests(unittest.TestCase):
    def test_preko_40k_is_only_a_minimum_price(self):
        intent = parse_question("auta preko 40k")  # P4
        self.assertEqual(Filters(price_min=40000), intent.filters)
        self.assertIsNone(intent.filters.budget)

    def test_other_wordings(self):
        for message in ("iznad 40.000 KM", "više od 40 hiljada KM", "cars over 40k", "above 40000 KM"):
            filters = parse_question(message).filters
            self.assertEqual((40000, None), (filters.price_min, filters.budget), message)

    def test_preko_with_lower_case_km_is_not_a_price(self):
        self.assertIsNone(parse_question("golf preko 200.000 km").filters.price_min)

    def test_the_title_reads_back_as_a_minimum(self):
        title = telegram_bot._filter_title(Filters(price_min=40000), "bs")
        self.assertEqual("Viđeni oglasi · od 40.000 KM", title)
        self.assertEqual(Filters(price_min=40000), context_from_reply(title).filters)

    def test_gemini_must_not_turn_preko_into_a_cap(self):
        self.assertFalse(bounds_match(Filters(budget=40000), "auta preko 40k"))
        self.assertTrue(bounds_match(Filters(price_min=40000), "auta preko 40k"))
        self.assertIn("price_min_km 40000 with NO budget_km", telegram_llm.SYSTEM_PROMPT)


# ---- 3. fuel and gearbox --------------------------------------------------------------------------

class FuelAndGearboxTests(unittest.TestCase):
    def test_dizel_is_a_filter_not_a_model(self):
        self.assertEqual(Filters(budget=15000, fuel="diesel"), parse_question("dizel do 15.000 KM").filters)  # P3

    def test_follow_ups_narrow_the_previous_question(self):
        # F1: "Golf do 20k" -> "ne, nego dizel" -> "a automatik?" -> "isto ali Passat"
        intents = conversation("Golf do 20k", "ne, nego dizel", "a automatik?", "isto ali Passat")
        self.assertEqual([Filters("golf", 20000), Filters("golf", 20000, fuel="diesel"),
                          Filters("golf", 20000, fuel="diesel", transmission="automatic"),
                          Filters("passat", 20000, fuel="diesel", transmission="automatic")],
                         [intent.filters for intent in intents])
        self.assertTrue(all(intent.kind == "find" for intent in intents))

    def test_follow_ups_use_the_remembered_scope_in_a_private_chat(self):
        clear_memory()
        seen = []
        with patch.object(telegram_bot, "answer_intent",
                          side_effect=lambda engine, intent, config, lang="en": (seen.append(intent) or "ok", False)):
            for uid, message in enumerate(("Golf do 20k", "ne, nego dizel", "a automatik?"), 1):
                process_update({"update_id": uid, "message": {"chat": {"id": 123, "type": "private"},
                                                              "from": {"id": 123}, "text": message}},
                               None, CONFIG, call=Mock(return_value={"message_id": 1}), llm=None, phraser=None)
        self.assertEqual(Filters("golf", 20000, fuel="diesel", transmission="automatic"), seen[-1].filters)

    def test_titles_and_buttons_keep_fuel_and_gearbox(self):
        filters = Filters("golf", 20000, fuel="diesel", transmission="automatic")
        for lang in ("bs", "en"):
            title = telegram_bot._filter_title(filters, lang)
            self.assertEqual(filters, context_from_reply(title).filters, title)
        self.assertEqual("Viđeni oglasi · golf · dizel · automatik · do 20.000 KM", telegram_bot._filter_title(filters, "bs"))
        data = telegram_bot.refine_callback(filters)
        self.assertEqual("fda:20000:0:0:0:golf", data)
        self.assertEqual(filters, telegram_bot.parse_refine_callback(data))
        self.assertEqual(Filters("golf", 15000), telegram_bot.parse_refine_callback("f:15000:0:0:golf"))  # old buttons
        liquidity = Filters(budget=30000, fuel="petrol", transmission="manual")
        self.assertEqual(liquidity, telegram_bot.parse_liquidity_callback(telegram_bot.liquidity_callback(liquidity)))

    def test_gemini_reading_must_keep_the_fuel(self):
        self.assertFalse(bounds_match(Filters("golf", 15000), "dizel do 15.000 KM"))
        plan = {"intent": "find", "search": "golf 7", "budget_km": 15000, "fuel": "diesel"}
        self.assertEqual(Filters("golf", 15000, 2012, year_max=2020, fuel="diesel"), intent_from_plan(plan).filters)
        self.assertEqual("", intent_from_plan({"intent": "find", "search": "golf", "fuel": "coal"}).filters.fuel)


class FollowUpRangeTests(unittest.TestCase):
    def test_a_km_range_right_after_a_price_question_is_a_price_range(self):
        # M2: "auto do 20.000 KM" -> "50.000km do 60.000km" meant KM, not mileage
        first, second = conversation("auto do 20.000 KM", "50.000km do 60.000km")
        self.assertEqual(Filters(budget=60000, price_min=50000), second.filters)
        self.assertTrue(bounds_match(Filters(budget=60000, price_min=50000), "50.000km do 60.000km", first))
        # without a price question before it, lower-case km is still mileage
        self.assertEqual(60000, parse_question("Octavia 50.000km do 60.000km").filters.mileage_max)
        self.assertEqual(150000, conversation("Golf do 20k", "do 150.000 km")[-1].filters.mileage_max)

    def test_deal_words_are_not_typo_repaired_into_a_model(self):
        self.assertEqual("", parse_question("najjeftinije dobre ponude ove sedmice").filters.search)  # I3
        resolved, _ = telegram_bot.resolve_search(LABELS + [("fiat sedici", "Fiat Sedici", 3)], "sedmice")
        self.assertIsNone(resolved)  # at most a "did you mean" suggestion, never searched silently


# ---- 4. price check -------------------------------------------------------------------------------

class PriceCheckParsingTests(unittest.TestCase):
    def test_the_stated_price_is_judged_not_used_as_a_cap(self):
        for message in ("is this a good price: Audi A4 2017 22.000 KM",  # I4
                        "da li je 22.000 KM dobra cijena za Audi A4 2017?",  # I5
                        "je li ovo dobra cijena: Audi A4 2017 22.000 KM"):
            intent = parse_question(message)
            self.assertEqual(("price_check", "audi a4", 22000, 2017), (intent.kind, intent.filters.search,
                                                                       intent.price, intent.year), message)
            self.assertEqual(Filters("audi a4", year_min=2016, year_max=2018), intent.filters, message)

    def test_fair_price_questions_without_a_price(self):
        intent = parse_question("BMW X5 2020 automatic, what's a fair price?")  # E7
        self.assertEqual(("price_check", None, Filters("bmw x5", year_min=2019, year_max=2021, transmission="automatic")),
                         (intent.kind, intent.price, intent.filters))
        self.assertEqual("price_check", parse_question("koliko košta prosječno Škoda Superb 2019?").kind)  # E11

    def test_a_price_check_without_a_model_asks_for_one(self):
        intent = parse_question("je li 15.000 KM dobra cijena?")
        self.assertEqual(("clarify", "price_check"), (intent.kind, intent.note))
        text_value, buttons = telegram_bot.answer_intent(None, intent, CONFIG, lang="bs")
        self.assertIn("Za koji model i godište", text_value)
        self.assertIs(False, buttons)

    def test_gemini_price_check_plan(self):
        intent = intent_from_plan({"intent": "price_check", "search": "audi a4", "year_min": 2017,
                                   "asking_price_km": 22000})
        self.assertEqual(("price_check", 22000, 2017, Filters("audi a4", year_min=2016, year_max=2018)),
                         (intent.kind, intent.price, intent.year, intent.filters))


# ---- 6. Gemini resilience -------------------------------------------------------------------------

def gemini_response(status, payload=None):
    response = Mock(status_code=status)
    response.json.return_value = payload or {}
    return response


class GeminiResilienceTests(unittest.TestCase):
    CONFIG = telegram_llm.LLMConfig(api_key="test-only-key")

    def setUp(self):
        telegram_llm._BACKOFF["until"] = 0.0
        self.addCleanup(telegram_llm._BACKOFF.update, {"until": 0.0})

    def test_a_429_stops_the_phrasing_call_and_the_next_messages(self):
        post = Mock(return_value=gemini_response(429))
        token = telegram_llm.start_message()
        try:
            self.assertIsNone(telegram_llm.interpret("golf", None, self.CONFIG, post=post))
            self.assertIsNone(telegram_llm.phrase("golf", "facts", self.CONFIG, post=post))
        finally:
            telegram_llm.end_message(token)
        self.assertEqual(1, post.call_count)  # never called twice after a 429
        self.assertIn("rate limit", telegram_llm.PHRASE_STATUS.get())
        token = telegram_llm.start_message()  # the next message, within the back-off
        try:
            self.assertIsNone(telegram_llm.interpret("passat", None, self.CONFIG, post=post))
        finally:
            telegram_llm.end_message(token)
        self.assertEqual(1, post.call_count)

    def test_a_timeout_skips_the_phrasing_call_for_that_message(self):
        post = Mock(side_effect=requests.Timeout())
        token = telegram_llm.start_message()
        try:
            telegram_llm.interpret("golf", None, self.CONFIG, post=post)
            telegram_llm.phrase("golf", "facts", self.CONFIG, post=post)
        finally:
            telegram_llm.end_message(token)
        self.assertEqual(1, post.call_count)

    def test_each_call_gets_only_the_time_left_of_the_budget(self):
        post = Mock(return_value=gemini_response(200, {"candidates": [{"finishReason": "STOP", "content": {
            "parts": [{"text": '{"intent": "find", "search": "golf"}'}]}}]}))
        token = telegram_llm.start_message(started=telegram_llm.time.monotonic() - 4.0)  # 2 s of 6 s left
        try:
            telegram_llm.interpret("golf", None, self.CONFIG, post=post)
        finally:
            telegram_llm.end_message(token)
        connect, read = post.call_args.kwargs["timeout"]
        self.assertLessEqual(read, 2.01)
        token = telegram_llm.start_message(started=telegram_llm.time.monotonic() - 5.5)  # under 1 s left
        try:
            self.assertIsNone(telegram_llm.phrase("golf", "facts", self.CONFIG, post=post))
        finally:
            telegram_llm.end_message(token)
        self.assertEqual(1, post.call_count)
        self.assertIn("no time left", telegram_llm.PHRASE_STATUS.get())

    def test_process_update_falls_back_to_the_database_answer_after_one_429(self):
        clear_memory()
        posted = Mock(return_value=gemini_response(429))
        sent = []
        call = Mock(side_effect=lambda config, method, payload: sent.append((method, payload)) or {"message_id": 5})
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-only-key"}), \
                patch.object(telegram_llm.requests, "post", posted), \
                patch.object(telegram_bot, "answer_intent", return_value=("Viđeni oglasi · golf\nDB answer", None)):
            started = telegram_llm.time.monotonic()
            process_update({"update_id": 9, "message": {"chat": {"id": 123, "type": "private"}, "from": {"id": 123},
                                                        "text": "golf"}}, None, CONFIG, call=call)
        self.assertLess(telegram_llm.time.monotonic() - started, 6)
        self.assertEqual(1, posted.call_count)  # interpret only; phrasing is not attempted after the 429
        self.assertIn("DB answer", sent[-1][1]["text"])

    def test_phrasing_that_blames_olx_is_rejected(self):
        for reply in ("OLX didn't recognise that specific model search, so it pulled up other listings.",
                      "OLX nije prepoznao taj model."):
            self.assertEqual(("", "blamed OLX for the bot's own reading"), telegram_llm._check_text(reply, set()))
        self.assertIn("never say OLX", telegram_llm.PHRASE_PROMPT)

    def test_the_english_fallback_text_does_not_blame_olx(self):
        for key in ("search_unknown", "search_unknown_hint", "search_assumed", "did_you_mean"):
            for lang in ("en", "bs"):
                self.assertNotRegex(telegram_i18n.STRINGS[lang][key], r"(?i)\bolx\b", key)
        self.assertTrue(telegram_i18n.t("en", "search_unknown", term="x").startswith("I didn't recognise"))

    def test_a_phrased_reply_must_name_the_model_that_was_actually_searched(self):
        clear_memory()
        answer = telegram_bot.Html("<b>Viđeni oglasi · golf</b>\nNisam prepoznao „golff“, pa tražim „golf“.")
        answer.must_mention = "golf"
        call = Mock(return_value={"message_id": 5})
        phraser = Mock(return_value=("Evo nekoliko jeftinih auta do 15.000 KM.", False))
        with patch.object(telegram_bot, "answer_intent", return_value=(answer, None)):
            process_update({"update_id": 10, "message": {"chat": {"id": 123, "type": "private"}, "from": {"id": 123},
                                                         "text": "golff do 15000 KM"}}, None, CONFIG, call=call,
                           llm=None, phraser=phraser)
        phraser.assert_called_once()
        self.assertIn("Nisam prepoznao „golff“", call.call_args_list[-1].args[2]["text"])

    def test_a_did_you_mean_answer_is_never_rephrased(self):
        clear_memory()
        phraser = Mock(return_value=("Evo svih auta.", True))
        call = Mock(return_value={"message_id": 5})
        with patch.object(telegram_bot, "answer_intent",
                          return_value=(telegram_bot.NoMatch("Jesi li mislio na Volkswagen Golf?"), False)):
            process_update({"update_id": 11, "message": {"chat": {"id": 123, "type": "private"}, "from": {"id": 123},
                                                         "text": "gol do 15000 KM"}}, None, CONFIG, call=call,
                           llm=None, phraser=phraser)
        phraser.assert_not_called()


# ---- 5 and the rest against a database ------------------------------------------------------------

def _naive_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@unittest.skipUnless(os.getenv("DEALER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class SweepDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["DEALER_TEST_DATABASE_URL"])

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.cleanup()
        self.addCleanup(self.cleanup)
        self.now = _naive_now()

    def cleanup(self):
        with self.engine.begin() as c:
            c.execute(text("DELETE FROM car_listings WHERE listing_id LIKE '__qa_s_%'"))

    def seed(self, rows, *, first_days=20, last_days=1):
        """rows: (id, make, model, year, price, mileage, fuel, gearbox[, first_days, last_days])"""
        with self.engine.begin() as c:
            for row in rows:
                lid, make, model, year, price, mileage, fuel, gearbox, *days = row
                first, last = days or (first_days, last_days)
                c.execute(text("""INSERT INTO car_listings(listing_id,make,model,year,price,price_status,mileage,
                    fuel_type,transmission,source,collection_method,last_verified_at,first_seen_at,is_active,
                    consecutive_missing_runs) VALUES (:id,:make,:model,:year,:price,'priced',:mileage,:fuel,:gearbox,
                    'olx.ba','olx_api',:last,:first,true,0)"""),
                    {"id": f"__qa_s_{lid}", "make": make, "model": model, "year": year, "price": price,
                     "mileage": mileage, "fuel": fuel, "gearbox": gearbox,
                     "first": self.now - timedelta(days=first), "last": self.now - timedelta(days=last)})

    def find(self, filters, lang="bs"):
        text_value, buttons = telegram_bot.answer_intent(self.engine, Intent("find", filters), CONFIG, lang=lang)
        return telegram_bot.html_to_text(text_value, keep_links=True), buttons

    def ids(self, reply):
        return set(re.findall(r"artikal/__qa_s_(\w+)", reply))

    # 3. fuel / gearbox
    def test_fuel_and_gearbox_filter_in_sql_and_the_gearbox_caveat_is_shown(self):
        self.seed([("gd", "Volkswagen", "Golf", 2016, 14000, 150000, "diesel", None),
                   ("gda", "Volkswagen", "Golf", 2017, 16000, 140000, "diesel", "automatic"),
                   ("gp", "Volkswagen", "Golf", 2016, 13000, 120000, "petrol", "manual")])
        reply, _ = self.find(parse_question("dizel do 20.000 KM").filters)  # P3
        self.assertEqual({"gd", "gda"}, self.ids(reply))
        self.assertNotIn("petine", reply)
        filters = conversation("Golf do 20k", "ne, nego dizel", "a automatik?")[-1].filters  # F1c
        reply, _ = self.find(filters)
        self.assertEqual({"gda"}, self.ids(reply))
        self.assertIn("mjenjač je upisan na oko 1 od 5 oglasa", reply)

    # 1. model search end to end
    def test_unknown_model_asks_did_you_mean_and_lists_nothing(self):
        self.seed([("g1", "Volkswagen", "Golf", 2016, 14000, 150000, "diesel", None),
                   ("p1", "Volkswagen", "Passat", 2016, 15000, 150000, "diesel", None)])
        reply, buttons = self.find(Filters("gulff", 20000))
        self.assertIn("Nisam prepoznao „gulff“ kao marku ili model", reply)
        self.assertIn("Jesi li mislio na Volkswagen Golf?", reply)
        self.assertEqual(set(), self.ids(reply))
        self.assertEqual("🔎 Volkswagen Golf", buttons.rows[0][0][0])
        self.assertEqual(Filters("volkswagen golf", 20000), telegram_bot.parse_refine_callback(buttons.rows[0][0][1]))

    def test_typo_and_extra_words_are_repaired_and_the_answer_says_so(self):
        self.seed([("g1", "Volkswagen", "Golf", 2016, 14000, 150000, "diesel", None),
                   ("s1", "Škoda", "Superb", 2019, 30000, 150000, "diesel", None)])
        reply, page = self.find(Filters("golff", 20000))
        self.assertIn("Nisam prepoznao „golff“, pa tražim „golf“.", reply)
        self.assertEqual({"g1"}, self.ids(reply))
        self.assertEqual("golf", page.filters.search)
        reply, _ = self.find(Filters("superb skoda", 40000))
        self.assertEqual({"s1"}, self.ids(reply))

    def test_c_klasa_finds_c_220(self):
        self.seed([("c1", "Mercedes-Benz", "C 220", 2016, 28000, 150000, "diesel", "automatic"),
                   ("cla", "Mercedes-Benz", "CLA", 2016, 28000, 150000, "diesel", "automatic"),
                   ("e1", "Mercedes-Benz", "E 220", 2016, 28000, 150000, "diesel", "automatic")])
        reply, _ = self.find(parse_question("Mercedes C klasa 2015-2018 do 30 hiljada, automatik").filters)  # E12
        self.assertEqual({"c1"}, self.ids(reply))

    def test_compare_by_generation_uses_the_generation_years(self):
        self.seed([("g6", "Volkswagen", "Golf", 2010, 9000, 200000, "diesel", None),
                   ("g7a", "Volkswagen", "Golf", 2015, 17000, 150000, "diesel", None),
                   ("g7b", "Volkswagen", "Golf", 2017, 19000, 120000, "diesel", None),
                   ("o3", "Škoda", "Octavia", 2016, 18000, 140000, "diesel", None)])
        reply = telegram_bot.compare_message(self.engine, "golf 7 | octavia 3", lang="bs")  # E10
        self.assertIn("golf 7 (2012–2020)", reply)
        self.assertIn("Uzorak: 2 oglasa", reply)  # the 2010 Golf 6 is not a Golf 7
        self.assertIn("Medijan tražene cijene: 18.000 KM", reply)
        self.assertIn("octavia 3 (2012–2020)", reply)

    # 4. price check
    def test_price_check_gives_median_middle_half_sample_and_position(self):
        rows = [(f"a{n}", "Audi", "A4", 2016 + n % 3, price, 150000 + 1000 * n, "diesel", None)
                for n, price in enumerate((24000, 25000, 26000, 27000, 28000, 29000, 30000, 31000))]
        rows.append(("a_far", "Audi", "A4", 2012, 12000, 250000, "diesel", None))  # other years: not compared
        rows.append(("a_new", "Audi", "A4", THIS_YEAR, 70000, 10, "diesel", None))  # new: not compared
        self.seed(rows)
        intent = parse_question("da li je 22.000 KM dobra cijena za Audi A4 2017?")  # I5
        reply, buttons = telegram_bot.answer_intent(self.engine, intent, CONFIG, lang="bs")
        self.assertTrue(reply.startswith("Provjera cijene · audi a4 · 2016–2018"), reply)
        self.assertIn("Uporedivih oglasa (zadnjih 90 dana): 8", reply)
        self.assertIn("Medijan tražene cijene: 27.500 KM", reply)
        self.assertIn("Srednja polovina cijena: 25.750–29.250 KM", reply)
        self.assertIn("22.000 KM je ispod srednje polovine: jeftinije od većine uporedivih oglasa (20% ispod medijana)",
                      reply)
        self.assertNotIn("Mali uzorak", reply)
        english, _ = telegram_bot.answer_intent(
            self.engine, parse_question("is this a good price: Audi A4 2017 22.000 KM"), CONFIG, lang="en")  # I4
        self.assertIn("Comparable listings (last 90 days): 8", english)
        self.assertIn("22.000 KM is below the middle half", english)
        self.assertEqual("🔎", buttons.rows[0][0][0][:1])

    def test_price_check_with_too_few_listings_is_honest(self):
        self.seed([("x1", "BMW", "X5", 2020, 80000, 90000, "diesel", "automatic"),
                   ("x2", "BMW", "X5", 2020, 85000, 80000, "diesel", None),
                   ("x3", "BMW", "X5", 2021, 90000, 60000, "diesel", None)])
        intent = parse_question("BMW X5 2020 automatic, what's a fair price?")  # E7
        reply, _ = telegram_bot.answer_intent(self.engine, intent, CONFIG, lang="en")
        self.assertIn("Comparable listings (last 90 days): 3", reply)
        self.assertIn("Too few with that gearbox recorded, so all gearboxes are included.", reply)
        self.assertIn("Thin sample: only 3 comparable listings", reply)
        none, _ = telegram_bot.answer_intent(self.engine, parse_question("is this a good price: Lada Niva 2017 9000 KM"),
                                             CONFIG, lang="en")
        self.assertIn("No comparable listings in the last 90 days, so I can't judge this price.", none)

    # 5. data hygiene
    def test_new_cars_are_left_out_of_used_lists_medians_and_deals(self):
        rows = [(f"k{n}", "Kia", "Sportage", 2019, 30000 + 500 * n, 90000 + 1000 * n, "diesel", None) for n in range(5)]
        rows += [(f"kn{n}", "Kia", "Sportage", THIS_YEAR, 52000, 3, "diesel", None) for n in range(3)]
        rows += [(f"cn{n}", "Toyota", "Corolla", THIS_YEAR, 33000, 0, "hybrid", None) for n in range(4)]
        rows += [(f"cu{n}", "Toyota", "Corolla", THIS_YEAR, 49000, 0, "hybrid", None) for n in range(4)]
        self.seed(rows)
        reply, _ = self.find(Filters(price_min=20000, budget=60000))  # P1
        self.assertFalse(any(i.startswith(("kn", "cn", "cu")) for i in self.ids(reply)), reply)
        self.assertIn("11 novih auta izostavljeno", reply)
        suggest = telegram_bot.suggest_message(self.engine, Filters(budget=60000, price_min=20000), lang="bs")
        self.assertNotIn("Corolla", suggest)  # C1a: no "33% below median" deal on a 0 km car
        self.assertIn("Kia Sportage · 5 oglasa", suggest)
        self.assertNotIn("5.000 km", suggest)
        asked, _ = self.find(parse_question("novi auto od 20 do 60k").filters)
        self.assertTrue(self.ids(asked) and all(i.startswith(("kn", "cn", "cu")) for i in self.ids(asked)), asked)
        stats = telegram_bot.model_stats_message(self.engine, "kia sportage", lang="bs")
        self.assertIn("Uzorak: 5 oglasa", stats)

    def test_last_years_cars_at_one_km_are_new_too(self):
        self.seed([("j1", "Jeep", "Compass", THIS_YEAR - 1, 44900, 1, "diesel", None),
                   ("j2", "Jeep", "Compass", THIS_YEAR - 1, 39900, 25000, "diesel", None)])
        reply, _ = self.find(Filters("jeep", 50000))
        self.assertEqual({"j2"}, self.ids(reply))

    def test_a_gearbox_filter_that_finds_nothing_offers_any_gearbox(self):
        self.seed([("c1", "Mercedes-Benz", "C 220", 2016, 28000, 150000, "diesel", None),
                   ("c2", "Mercedes-Benz", "C 200", 2017, 29000, 140000, "petrol", "manual"),
                   ("c3", "Mercedes-Benz", "C 180", 2016, 25000, 160000, "petrol", "automatic", 60, 30)])
        filters = parse_question("Mercedes C klasa 2015-2018 do 30 hiljada, automatik").filters  # E12
        reply, choices = self.find(filters)
        self.assertEqual(replace(filters, include_old=True), telegram_bot.parse_refine_callback(choices.rows[1][0][1]))
        self.assertIn("Bez filtera mjenjača ima 2 odgovarajućih oglasa", reply)
        self.assertEqual("🔎 Bilo koji mjenjač (2)", choices.rows[0][0][0])
        self.assertEqual(replace(filters, transmission=""), telegram_bot.parse_refine_callback(choices.rows[0][0][1]))

    def test_the_any_gearbox_hint_counts_older_ads_when_none_are_fresh(self):
        self.seed([("c1", "Mercedes-Benz", "C 220", 2016, 28000, 150000, "diesel", None, 60, 30)])
        filters = parse_question("Mercedes C klasa 2015-2018 do 30 hiljada, automatik").filters  # E12 as on 15 Nov
        reply, choices = self.find(filters)
        self.assertIn("Bez filtera mjenjača ima 1 odgovarajućih oglasa iz zadnjih 90 dana", reply)
        self.assertEqual(replace(filters, transmission="", include_old=True),
                         telegram_bot.parse_refine_callback(choices.rows[0][0][1]))

    def test_mileage_over_999999_is_unknown_in_rows_and_buttons(self):
        self.seed([("v1", "Suzuki", "Vitara", 2016, 20000, 3024000, "diesel", None),
                   ("v2", "Suzuki", "Vitara", 2017, 21000, 120000, "diesel", None)])
        reply, page = self.find(parse_question("treba mi porodični auto do 25k").filters)  # I2
        self.assertNotIn("3.024.000", reply)
        self.assertEqual(120000, page.max_km)
        labels = [b["text"] for row in telegram_bot.keyboard(CONFIG, page, "bs")["inline_keyboard"] for b in row]
        self.assertFalse(any("2.110.000" in label or "3.024.000" in label for label in labels), labels)

    def test_no_narrowing_buttons_on_an_empty_result(self):
        self.seed([("t0", "Tesla", "Model 3", 2021, 60000, 50000, "electric", "automatic")])
        _, page = self.find(parse_question("auta preko 40k").filters)  # P4: budgets above the minimum only
        budgets = [telegram_bot.parse_refine_callback(b["callback_data"])
                   for row in telegram_bot.keyboard(CONFIG, page, "bs")["inline_keyboard"] for b in row
                   if b.get("callback_data", "").startswith("f:") and "KM" in b["text"] and "%" not in b["text"]]
        self.assertEqual([(40000, 50000), (40000, 60000), (40000, 80000)],
                         [(f.price_min, f.budget) for f in budgets])
        self.cleanup()
        self.seed([("t1", "Tesla", "Model 3", 2021, 60000, 50000, "electric", "automatic")])
        _, page = self.find(parse_question("Tesla 2024 do 10k").filters)  # E3
        labels = [b["text"] for row in telegram_bot.keyboard(CONFIG, page, "bs")["inline_keyboard"] for b in row]
        self.assertFalse(any("Od 20" in label for label in labels), labels)

    def test_liquidity_is_suppressed_when_it_measures_collector_churn(self):
        rows = [(f"o{n}", "Škoda", "Octavia", 2016, 15000, 150000, "diesel", None, 30, 29.95) for n in range(20)]
        rows += [(f"m{n}", "Renault", "Megane", 2016, 9000, 180000, "diesel", None, 40, 39.9) for n in range(6)]
        self.seed(rows)
        reply = telegram_bot.html_to_text(telegram_bot.liquidity_message(self.engine, Filters(), lang="bs"))  # I1
        self.assertIn("ne mogu poredati šta se najbrže prodaje", reply)
        self.assertIn("100% oglasa koje smo vidjeli (26 od 26)", reply)
        self.assertNotIn("medijan 0 dana", reply)
        _, choices = telegram_bot.answer_intent(self.engine, parse_question("šta se najlakše prodaje?"), CONFIG, lang="bs")
        self.assertIs(False, choices)


if __name__ == "__main__":
    unittest.main()
