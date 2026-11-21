"""Price and year ranges, and corrections to the previous question.

The real case from Ogi's group:
  1. "Pronadji mi koje auto se najbolje prodaje od 50 - 60k, godiste od 2022 - 2024"
     -> the bot answered "do 60.000 KM · od 2022" (models around 33k KM, 2026 cars at 0 km).
  2. "Mislim da sam stavio gore od 50.000km do 60.000km a ti mi pokazujes skodu od 33k"
     -> the bot said it could not change searches and showed an unrelated shortlist "do 30.000 KM".
"""

from datetime import datetime, timedelta, timezone
import os
import re
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, text

import telegram_bot
import telegram_i18n
import telegram_intent
import telegram_llm
from telegram_bot import TelegramConfig, process_update
from telegram_intent import Filters, Intent, context_from_reply, parse_question
from telegram_llm import bounds_match, intent_from_plan, reply_keeps_range

QUESTION = "Pronadji mi koje auto se najbolje prodaje od 50 - 60k, godiste od 2022 - 2024"
# Ogi originally wrote lower-case "km" here, meaning KM. Under the case rule (lower-case km = mileage,
# upper-case KM = currency) the price reading needs "KM"; the lower-case original is kept for the
# no-context cases, where a mileage range gives the bot nothing to re-run.
CORRECTION_LOWER = "Mislim da sam stavio gore od 50.000km do 60.000km a ti mi pokazujes skodu od 33k"
CORRECTION = CORRECTION_LOWER.replace("km", " KM")
EXPECTED = {"price_min": 50000, "price_max": 60000, "year_min": 2022, "year_max": 2024}
HEADER = "Uža lista na osnovu podataka · 50.000–60.000 KM · 2022–2024"

ENV = {
    "TELEGRAM_BOT_TOKEN": "test-only-token",
    "TELEGRAM_WEBHOOK_SECRET": "test_only_webhook_secret_123",
    "TELEGRAM_CHAT_ID": "-5332163835",
    "TELEGRAM_ALLOWED_USER_IDS": "123, 456",
}
CONFIG = TelegramConfig.from_env(ENV)
GROUP = -5332163835
CANNOT_CHANGE = re.compile(r"ne mogu mijenja|can'?t change", re.I)


def bounds(filters):
    return {"price_min": filters.price_min, "price_max": filters.price_max,
            "year_min": filters.year_min, "year_max": filters.year_max}


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


class OgisConversationParserTests(unittest.TestCase):
    def test_the_question_keeps_both_ends_of_the_price_and_year_ranges(self):
        intent = parse_question(QUESTION)
        self.assertEqual("suggest", intent.kind)  # "najbolje se prodaje": most listed, as before
        self.assertEqual(EXPECTED, bounds(intent.filters))
        self.assertEqual("", intent.filters.search)
        self.assertIsNone(intent.filters.mileage_max)

    def test_the_correction_keeps_the_ranges_and_the_intent(self):
        first = parse_question(QUESTION)
        corrected = parse_question(CORRECTION, first)
        self.assertEqual("suggest", corrected.kind)
        self.assertEqual(EXPECTED, bounds(corrected.filters))
        self.assertIsNone(corrected.filters.mileage_max)  # "KM" after a price question is currency
        self.assertEqual("correction", corrected.note)

    def test_the_correction_fixes_what_the_old_answer_got_wrong(self):
        # what the bot actually understood the first time: "do 60.000 KM · od 2022"
        wrong = Intent("suggest", Filters(budget=60000, year_min=2022))
        corrected = parse_question(CORRECTION, wrong)
        self.assertEqual(("suggest", {**EXPECTED, "year_max": None}), (corrected.kind, bounds(corrected.filters)))
        # "33k" describes what the bot showed, never a new filter
        self.assertNotIn(33000, (corrected.filters.price_min, corrected.filters.budget))

    def test_the_correction_is_read_back_from_the_answer_it_replies_to(self):
        context = context_from_reply("Evo šta baza pokazuje.\n\n" + HEADER + "\n\nNajviše oglasa:\n• ...")
        self.assertEqual(("suggest", EXPECTED), (context.kind, bounds(context.filters)))
        corrected = parse_question(CORRECTION, context)
        self.assertEqual(("suggest", EXPECTED), (corrected.kind, bounds(corrected.filters)))


