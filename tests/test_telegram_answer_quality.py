"""Regression tests for the eight answer-quality fixes (21 Nov 2026).

Each class quotes the real failing exchange from Ogi's group. The parser tests need nothing; the database
tests need a throwaway PostgreSQL (DEALER_TEST_DATABASE_URL). None of these paths needs Gemini.
"""

from datetime import datetime, timedelta, timezone
import os
import re
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, text

import telegram_bot
import telegram_i18n
import telegram_llm
from telegram_bot import Choices, Page, TelegramConfig, answer_intent, intent_notes, keyboard, process_update
from telegram_intent import Filters, Intent, is_correction, parse_question

ENV = {
    "TELEGRAM_BOT_TOKEN": "test-only-token",
    "TELEGRAM_WEBHOOK_SECRET": "test_only_webhook_secret_123",
    "TELEGRAM_CHAT_ID": "-5332163835",
    "TELEGRAM_ALLOWED_USER_IDS": "123, 456",
}
CONFIG = TelegramConfig.from_env(ENV)
THIS_YEAR = datetime.now(timezone.utc).year
TAG_WORDS = re.compile(r"tag|označ|oznac|spomen", re.I)


def setUpModule():
    patcher = patch.dict(os.environ, {}, clear=False)
    patcher.start()
    for name in ("GEMINI_API_KEY", "TELEGRAM_DEFAULT_LANGUAGE", "TELEGRAM_GROUP_WINDOW_SECONDS"):
        os.environ.pop(name, None)
    globals()["_env_patch"] = patcher


def tearDownModule():
    _env_patch.stop()


def dm(text_value, uid=123):
    return {"update_id": 1, "message": {"message_id": 5, "text": text_value, "chat": {"id": uid, "type": "private"},
                                        "from": {"id": uid, "language_code": "bs"}}}


def sent_text(call):
    texts = [c.args[2].get("text") for c in call.call_args_list if c.args[1] in {"sendMessage", "editMessageText"}]
    return texts[-1]


def labels(markup):
    return [label for row in markup.rows for label, _ in row]


def clear_memory():
    for store in (telegram_bot._SCOPES, telegram_bot._BUDGETS, telegram_bot._HISTORY, telegram_bot._LANGS,
                  telegram_bot._ACTIVE):
        store.clear()


# ---- 1. units: "do N km" with no model ------------------------------------------------------------

class UnitTests(unittest.TestCase):
    def test_a_lone_do_30000km_asks_price_or_mileage_instead_of_capping_mileage(self):
        intent = parse_question("Auto do 30 000km da nije starije od 10 godina")
        self.assertEqual(("clarify", "km_unit", 30000), (intent.kind, intent.note, intent.offset))
        self.assertIsNone(intent.filters.mileage_max)  # not a silent mileage cap
        self.assertEqual(THIS_YEAR - 10, intent.filters.year_min)  # what else was said is kept
        message, choices = answer_intent(Mock(), intent, CONFIG, lang="bs")
        self.assertEqual("Cijena (KM) ili kilometraža? (30.000)", message)
        self.assertEqual(2, len(choices.rows))  # two buttons: price, mileage
        price = telegram_bot.parse_refine_callback(choices.rows[0][0][1])
        mileage = telegram_bot.parse_refine_callback(choices.rows[1][0][1])
        self.assertEqual((30000, None, THIS_YEAR - 10), (price.budget, price.mileage_max, price.year_min))
        self.assertEqual((None, 30000, THIS_YEAR - 10), (mileage.budget, mileage.mileage_max, mileage.year_min))
        message, _ = answer_intent(Mock(), intent, CONFIG, lang="en")
        self.assertEqual("Price (KM) or mileage? (30.000)", message)

    def test_a_resale_question_with_a_lone_km_amount_keeps_the_resale_buttons(self):
        intent = parse_question("sta se najlakse proda do 20000km")
        self.assertEqual(("clarify", "km_unit", ("liquidity",)), (intent.kind, intent.note, intent.labels))

    def test_explicit_km_still_parses_as_price_and_explicit_kilometraza_as_mileage(self):
        for message, field in (("Auto do 30 000 KM", "budget"), ("30 000 KM", "budget"), ("do 30000 KM", "budget"),
                               ("Auto do 30 000 BAM", "budget"), ("Auto do 30 000 maraka", "budget"),
                               ("Golf do 30 000km", "mileage_max"),  # a model: mileage as before
                               ("Auto do 30 000 kilometara", "mileage_max"),
                               ("Auto kilometraža do 30000 km", "mileage_max"),
                               ("Auto do 30000 km kilometraža", "mileage_max"),
                               ("auto do 200000km", "mileage_max")):  # nobody caps a price this high
            intent = parse_question(message)
            self.assertEqual("find", intent.kind, message)
            self.assertEqual(200000 if "200000" in message else 30000, getattr(intent.filters, field), message)

    def test_a_follow_up_km_amount_after_a_search_is_still_mileage(self):
        context = Intent("find", Filters("golf", 15000))
        self.assertEqual(100000, parse_question("do 100000km", context).filters.mileage_max)

    def test_kn_is_a_price_with_a_visible_note(self):
        intent = parse_question("Auto do 30000kn")
        self.assertEqual(("find", 30000, ""), (intent.kind, intent.filters.budget, intent.filters.search))
        self.assertIn("kn", intent.labels)
        self.assertEqual(["„kn“ čitam kao KM (konvertibilne marke), ne kune: cijene su ovdje u KM."],
                         intent_notes(intent, "bs"))
        self.assertIn("not kuna", intent_notes(intent, "en")[0])
        self.assertNotIn("kn", parse_question("Auto do 30000 KM").labels)
        text_value = telegram_bot._with_notes("Odgovor", intent, "bs")
        self.assertIn("„kn“ čitam kao KM", text_value)