class RangeParsingTests(unittest.TestCase):
    def test_price_ranges(self):
        for message in ("golf od 50 - 60k", "golf 50-60k", "golf između 50 i 60 hiljada", "golf izmedju 50 i 60 hiljada",
                        "golf 50.000 do 60.000 KM", "golf od 50.000 do 60.000 KM", "golf 50k-60k", "golf 60-50k"):
            filters = parse_question(message).filters
            self.assertEqual(("golf", 50000, 60000), (filters.search, filters.price_min, filters.price_max), message)

    def test_year_ranges(self):
        for message in ("octavia godište od 2022 - 2024", "octavia 2022-2024", "octavia 2022 do 2024",
                        "octavia između 2022 i 2024", "octavia od 2022 do 2024"):
            filters = parse_question(message).filters
            self.assertEqual(("octavia", 2022, 2024, None), (filters.search, filters.year_min, filters.year_max,
                                                           filters.budget), message)

    def test_both_ranges_together(self):
        filters = parse_question("passat 10-15k 2015-2018").filters
        self.assertEqual(Filters("passat", 15000, 2015, price_min=10000, year_max=2018), filters)

    def test_single_bounds_and_mixed_units_are_read_as_before(self):
        self.assertEqual(Filters("golf", 15000), parse_question("golf do 15000 KM").filters)
        self.assertEqual(Filters("bmw 320d", 25000, 2015, year_max=2015), parse_question("bmw 320d 2015 25000").filters)
        self.assertEqual(Filters("passat", 12000, mileage_max=200000),
                         parse_question("passat do 12000 KM do 200000 km").filters)

    def test_lowercase_km_range_without_price_context_stays_mileage(self):
        filters = parse_question("golf od 50.000km do 100.000km").filters
        self.assertEqual((None, None, 100000), (filters.price_min, filters.budget, filters.mileage_max))

    def test_a_correction_that_talks_about_mileage_keeps_km_as_mileage(self):
        first = Intent("suggest", Filters(budget=60000, price_min=50000))
        corrected = parse_question("Mislim da sam stavio kilometraža do 100.000km", first)
        self.assertEqual((50000, 60000, 100000), (corrected.filters.price_min, corrected.filters.budget,
                                                  corrected.filters.mileage_max))

    def test_correction_cues_and_merging(self):
        first = Intent("find", Filters("golf", 60000, 2022, price_min=50000, year_max=2024))
        for message, expected in (
                ("ne, nego od 2020", Filters("golf", 60000, 2020, price_min=50000, year_max=2024)),
                ("rekao sam do 40k", Filters("golf", 40000, 2022, year_max=2024)),  # 50k no longer fits
                ("rekao sam passat", Filters("passat", 60000, 2022, price_min=50000, year_max=2024)),
                ("I said 30-40k", Filters("golf", 40000, 2022, price_min=30000, year_max=2024))):
            intent = parse_question(message, first)
            self.assertEqual(("find", expected), (intent.kind, intent.filters), message)

    def test_a_correction_with_nothing_to_correct_asks_instead_of_guessing(self):
        intent = parse_question(CORRECTION_LOWER)  # no previous question known
        self.assertEqual(("clarify", "correction"), (intent.kind, intent.note))
        text_value, choices = telegram_bot.answer_intent(Mock(), intent, CONFIG, lang="bs")
        self.assertNotRegex(text_value, CANNOT_CHANGE)
        self.assertIn("50.000 do 60.000 KM", text_value)
        self.assertFalse(choices)
        restated = parse_question("rekao sam golf 50-60k a ti mi pokazuješ passat")
        self.assertEqual(("find", Filters("golf", 60000, price_min=50000)), (restated.kind, restated.filters))