# ---- 2. corrections that keep the previous search -------------------------------------------------

class BudgetCorrectionTests(unittest.TestCase):
    PREVIOUS = Intent("find", Filters("golf", 15000))

    def test_budzet_je_30k_a_prva_dva_su_izvan_budzeta_keeps_the_search(self):
        message = "Budžet je 30k a prva dva su izvan budžeta"
        intent = parse_question(message, self.PREVIOUS)
        self.assertEqual(("find", "correction", "golf", 30000), (intent.kind, intent.note, intent.filters.search,
                                                                   intent.filters.budget))
        self.assertTrue(is_correction(message))
        self.assertNotEqual("unsupported", intent.kind)

    def test_imam_x_and_izvan_budzeta_are_corrections_too(self):
        self.assertEqual(("golf", 30000), self._read("imam 30k"))
        self.assertEqual(("golf", 25000), self._read("Imam 25 000 KM"))
        self.assertEqual(("golf", 15000), self._read("prvi su izvan budžeta"))  # same cap again, same search
        self.assertEqual(("golf", 15000), self._read("izvan budžeta"))
        self.assertTrue(is_correction("izvan budžeta"))
        self.assertTrue(is_correction("imam 30k"))
        self.assertFalse(is_correction("Golf do 15000 KM"))

    def _read(self, message):
        intent = parse_question(message, self.PREVIOUS)
        self.assertEqual(("find", "correction"), (intent.kind, intent.note), message)
        return intent.filters.search, intent.filters.budget

    def test_the_other_filters_of_the_previous_search_are_kept(self):
        previous = Intent("find", Filters("golf", 15000, year_min=2018, fuel="diesel"))
        filters = parse_question("budžet je 30k", previous).filters
        self.assertEqual(("golf", 30000, 2018, "diesel"), (filters.search, filters.budget, filters.year_min, filters.fuel))

    def test_a_30k_misread_as_mileage_is_dropped_when_the_budget_is_stated(self):
        previous = Intent("find", Filters(year_min=THIS_YEAR - 10, mileage_max=30000))  # the 30.000 km misread
        filters = parse_question("Budžet je 30k a prva dva su izvan budžeta", previous).filters
        self.assertEqual((30000, None, THIS_YEAR - 10), (filters.budget, filters.mileage_max, filters.year_min))

    def test_without_a_cap_anywhere_the_bot_asks_for_the_budget(self):
        intent = parse_question("prvi su izvan budžeta", Intent("find", Filters("golf")))
        self.assertEqual(("clarify", "over_budget"), (intent.kind, intent.note))
        message, choices = answer_intent(Mock(), intent, CONFIG, lang="bs")
        self.assertIn("budžet", message)
        self.assertIsInstance(choices, Choices)  # budget buttons

    def test_a_message_that_also_asks_for_resale_is_not_a_correction(self):
        intent = parse_question("imam 30k, šta se najbrže prodaje", self.PREVIOUS)
        self.assertNotEqual("correction", intent.note)

    def test_the_correction_reaches_a_group_member_who_corrects_their_own_search(self):
        clear_memory()
        key = (-100, 123)
        telegram_bot._remember_scope(key, Intent("find", Filters("golf", 15000)))
        intent = telegram_bot._understand("Budžet je 30k a prva dva su izvan budžeta", {}, key, None, chat_kind="group")
        self.assertEqual(("find", "golf", 30000), (intent.kind, intent.filters.search, intent.filters.budget))