class HeaderAndButtonTests(unittest.TestCase):
    FILTERS = Filters(budget=60000, year_min=2022, price_min=50000, year_max=2024)

    def test_titles_state_both_ends(self):
        self.assertEqual(HEADER, telegram_bot._suggest_title(self.FILTERS, "bs"))
        self.assertEqual("Data-based shortlist · 50.000–60.000 KM · 2022–2024",
                         telegram_bot._suggest_title(self.FILTERS, "en"))
        self.assertEqual("Viđeni oglasi · golf · 50.000–60.000 KM · 2022–2024",
                         telegram_bot._filter_title(Filters("golf", 60000, 2022, price_min=50000, year_max=2024), "bs"))
        self.assertEqual("⏱ Najbrže nestaju iz naše pretrage · 50.000–60.000 KM · 2022–2024",
                         telegram_bot._liquidity_title(self.FILTERS, "bs"))
        self.assertEqual("Viđeni oglasi · do 15.000 KM · od 2018",
                         telegram_bot._filter_title(Filters(budget=15000, year_min=2018), "bs"))

    def test_titles_read_back_into_the_same_filters(self):
        for lang in ("bs", "en"):
            for filters in (self.FILTERS, Filters(budget=60000, year_min=2022), Filters(price_min=50000, year_max=2024),
                            Filters("golf", 60000, 2022, 150000, price_min=50000, year_max=2024)):
                for kind, title in (("find", telegram_bot._filter_title(filters, lang)),
                                    ("liquidity", telegram_bot._liquidity_title(filters, lang))):
                    self.assertEqual((kind, filters), (context_from_reply(title).kind, context_from_reply(title).filters))

    def test_reply_titles_match_the_i18n_strings(self):
        for titles, lang in zip(telegram_intent._TITLES, ("en", "bs")):
            self.assertEqual(titles["suggest"], telegram_i18n.t(lang, "suggest_title"))
            self.assertEqual(titles["liquidity"], telegram_i18n.t(lang, "liquidity_title"))
            self.assertEqual(titles["find"], telegram_i18n.t(lang, "find_title"))

    def test_buttons_carry_ranges_and_old_buttons_still_work(self):
        data = telegram_bot.refine_callback(Filters("golf", 60000, 2022, price_min=50000, year_max=2024))
        self.assertEqual("f:50000-60000:2022-2024:0:0:golf", data)
        self.assertEqual(Filters("golf", 60000, 2022, price_min=50000, year_max=2024),
                         telegram_bot.parse_refine_callback(data))
        self.assertEqual(Filters("golf", 15000, 2018), telegram_bot.parse_refine_callback("f:15000:2018:0:0:golf"))
        self.assertEqual(Filters("golf", 15000), telegram_bot.parse_refine_callback("f:15000:0:0:golf"))
        self.assertEqual(Filters(year_max=2024), telegram_bot.parse_refine_callback(
            telegram_bot.refine_callback(Filters(year_max=2024))))
        liquid = telegram_bot.liquidity_callback(self.FILTERS)
        self.assertEqual(self.FILTERS, telegram_bot.parse_liquidity_callback(liquid))
        for bad in ("f:60000-50000:0:0:0:golf", "f:50000-60000:2024-2022:0:0:x", "f:5-60000:0:0:0:x",
                    "f:a-b:0:0:0:x", "f:-:0:0:0:x"):
            self.assertIsNone(telegram_bot.parse_refine_callback(bad), bad)

    def test_model_buttons_and_refinements_keep_the_range(self):
        with patch("telegram_bot.suggest_message", return_value=(HEADER, ["Škoda Kodiaq"])):
            _, choices = telegram_bot.answer_intent(Mock(), Intent("suggest", self.FILTERS), CONFIG, lang="bs")
        self.assertEqual(Filters("Škoda Kodiaq", 60000, 2022, price_min=50000, year_max=2024),
                         telegram_bot.parse_refine_callback(choices.rows[0][0][1]))
        page = telegram_bot.Page(Filters("golf", 60000, 2022, price_min=50000, year_max=2024), min_year=2022)
        buttons = {b["text"]: b.get("callback_data")
                   for row in telegram_bot.keyboard(CONFIG, page, "bs")["inline_keyboard"] for b in row}
        cheaper = telegram_bot.parse_refine_callback(next(v for k, v in buttons.items() if "−10%" in k))
        self.assertEqual((45000, 54000), (cheaper.price_min, cheaper.budget))
        newer = [v for k, v in buttons.items() if k.startswith("📅")]
        for data in newer:
            parsed = telegram_bot.parse_refine_callback(data)
            self.assertLessEqual(parsed.year_min, 2024)