# ---- 3. a stated budget is carried across turns -----------------------------------------------------

class RememberedBudgetTests(unittest.TestCase):
    def setUp(self):
        clear_memory()
        self.key = (123, 123)

    def understand(self, message):
        intent = telegram_bot._understand(message, {}, self.key, None, chat_kind="private")
        telegram_bot._remember_scope(self.key, intent)
        return intent

    def test_a_budget_given_earlier_is_used_for_koji_auto_koji_modeli(self):
        self.understand("Ok imam 30k budžet")
        self.understand("koji su trendovi")  # the scope is now a trends question, which has no budget
        intent = self.understand("Da ali koji auto, koji modeli")
        self.assertEqual(("suggest", 30000, ("budget_reused",)), (intent.kind, intent.filters.budget, intent.labels))
        self.assertEqual(["Koristim tvoj budžet od 30.000 KM od ranije."], intent_notes(intent, "bs"))

    def test_without_an_earlier_budget_it_still_asks(self):
        intent = self.understand("Da ali koji auto, koji modeli")
        self.assertEqual("clarify", intent.kind)

    def test_a_new_budget_replaces_the_old_one(self):
        self.understand("Ok imam 30k budžet")
        self.understand("Golf do 12000 KM")
        self.assertEqual(12000, self.understand("koji auto").filters.budget)

    def test_the_budget_is_per_person_and_expires(self):
        self.understand("Ok imam 30k budžet")
        other = telegram_bot._understand("koji auto", {}, (123, 456), None, chat_kind="private")
        self.assertEqual("clarify", other.kind)
        with patch("telegram_bot.time.monotonic", return_value=telegram_bot.time.monotonic() + 3600):
            self.assertEqual("clarify", self.understand("koji auto").kind)

    def test_a_message_with_its_own_budget_is_not_overwritten(self):
        self.understand("Ok imam 30k budžet")
        intent = self.understand("koji auto do 10000 KM")
        self.assertEqual((10000, ()), (intent.filters.budget, intent.labels))


# ---- 4. mixed messages ---------------------------------------------------------------------------

class MixedMessageTests(unittest.TestCase):
    MESSAGE = "Ok imam 30k budžet. Koje auto da kupim koje mogu brzo prodati. Koji su trendovi"

    def test_the_first_clear_intent_is_answered_and_the_skipped_one_is_named(self):
        intent = parse_question(self.MESSAGE)
        self.assertEqual("liquidity", intent.kind)  # not 7-day trends, as before
        self.assertEqual(30000, intent.filters.budget)  # the stated budget is not dropped
        self.assertEqual(("skipped:trends",), intent.labels)
        self.assertEqual(["Poruka je tražila više stvari. Odgovorio sam na prvu; preskočeno: trendove cijena. "
                          "Pitaj to posebno."], intent_notes(intent, "bs"))
        self.assertIn("skipped: price trends", intent_notes(intent, "en")[0])

    def test_a_single_request_is_parsed_as_before(self):
        for message in ("Golf do 15000 KM", "Golf do 15000 KM. Hvala", "koji su trendovi", "trendovi zadnjih 30 dana"):
            self.assertFalse([l for l in parse_question(message).labels if l.startswith("skipped")], message)
        self.assertEqual("trends", parse_question("koji su trendovi").kind)

    def test_two_requests_without_a_stated_budget(self):
        intent = parse_question("Šta se najbrže prodaje do 15000 KM? Pokaži trendove.")
        self.assertEqual(("liquidity", 15000, ("skipped:trends",)), (intent.kind, intent.filters.budget, intent.labels))

    def test_the_note_survives_a_chatty_rewrite_and_shows_after_the_answer(self):
        intent = parse_question(self.MESSAGE)
        self.assertTrue(telegram_bot._with_notes("Evo liste.", intent, "bs").endswith("Pitaj to posebno."))
        once = telegram_bot._with_notes("Evo liste.", intent, "bs")
        self.assertEqual(once, telegram_bot._with_notes(once, intent, "bs"))  # never doubled


# ---- database -----------------------------------------------------------------------------------

class AnswerQualityDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["DEALER_TEST_DATABASE_URL"])

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        clear_memory()
        self.cleanup()
        self.addCleanup(self.cleanup)
        self.now = datetime.now(timezone.utc).replace(tzinfo=None)

    def cleanup(self):
        with self.engine.begin() as c:
            c.execute(text("DELETE FROM listing_snapshots WHERE listing_id LIKE '__qa_aq_%'"))
            c.execute(text("DELETE FROM car_listings WHERE listing_id LIKE '__qa_aq_%'"))

    def seed(self, rows, *, first_days=20, last_days=1):
        """rows: (id, make, model, year, price, mileage[, fuel, transmission, location])"""
        with self.engine.begin() as c:
            for lid, make, model, year, price, mileage, *extra in rows:
                fuel, gearbox, city = (list(extra) + [None, None, None])[:3]
                c.execute(text("""INSERT INTO car_listings(listing_id,make,model,year,price,price_status,mileage,
                    fuel_type,transmission,location,source,collection_method,last_verified_at,first_seen_at,
                    is_active,consecutive_missing_runs) VALUES (:id,:make,:model,:year,:price,'priced',:mileage,
                    :fuel,:gearbox,:city,'olx.ba','olx_api',:last,:first,true,0)"""),
                    {"id": f"__qa_aq_{lid}", "make": make, "model": model, "year": year, "price": price,
                     "mileage": mileage, "fuel": fuel, "gearbox": gearbox, "city": city,
                     "first": self.now - timedelta(days=first_days), "last": self.now - timedelta(days=last_days)})

    def answer(self, message, lang="bs", context=None):
        text_value, markup = answer_intent(self.engine, parse_question(message, context), CONFIG, lang=lang)
        return text_value, markup

    def ids(self, reply):
        return re.findall(r"artikal/__qa_aq_(\w+)", reply)

    # -- 6. sort
    def test_a_search_without_a_model_lists_the_cheapest_first(self):
        self.seed([("lux", "Mercedes-Benz", "S 500", THIS_YEAR - 3, 28000, 30000),
                   ("mid", "Volkswagen", "Golf", 2016, 12000, 150000),
                   ("low", "Škoda", "Fabia", 2010, 4000, 200000),
                   ("new", "Audi", "A3", THIS_YEAR - 2, 29500, 20000)])
        reply, _ = self.answer("Auto do 30000 KM")
        self.assertEqual(["low", "mid", "lux", "new"], self.ids(reply))  # price ascending, not newest year first
        self.assertIn("prvo najjeftiniji", reply)
        reply, _ = self.answer("Auto do 30000 KM", lang="en")
        self.assertIn("cheapest first", reply)

    def test_a_search_with_a_model_keeps_newest_year_first(self):
        self.seed([("g1", "Volkswagen", "Golf", 2012, 5000, 220000), ("g2", "Volkswagen", "Golf", 2019, 19000, 90000),
                   ("g3", "Volkswagen", "Golf", 2016, 9000, 150000)])
        reply, _ = self.answer("golf do 30000 KM")
        self.assertEqual(["g2", "g3", "g1"], self.ids(reply))
        self.assertNotIn("najjeftiniji", reply)

    # -- 7. formatting
    def test_rows_show_fuel_gearbox_and_city_and_tell_price_from_mileage(self):
        self.seed([("a1", "Volkswagen", "Golf", 2022, 134900, 30000, "diesel", "automatic", "Sarajevo")])
        reply, page = self.answer("golf")
        self.assertIn("💰 <b>134.900 KM</b> · 🛣 30.000 km", reply)
        self.assertIn("Dizel · Automatik · Sarajevo · Viđeno", reply)
        english, _ = self.answer("golf", lang="en")
        self.assertIn("Diesel · Automatic · Sarajevo · Seen", english)

    def test_a_row_without_fuel_gearbox_or_city_shows_only_what_is_recorded(self):
        self.seed([("a2", "Volkswagen", "Golf", 2016, 9000, 150000, "petrol", None, None)])
        reply, _ = self.answer("golf")
        self.assertIn("Benzin · Viđeno", reply)
        self.assertNotIn("None", reply)

    def test_the_footers_are_one_compact_line(self):
        self.seed([("a3", "Volkswagen", "Golf", 2016, 9000, 150000), ("a4", "Volkswagen", "Golf", 2015, 8000, 170000)],
                  last_days=30)
        reply, _ = self.answer("golf do 20000 KM, od 2014 do 200000 km i stariji")
        lines = reply.rstrip().split("\n")
        self.assertTrue(lines[-1].startswith("<i>ℹ️ Viđene tražene cijene, ne preporuke"), lines[-1])
        self.assertEqual("", lines[-2])
        self.assertEqual(1, sum(1 for line in lines if line.startswith("<i>")))  # nothing else italic, no 3-4 note lines
        self.assertIn("⚠ viđeno prije više od 14 dana", lines[-1])
        self.assertIn("oglasi bez godišta ili kilometraže", lines[-1])

    def test_save_buttons_carry_make_and_model(self):
        self.seed([("s1", "Volkswagen", "Golf", 2022, 134900, 30000, "diesel", "automatic", "Sarajevo"),
                   ("s2", "Škoda", "Octavia", 2018, 15000, 120000)])
        _, page = self.answer("auto do 200000 KM")
        rows = keyboard(CONFIG, page, "bs")["inline_keyboard"]
        stars = [b["text"] for row in rows for b in row if b["text"].startswith("⭐")]
        self.assertEqual(["⭐ Škoda Octavia · 2018 · 15.000 KM", "⭐ Volkswagen Golf · 2022 · 134.900 KM"], stars)

    def test_a_very_long_name_is_cut_so_that_year_and_price_stay_visible(self):
        label = telegram_bot._save_label("Mercedes-Benz E 63 AMG S 4MATIC+ Edition 1 Exclusive", 2022, 134900, "bs")
        self.assertTrue(label.endswith("· 2022 · 134.900 KM"), label)
        self.assertLessEqual(len(label), telegram_bot.SAVE_LABEL_MAX)

    # -- kn
    def test_kn_answer_says_it_was_read_as_km(self):
        self.seed([("k1", "Volkswagen", "Golf", 2016, 9000, 150000)])
        reply, _ = self.answer("Auto do 30000kn")
        self.assertIn("„kn“ čitam kao KM", reply)
        self.assertIn("__qa_aq_k1", reply)

    # -- 5. resale
    def test_the_resale_question_with_kn_ends_with_a_proxy_list_not_a_dead_end(self):
        self.seed([("l1", "Volkswagen", "Golf", 2016, 9000, 150000), ("l2", "Volkswagen", "Golf", 2017, 11000, 120000),
                   ("l3", "Škoda", "Octavia", 2015, 8000, 200000)])
        message = "Bilo koji model koji se brzo prodaje do 30000kn"
        intent = parse_question(message)
        self.assertEqual(("liquidity", "", 30000), (intent.kind, intent.filters.search, intent.filters.budget))
        reply, buttons = self.answer(message)
        self.assertIn("Nema dovoljno podataka", reply)  # nothing can be ranked yet ...
        self.assertIn("1. Volkswagen Golf: 2 oglasa", reply)  # ... but it still ends with the most listed models
        self.assertIn("2. Škoda Octavia: 1 oglasa", reply)
        self.assertIn("„kn“ čitam kao KM", reply)
        self.assertEqual(2, len(buttons.rows[0]))
        self.assertIsNone(TAG_WORDS.search(reply))

    def test_a_chatty_rewrite_cannot_replace_the_resale_list(self):
        self.seed([("p1", "Volkswagen", "Golf", 2016, 9000, 150000), ("p2", "Škoda", "Octavia", 2015, 8000, 200000)])
        phraser = Mock(return_value=("Trenutno nema dovoljno podataka da bih rekao šta se brzo prodaje.", False))
        call = Mock(return_value={"message_id": 1})
        process_update(dm("Bilo koji model koji se brzo prodaje do 30000kn"), self.engine, CONFIG, call=call,
                       llm=None, phraser=phraser)
        reply = sent_text(call)
        self.assertIn("Trenutno nema dovoljno podataka", reply)  # the chat sentence is still there ...
        self.assertIn("Volkswagen Golf: 1 oglasa", reply)  # ... and the list is attached whatever show_details said
        self.assertIn("„kn“ čitam kao KM", reply)
        self.assertIsNone(TAG_WORDS.search(reply))

    def test_without_a_ranking_or_most_listed_models_it_lists_the_newest_cars(self):
        self.seed([("n1", "Volkswagen", "Golf", 2015, 9000, 150000), ("n2", "Volkswagen", "Golf", 2018, 12000, 90000)],
                  first_days=60, last_days=30)  # nothing seen in the last 14 days: no proxy
        reply, _ = self.answer("šta se najbrže prodaje do 15000 KM")
        text_value = telegram_bot.html_to_text(reply)
        self.assertIn("evo najnovijih auta", text_value)
        self.assertLess(text_value.index("Volkswagen Golf"), text_value.index("Nije preporuka"))
        self.assertEqual(["n2", "n1"], self.ids(reply))  # newest model year first

    def test_a_resale_answer_that_ranks_models_is_unchanged(self):
        rows = [(f"r{n}", "Volkswagen", "Golf", 2018, 15000, 120000 + n) for n in range(6)]
        self.seed(rows, first_days=60, last_days=54)
        self.seed([(f"y{n}", "Toyota", "Yaris", 2018, 14000, 100000) for n in range(8)], first_days=20, last_days=0)
        reply, _ = self.answer("šta se najbrže prodaje do 20000 KM")
        self.assertIn("1. <b>Volkswagen Golf</b>: medijan 6 dana", reply)
        self.assertNotIn("evo najnovijih", reply)

    # -- 8. group tag reminder
    def test_no_group_tag_reminder_in_an_ordinary_group_answer(self):
        self.seed([("t1", "Volkswagen", "Golf", 2016, 9000, 150000)])
        message = {"message_id": 5, "text": "@mojagaraza_bot Golf do 15000 KM", "chat": {"id": -5332163835, "type": "supergroup"},
                   "from": {"id": 123}}
        phraser = Mock(return_value=("Evo šta imamo.", True))
        call = Mock(return_value={"message_id": 1})
        env = {**ENV, "TELEGRAM_BOT_USERNAME": "mojagaraza_bot"}
        process_update({"update_id": 1, "message": message}, self.engine, TelegramConfig.from_env(env), call=call,
                       llm=None, phraser=phraser)
        self.assertEqual("group", phraser.call_args.kwargs["chat_kind"])
        self.assertIsNone(TAG_WORDS.search(sent_text(call)))