class GeminiGroundingTests(unittest.TestCase):
    def test_plans_keep_both_ends_of_a_range(self):
        intent = intent_from_plan({"intent": "suggest", "budget_km": 60000, "price_min_km": 50000,
                                   "year_min": 2022, "year_max": 2024}, None, QUESTION)
        self.assertEqual(("suggest", EXPECTED), (intent.kind, bounds(intent.filters)))
        swapped = intent_from_plan({"intent": "find", "search": "golf", "budget_km": 50000,
                                    "price_min_km": 60000}, None, "golf")
        self.assertEqual((50000, 60000), (swapped.filters.price_min, swapped.filters.budget))
        self.assertIn("price_min_km", telegram_llm.SCHEMA["properties"])
        self.assertIn("year_max", telegram_llm.SCHEMA["properties"])

    def test_a_plan_correction_merges_into_the_previous_scope(self):
        previous = parse_question(QUESTION)
        intent = intent_from_plan({"intent": "suggest", "refines_previous": True, "budget_km": 60000,
                                   "price_min_km": 50000}, previous, CORRECTION)
        self.assertEqual(("suggest", EXPECTED), (intent.kind, bounds(intent.filters)))
        self.assertIn("price_min_km", telegram_llm._previous_scope(previous))

    def test_readings_that_drop_or_change_a_stated_bound_are_rejected(self):
        self.assertTrue(bounds_match(parse_question(QUESTION).filters, QUESTION))
        self.assertFalse(bounds_match(Filters(budget=60000, year_min=2022), QUESTION))  # the old answer
        self.assertFalse(bounds_match(Filters(budget=60000, year_min=2022, price_min=50000), QUESTION))
        previous = parse_question(QUESTION)
        self.assertTrue(bounds_match(previous.filters, CORRECTION, previous))
        self.assertFalse(bounds_match(Filters(mileage_max=60000), CORRECTION, previous))

    def test_understand_falls_back_to_the_parser_when_gemini_narrows_the_range(self):
        long_question = ("Pronadji mi molim te neki fin porodicni auto sa dosta prostora od 50 - 60k, "
                         "godiste od 2022 - 2024")
        parsed = parse_question(long_question)
        narrowed = Intent("find", Filters(budget=60000, year_min=2022, family=True))
        kept = Intent("find", Filters(budget=60000, year_min=2022, family=True, price_min=50000, year_max=2024))
        for gemini, expected in ((narrowed, parsed), (kept, kept)):
            trace = {}
            with patch("telegram_bot._needs_interpreter", return_value=True):
                result = telegram_bot._understand(long_question, {}, None, lambda *a, **k: gemini, trace=trace)
            self.assertEqual(expected, result)

    def test_phrasing_that_narrows_the_range_or_refuses_is_not_sent(self):
        filters = parse_question(QUESTION).filters
        self.assertFalse(reply_keeps_range("Evo modela do 60.000 KM.", filters))
        self.assertTrue(reply_keeps_range("Između 50.000 i 60.000 KM, 2022–2024, najviše je Kodiaqa.", filters))
        allowed = telegram_llm._numbers("50.000 60.000")
        self.assertEqual("", telegram_llm._safe_text("Izvini, ja samo gledam podatke iz baze i ne mogu mijenjati "
                                                     "tvoje pretrage.", allowed))
        self.assertEqual("", telegram_llm._safe_text("Sorry, I can't change your searches.", allowed))

    def test_the_prompt_explains_ranges_and_corrections(self):
        prompt = telegram_llm.SYSTEM_PROMPT
        self.assertIn("price_min_km 50000", prompt)
        self.assertIn("year_max 2024", prompt)
        self.assertIn("Never reply that you cannot change their searches", prompt)


def telegram_fake(member_count=9):
    def call(_config, method, payload):
        if method == "getChatMemberCount":
            return member_count
        if method == "getChatMember":
            return {"status": "member", "user": {"id": payload["user_id"]}}
        return {"message_id": 77}
    return Mock(side_effect=call)


def say(text_value, *, user=123, uid=1, chat_id=GROUP, chat_type="supergroup", reply=None):
    message = {"chat": {"id": chat_id, "type": chat_type}, "from": {"id": user}, "text": text_value}
    if reply is not None:
        message["reply_to_message"] = reply
    return {"update_id": uid, "message": message}


class OgisConversationFlowTests(unittest.TestCase):
    """Both messages through process_update, the way Telegram delivers them."""

    def setUp(self):
        clear_memory()

    def run_update(self, update, call, phraser=None):
        answer = Mock(side_effect=lambda engine, intent, config, lang: (
            telegram_bot._suggest_title(intent.filters, lang) + "\n\nNajviše oglasa:\n• Škoda Kodiaq", False))
        with patch("telegram_bot.answer_intent", answer):
            process_update(update, Mock(), CONFIG, call=call, llm=None, phraser=phraser)
        sent = [c.args[2]["text"] for c in call.call_args_list if c.args[1] in {"sendMessage", "editMessageText"}]
        return (answer.call_args.args[1] if answer.called else None), sent[-1] if sent else None

    def assert_ranges(self, intent, sent):
        self.assertEqual(("suggest", EXPECTED), (intent.kind, bounds(intent.filters)))
        self.assertTrue(sent.startswith(HEADER), sent)
        self.assertNotRegex(sent, CANNOT_CHANGE)

    def test_group_correction_right_after_the_answer(self):
        call = telegram_fake()
        intent, sent = self.run_update(say("@mojagaraza_bot " + QUESTION, uid=1), call)
        self.assert_ranges(intent, sent)
        intent, sent = self.run_update(say(CORRECTION, uid=2), call)  # no tag: inside the group window
        self.assert_ranges(intent, sent)
        self.assertEqual("correction", intent.note)

    def test_group_correction_as_a_reply_to_the_answer(self):
        call = telegram_fake()
        _, first_answer = self.run_update(say("@mojagaraza_bot " + QUESTION, uid=1), call)
        clear_memory()  # another server instance: only the replied-to message is known
        reply = {"from": {"id": 999, "is_bot": True, "username": "mojagaraza_bot"}, "text": first_answer}
        intent, sent = self.run_update(say(CORRECTION, uid=2, reply=reply), call)
        self.assert_ranges(intent, sent)

    def test_private_chat_correction(self):
        call = telegram_fake()
        intent, sent = self.run_update(say(QUESTION, uid=1, chat_id=123, chat_type="private"), call)
        self.assert_ranges(intent, sent)
        intent, sent = self.run_update(say(CORRECTION, uid=2, chat_id=123, chat_type="private"), call)
        self.assert_ranges(intent, sent)

    def test_someone_elses_question_is_never_corrected(self):
        call = telegram_fake()
        self.run_update(say("@mojagaraza_bot " + QUESTION, uid=1), call)
        intent, sent = self.run_update(say("@mojagaraza_bot " + CORRECTION_LOWER, user=456, uid=2), call)
        self.assertEqual(("clarify", "correction"), (intent.kind, intent.note))
        self.assertNotRegex(sent, CANNOT_CHANGE)

    def test_a_phrased_reply_that_drops_the_lower_bound_falls_back_to_the_database_answer(self):
        call = telegram_fake()
        phraser = Mock(return_value=("Evo šta se najviše nudi do 60.000 KM.", False))
        intent, sent = self.run_update(say(QUESTION, uid=1, chat_id=123, chat_type="private"), call, phraser)
        phraser.assert_called_once()
        self.assert_ranges(intent, sent)