class GroupTagReminderTests(unittest.TestCase):
    CFG = telegram_llm.LLMConfig(api_key="test-only-key")

    def test_the_persona_no_longer_tells_gemini_to_remind_people_to_tag(self):
        self.assertNotIn("they should tag you", telegram_llm.PERSONA)
        self.assertIn("Never mention tagging", telegram_llm.PERSONA)
        self.assertIn("Never mention tagging", telegram_llm.PHRASE_PROMPT)

    def test_a_phrased_reply_with_a_tag_reminder_is_rejected(self):
        allowed = set()
        for reminder in ("U grupi moraš tagovati bota da bih odgovorio.", "Tagiraj me pa ću odgovoriti.",
                         "In a group, tag me or reply to my message.", "Please mention me first.",
                         "Odgovori na moju poruku da nastavimo."):
            self.assertEqual("group tag reminder", telegram_llm._check_text(reminder, allowed)[1], reminder)
        self.assertEqual(("Našao sam nekoliko oglasa.", ""), telegram_llm._check_text("Našao sam nekoliko oglasa.", allowed))

    def test_phrase_falls_back_to_the_database_answer_when_the_reply_nags_about_tagging(self):
        response = Mock(status_code=200)
        response.json.return_value = {"candidates": [{"content": {"parts": [{"text":
            '{"reply": "Evo oglasa. U grupi moraš tagovati bota.", "show_details": true}'}]}}]}
        result = telegram_llm.phrase("golf", "Viđeni oglasi · golf", self.CFG, post=Mock(return_value=response),
                                     chat_kind="group")
        self.assertIsNone(result)
        self.assertIn("group tag reminder", telegram_llm.PHRASE_STATUS.get())

    def test_the_resale_prompt_always_asks_for_the_list(self):
        post = Mock()
        response = Mock(status_code=200)
        response.json.return_value = {"candidates": [{"content": {"parts": [{"text":
            '{"reply": "Evo.", "show_details": true}'}]}}]}
        post.return_value = response
        telegram_llm.phrase("šta se brzo prodaje", "facts", self.CFG, post=post, mode="liquidity")
        prompt = post.call_args.kwargs["json"]["systemInstruction"]["parts"][0]["text"]
        self.assertIn("always set show_details to true", prompt)

    def test_the_i18n_templates_have_no_tag_reminder(self):
        for lang, strings in telegram_i18n.STRINGS.items() if hasattr(telegram_i18n, "STRINGS") else ():
            for key, value in strings.items():
                if key != "help":  # /help explains how to reach the bot; ordinary answers never do
                    self.assertIsNone(re.search(r"tagov|tagiraj|tag me", value, re.I), (lang, key))


if __name__ == "__main__":
    unittest.main()