def _naive_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@unittest.skipUnless(os.getenv("DEALER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class RangeDatabaseTests(unittest.TestCase):
    """Both ends of each range are applied in SQL, so the counts and medians stay inside them."""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["DEALER_TEST_DATABASE_URL"])

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.cleanup()
        self.addCleanup(self.cleanup)
        now = _naive_now()
        rows = [  # id, make, model, year, price, mileage
            ("__qa_r_k1", "Škoda", "Kodiaq", 2022, 52000, 40000),
            ("__qa_r_k2", "Škoda", "Kodiaq", 2023, 55000, 30000),
            ("__qa_r_k3", "Škoda", "Kodiaq", 2024, 59000, 10000),
            ("__qa_r_o1", "Škoda", "Octavia", 2022, 33000, 60000),  # under the range
            ("__qa_r_o2", "Škoda", "Octavia", 2023, 34000, 50000),
            ("__qa_r_o3", "Škoda", "Octavia", 2024, 33500, 20000),
            ("__qa_r_c1", "Toyota", "Corolla", 2026, 55000, 0),  # newer than the range
            ("__qa_r_c2", "Toyota", "Corolla", 2026, 56000, 0),
            ("__qa_r_c3", "Toyota", "Corolla", 2026, 57000, 0),
            ("__qa_r_x1", "Škoda", "Kodiaq", 2023, 75000, 5000),  # over the range
        ]
        with self.engine.begin() as c:
            for lid, make, model, year, price, mileage in rows:
                c.execute(text("""INSERT INTO car_listings(listing_id,make,model,year,price,price_status,
                    mileage,source,collection_method,last_verified_at,first_seen_at,is_active,
                    consecutive_missing_runs) VALUES (:id,:make,:model,:year,:price,'priced',:mileage,
                    'olx.ba','olx_api',:seen,:first,true,0)"""),
                    {"id": lid, "make": make, "model": model, "year": year, "price": price, "mileage": mileage,
                     "seen": now - timedelta(days=1), "first": now - timedelta(days=20)})

    def cleanup(self):
        with self.engine.begin() as c:
            c.execute(text("DELETE FROM car_listings WHERE listing_id LIKE '__qa_r_%'"))

    def test_the_shortlist_counts_and_medians_stay_inside_both_ranges(self):
        intent = parse_question(QUESTION)
        reply = telegram_bot.suggest_message(self.engine, intent.filters, lang="bs")
        self.assertEqual(HEADER, reply.split("\n")[0])
        self.assertIn("Škoda Kodiaq · 3 oglasa · medijan 55.000 KM", reply)
        self.assertNotIn("Octavia", reply)
        self.assertNotIn("Corolla", reply)
        corrected = parse_question(CORRECTION, intent)
        self.assertEqual(reply, telegram_bot.suggest_message(self.engine, corrected.filters, lang="bs"))

    def test_listings_and_liquidity_apply_both_ends(self):
        filters = Filters("skoda", 60000, 2022, price_min=50000, year_max=2024)
        reply = telegram_bot.html_to_text(telegram_bot.find_message(self.engine, filters, lang="bs"), keep_links=True)
        self.assertTrue(reply.startswith("Viđeni oglasi · skoda · 50.000–60.000 KM · 2022–2024"), reply)
        self.assertEqual({"__qa_r_k1", "__qa_r_k2", "__qa_r_k3"}, set(re.findall(r"artikal/(__qa_r_\w+)", reply)))
        liquidity = telegram_bot.html_to_text(telegram_bot.liquidity_message(self.engine, filters, lang="bs"))
        self.assertTrue(liquidity.startswith("⏱ Najbrže nestaju iz naše pretrage · skoda · 50.000–60.000 KM"))


if __name__ == "__main__":
    unittest.main()
