"""Fixes for the parser and Q&A findings of the 16 Nov 2026 use-case sweep
(docs/qa/bot-usecase-sweep-2026-11-16.md).

Every quoted message is the real sweep message (row number in a comment) or a close variant of it. The
parser tests need nothing; the database tests need a throwaway PostgreSQL (DEALER_TEST_DATABASE_URL).
None of these paths needs Gemini.
"""

from datetime import datetime, timedelta, timezone
import os
import re
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text

import telegram_bot
import telegram_i18n
from telegram_bot import TelegramConfig, answer_intent
from telegram_intent import Filters, context_from_reply, merge_filters, parse_question, repair_typo

ENV = {
    "TELEGRAM_BOT_TOKEN": "test-only-token",
    "TELEGRAM_WEBHOOK_SECRET": "test_only_webhook_secret_123",
    "TELEGRAM_CHAT_ID": "-5332163835",
    "TELEGRAM_ALLOWED_USER_IDS": "123, 456",
}
CONFIG = TelegramConfig.from_env(ENV)


def setUpModule():
    patcher = patch.dict(os.environ, {}, clear=False)
    patcher.start()
    for name in ("GEMINI_API_KEY", "TELEGRAM_DEFAULT_LANGUAGE", "TELEGRAM_GROUP_WINDOW_SECONDS"):
        os.environ.pop(name, None)
    globals()["_env_patch"] = patcher


def tearDownModule():
    _env_patch.stop()


def parsed(message, context=None):
    intent = parse_question(message, context)
    return intent, intent.filters


# ---- 1. a generation is not a price floor ---------------------------------------------------------

class GenerationAndPriceTests(unittest.TestCase):
    def test_generation_is_a_year_range_and_the_amount_is_the_ceiling(self):  # row 34 and the extra checks
        for message, years, budget in (("Golf 7 do 18k", (2012, 2020), 18000),
                                       ("Golf 6 do 12k", (2008, 2013), 12000),
                                       ("Golf 8 do 25k", (2019, None), 25000),
                                       ("Passat B8 do 120 hiljada", (2014, None), 120000),
                                       ("golf 7 do 18k KM", (2012, 2020), 18000),
                                       ("Octavia 3 do 15000 KM", (2012, 2020), 15000),
                                       ("golff 7 do 18k", (2012, 2020), 18000)):
            intent, filters = parsed(message)
            self.assertEqual("find", intent.kind, message)
            self.assertEqual((None, budget), (filters.price_min, filters.budget), message)
            self.assertEqual(years, (filters.year_min, filters.year_max), message)
            self.assertEqual(message.split()[0].lower().replace("golff", "golf"), filters.search, message)

    def test_a_model_token_with_a_digit_is_not_the_start_of_a_range(self):
        for message, search in (("audi a3 do 10k", "audi a3"), ("x5 do 20k", "x5"), ("bmw x3 do 25000 KM", "bmw x3")):
            _, filters = parsed(message)
            self.assertEqual((None, search), (filters.price_min, filters.search), message)
            self.assertEqual(int(re.search(r"do (\d+)", message).group(1)) * (1000 if "k" in message else 1),
                             filters.budget, message)

    def test_generation_with_both_price_ends_keeps_the_range(self):
        _, filters = parsed("Golf 7 od 12 do 18 hiljada")
        self.assertEqual((12000, 18000, 2012, 2020), (filters.price_min, filters.budget, filters.year_min,
                                                      filters.year_max))

    def test_plain_ranges_without_a_generation_are_unchanged(self):
        for message, low, high in (("auta od 8 do 12 hiljada maraka", 8000, 12000),  # row 8
                                   ("Passat između 15 i 20 hiljada", 15000, 20000),  # row 6
                                   ("od 50 - 60k", 50000, 60000)):
            _, filters = parsed(message)
            self.assertEqual((low, high), (filters.price_min, filters.budget), message)

    def test_the_follow_up_carries_the_generation_years(self):  # rows 34b / 34c
        intent, filters = parsed("Golf 7 do 18k")
        context = context_from_reply(telegram_bot._filter_title(filters, "bs"))
        follow, ff = parsed("a Passat?", context)
        self.assertEqual(("find", "passat", 18000), (follow.kind, ff.search, ff.budget))
        self.assertIsNone(ff.price_min)
        self.assertEqual((2012, 2020), (ff.year_min, ff.year_max))


# ---- 2. mileage phrasing --------------------------------------------------------------------------

class MileageTests(unittest.TestCase):
    def test_hiljada_kilometara_is_mileage_not_km_currency(self):  # row 18
        _, filters = parsed("Passat B8 do 120 hiljada kilometara")
        self.assertEqual(("passat", 120000, None, None, 2014), (filters.search, filters.mileage_max, filters.budget,
                                                                 filters.price_min, filters.year_min))
        for message in ("Golf do 150000 kilometara", "golf do 150 hiljada km", "golf do 150000km"):
            _, filters = parsed(message)
            self.assertEqual((150000, None, "golf"), (filters.mileage_max, filters.budget, filters.search), message)

    def test_an_uppercase_KM_is_still_a_price(self):
        _, filters = parsed("golf do 15000 KM")
        self.assertEqual((15000, None), (filters.budget, filters.mileage_max))

    def test_malo_kilometara_is_a_soft_preference_not_search_text(self):  # row 19
        intent, filters = parsed("auto sa malo kilometara do 20k, manje od 80000 km")
        self.assertEqual("find", intent.kind)
        self.assertEqual(("", 20000, 80000, None, True), (filters.search, filters.budget, filters.mileage_max,
                                                          filters.mileage_min, filters.low_km))
        intent, filters = parsed("Golf 7 sa malo kilometara")
        self.assertEqual(("golf", True, None), (filters.search, filters.low_km, filters.mileage_max))
        _, filters = parsed("golf low mileage under 10k")
        self.assertEqual(("golf", True, 10000), (filters.search, filters.low_km, filters.budget))

    def test_preko_and_vise_od_km_is_a_minimum(self):  # row 20 and the extra checks
        for message, search, minimum in (("BMW X3 preko 200.000 km", "bmw x3", 200000),
                                         ("BMW X3 sa više od 200000 km", "bmw x3", 200000),
                                         ("Octavia sa preko 300 hiljada km", "octavia", 300000),
                                         ("golf od 100000 km", "golf", 100000),
                                         ("golf over 150000 km", "golf", 150000),
                                         ("golf preko 200 hiljada kilometara", "golf", 200000)):
            intent, filters = parsed(message)
            self.assertEqual(("find", search, minimum, None, None), (intent.kind, filters.search, filters.mileage_min,
                                                                    filters.mileage_max, filters.budget), message)
            self.assertTrue(filters.numeric(), message)

    def test_a_maximum_and_a_minimum_together(self):
        _, filters = parsed("golf preko 100000 km do 200000 km")
        self.assertEqual((100000, 200000), (filters.mileage_min, filters.mileage_max))
        _, filters = parsed("golf 100 do 200 hiljada kilometara")
        self.assertEqual(("golf", 100000, 200000, None), (filters.search, filters.mileage_min, filters.mileage_max,
                                                          filters.budget))

    def test_manje_od_and_do_stay_a_maximum(self):
        for message in ("golf manje od 80000 km", "golf do 80000 km", "golf ispod 80000 km"):
            _, filters = parsed(message)
            self.assertEqual((None, 80000), (filters.mileage_min, filters.mileage_max), message)

    def test_a_year_after_a_km_maximum_is_still_a_year(self):
        _, filters = parsed("golf do 150000 km od 2015")
        self.assertEqual((150000, 2015, None), (filters.mileage_max, filters.year_min, filters.mileage_min))

    def test_the_minimum_reads_back_from_the_title_and_the_buttons(self):
        for filters in (Filters("bmw x3", mileage_min=200000), Filters("golf", mileage_min=100000, mileage_max=200000),
                        Filters("golf", low_km=True, budget=20000), Filters("golf", mileage_max=80000, low_km=True)):
            for lang in ("bs", "en"):
                title = telegram_bot._filter_title(filters, lang)
                self.assertEqual(filters, context_from_reply(title).filters, title)
            data = telegram_bot.refine_callback(filters)
            self.assertEqual(filters, telegram_bot.parse_refine_callback(data), data)
            liquid = telegram_bot.liquidity_callback(filters)
            self.assertEqual(filters, telegram_bot.parse_liquidity_callback(liquid), liquid)
        self.assertEqual("Viđeni oglasi · bmw x3 · od 200.000 km",
                         telegram_bot._filter_title(Filters("bmw x3", mileage_min=200000), "bs"))
        self.assertEqual("Observed listings · golf · low mileage",
                         telegram_bot._filter_title(Filters("golf", low_km=True), "en"))
        for bad in ("f:0:0:300000-100000:0:golf", "f:0:0:5-100000:0:golf"):
            self.assertIsNone(telegram_bot.parse_refine_callback(bad), bad)

    def test_old_buttons_without_a_minimum_still_work(self):
        self.assertEqual(Filters("golf", 15000, 2018, 150000), telegram_bot.parse_refine_callback(
            "f:15000:2018:150000:0:golf"))

    def test_merging_a_follow_up_keeps_a_consistent_minimum(self):
        base = Filters("golf", mileage_min=100000)
        self.assertEqual((100000, 150000), (lambda m: (m.mileage_min, m.mileage_max))(
            merge_filters(base, Filters(mileage_max=150000))))
        self.assertEqual((None, 50000), (lambda m: (m.mileage_min, m.mileage_max))(
            merge_filters(base, Filters(mileage_max=50000))))
        self.assertTrue(merge_filters(Filters("golf", low_km=True), Filters(budget=9000)).low_km)


# ---- 3. a price range after a year range ----------------------------------------------------------

class RangeAfterYearsTests(unittest.TestCase):
    def test_price_range_after_a_year_span(self):  # row 7
        for message, search, years, prices in (
                ("Octavia 2014-2016 od 12000 do 16000 KM", "octavia", (2014, 2016), (12000, 16000)),
                ("Passat 2014-2016 od 15000 do 20000 KM", "passat", (2014, 2016), (15000, 20000)),
                ("octavia 2014 - 2016 12000 - 16000 KM", "octavia", (2014, 2016), (12000, 16000)),
                ("Octavia od 12000 do 16000 KM 2014-2016", "octavia", (2014, 2016), (12000, 16000))):
            intent, filters = parsed(message)
            self.assertEqual(("find", search, years, prices), (intent.kind, filters.search,
                                                              (filters.year_min, filters.year_max),
                                                              (filters.price_min, filters.budget)), message)

    def test_a_year_span_alone_is_not_a_price_range(self):
        _, filters = parsed("Golf 2014-2016")
        self.assertEqual(((2014, 2016), None, None), ((filters.year_min, filters.year_max), filters.budget,
                                                     filters.price_min))

    def test_the_correction_keeps_the_floor_and_the_years(self):  # rows 7b / 7c
        _, filters = parsed("Octavia 2014-2016 od 12000 do 16000 KM")
        title = telegram_bot._filter_title(filters, "bs")
        self.assertEqual("Viđeni oglasi · octavia · 12.000–16.000 KM · 2014–2016", title)
        context = context_from_reply(title)
        for message in ("a ne, do 14000", "a ne, do 14000 KM"):
            follow, ff = parsed(message, context)
            self.assertEqual(("find", "octavia", 12000, 14000, 2014, 2016),
                             (follow.kind, ff.search, ff.price_min, ff.budget, ff.year_min, ff.year_max), message)
        follow, ff = parsed("mislio sam 2017", context)
        self.assertEqual((2017, 12000, 16000), (ff.year_min, ff.price_min, ff.budget))


# ---- 4. price intent ------------------------------------------------------------------------------

class PriceIntentTests(unittest.TestCase):
    def test_cijena_kolko_je_and_english_cost_are_price_questions(self):  # rows 2, 3, 25
        for message, search, year in (("Audi A3 2016 cijena", "audi a3", 2016),
                                      ("what does a 2018 BMW 320d cost?", "bmw 320d", 2018),
                                      ("bmw 320 2014 kolko je", "bmw 320", 2014),
                                      ("koliko je Golf 2015", "golf", 2015),
                                      ("how much is a 2017 Octavia", "octavia", 2017),
                                      ("cijena Passat 2016", "passat", 2016),
                                      ("what is the price of a 2019 Tucson", "tucson", 2019)):
            intent, filters = parsed(message)
            self.assertEqual("price_check", intent.kind, message)
            self.assertEqual(search, filters.search, message)
            # a bare year in a price question is that model year (+-1), never "from that year"
            self.assertEqual((year, year - 1, year + 1), (intent.year, filters.year_min, filters.year_max), message)

    def test_a_price_question_without_a_year_is_a_model_overview(self):
        for message in ("koliko košta Golf", "Golf cijena", "how much is a Golf"):
            intent, filters = parsed(message)
            self.assertEqual(("model_stats", "golf"), (intent.kind, filters.search), message)

    def test_a_generation_in_a_price_question_uses_its_years(self):
        intent, filters = parsed("Golf 7 cijena")
        self.assertEqual(("price_check", (2012, 2020)), (intent.kind, (filters.year_min, filters.year_max)))

    def test_an_amount_keeps_it_a_search(self):
        for message in ("Golf do 15000 cijena", "Golf 2015 do 9000 KM"):
            self.assertEqual("find", parse_question(message).kind, message)

    def test_the_old_price_questions_still_work(self):  # row 1, 36
        intent, filters = parsed("koliko košta Golf 6 2010?")
        self.assertEqual(("price_check", (2009, 2011)), (intent.kind, (filters.year_min, filters.year_max)))
        intent, filters = parsed("is a 2016 A4 at 17.500 a good deal?")
        self.assertEqual(("price_check", 17500), (intent.kind, intent.price))

    def test_plain_year_searches_are_unchanged(self):  # rows 4, 5
        for message, search in (("Renault Clio 2019", "renault clio"), ("Opel Astra 2012 do 9000 KM", "opel astra")):
            intent, filters = parsed(message)
            self.assertEqual(("find", search, None), (intent.kind, filters.search, filters.year_max), message)


# ---- 5. out of scope ------------------------------------------------------------------------------

class OutOfScopeTests(unittest.TestCase):
    def test_out_of_scope_text_gets_the_scope_message(self):  # rows 29-32
        for message in ("kako da registrujem auto u BiH?", "koliki je kredit za auto na 5 godina?",
                        "daj mi recept za ćevape", "Yamaha R6 motocikl do 8000", "how do I register a car?",
                        "car loan calculator", "osiguranje za auto", "best pizza recipe"):
            intent = parse_question(message)
            self.assertEqual(("chitchat", ("scope",)), (intent.kind, intent.labels), message)

    def test_the_scope_message_replaces_model_suggestions(self):
        for lang in ("bs", "en"):
            reply = telegram_bot.chitchat_message(lang, "scope")
            self.assertTrue(reply.startswith(telegram_i18n.t(lang, "smalltalk_scope")), reply)
            self.assertNotIn("Jesi li mislio", reply)
            self.assertIn("Golf", reply)  # the examples of what the bot can do
        self.assertIn("olx.ba", telegram_i18n.t("bs", "smalltalk_scope"))

    def test_car_questions_with_bounds_are_not_out_of_scope(self):
        for message in ("Golf 7 do 18k", "golf 2015 do 9000 KM registracija", "Passat 2016 sa kreditom do 20000 KM",
                        "Suzuki Vitara", "Honda Civic do 10k"):
            self.assertNotEqual("chitchat", parse_question(message).kind, message)

    def test_scope_is_answered_without_a_database(self):
        text_value, markup = answer_intent(None, parse_question("daj mi recept za ćevape"), CONFIG, lang="bs")
        self.assertIn("olx.ba", text_value)
        self.assertNotIn("Jesi li mislio", text_value)


# ---- 6. typos and "za porodicu" -------------------------------------------------------------------

class TypoAndCompareTests(unittest.TestCase):
    def test_one_letter_typos_of_models_are_repaired(self):  # row 21
        _, filters = parsed("pasat 2015 dizl")
        self.assertEqual(("passat", 2015, "diesel"), (filters.search, filters.year_min, filters.fuel))
        for typo, name in (("pasat", "passat"), ("peugot", "peugeot"), ("mercedez", "mercedes"),
                           ("hyundi", "hyundai"), ("corola", "corolla")):
            self.assertEqual(name, repair_typo(typo))

    def test_real_names_and_inflections_are_left_alone(self):
        for word in ("passat", "octavie", "passati", "golfovi", "pouzdano", "nesto", "tesla", "terrano", "vitara"):
            self.assertEqual(word, repair_typo(word), word)

    def test_benca_is_petrol_slang(self):  # row 23: "benca" used to be dropped without a word
        _, filters = parsed("vw polo za grad do 11k, benca")
        self.assertEqual(("polo", "petrol", 11000), (filters.search.split()[0], filters.fuel, filters.budget))
        self.assertNotIn("benca", filters.search)

    def test_za_porodicu_in_a_compare_is_kept(self):  # rows 9, 10
        for message, labels in (("Škoda Octavia vs VW Golf za porodicu", ("škoda octavia", "vw golf")),
                                ("šta je bolje za porodicu: Passat ili Superb?", ("passat", "superb")),
                                ("Octavia ili Passat za porodicu?", ("octavia", "passat"))):
            intent, filters = parsed(message)
            self.assertEqual(("compare", labels, True), (intent.kind, intent.labels, filters.family), message)
        intent, filters = parsed("Octavia vs Golf")
        self.assertFalse(filters.family)

    def test_two_makes_with_ili_are_a_compare(self):  # row 12
        intent, filters = parsed("Kia Sportage ili Hyundai Tucson 2019")
        self.assertEqual(("compare", ("kia sportage", "hyundai tucson"), 2019),
                         (intent.kind, intent.labels, filters.year_min))
        intent, _ = parsed("Kia Sportage ili Hyundai Tucson")
        self.assertEqual("compare", intent.kind)
        self.assertNotEqual("compare", parse_question("golf ili nesto").kind)
        self.assertNotEqual("compare", parse_question("dizel ili benzin Golf").kind)


class SuggestionNoiseTests(unittest.TestCase):
    LABELS = [("bmw x5", "BMW X5", 10), ("audi s5", "Audi S5", 5), ("renault scenic", "Renault Scenic", 8),
              ("kia stonic", "Kia Stonic", 4), ("volkswagen golf", "Volkswagen Golf", 40)]

    def test_a_digit_or_a_weak_resemblance_is_not_a_suggestion(self):  # rows 28, 30
        for search in ("kredit 5", "nice", "5"):
            self.assertEqual((None, []), telegram_bot.resolve_search(self.LABELS, search), search)

    def test_a_real_typo_still_gets_its_suggestion(self):
        self.assertEqual((None, ["Volkswagen Golf"]), telegram_bot.resolve_search(self.LABELS, "gulff"))


# ---- the answers, against a throwaway database ----------------------------------------------------

def _naive_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@unittest.skipUnless(os.getenv("DEALER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class SweepAnswerTests(unittest.TestCase):
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
        """rows: (id, make, model, year, price, mileage[, fuel])"""
        with self.engine.begin() as c:
            for lid, make, model, year, price, mileage, *fuel in rows:
                c.execute(text("""INSERT INTO car_listings(listing_id,make,model,year,price,price_status,mileage,
                    fuel_type,source,collection_method,last_verified_at,first_seen_at,is_active,
                    consecutive_missing_runs) VALUES (:id,:make,:model,:year,:price,'priced',:mileage,:fuel,
                    'olx.ba','olx_api',:last,:first,true,0)"""),
                    {"id": f"__qa_s_{lid}", "make": make, "model": model, "year": year, "price": price,
                     "mileage": mileage, "fuel": fuel[0] if fuel else "diesel",
                     "first": self.now - timedelta(days=first_days), "last": self.now - timedelta(days=last_days)})

    def answer(self, message, context=None, lang="bs"):
        intent = parse_question(message, context)
        text_value, markup = answer_intent(self.engine, intent, CONFIG, lang=lang)
        return telegram_bot.html_to_text(text_value, keep_links=True), markup

    def ids(self, reply):
        return set(re.findall(r"artikal/__qa_s_(\w+)", reply))

    def test_golf_7_do_18k_lists_only_golf_7_years_under_the_ceiling(self):  # row 34
        self.seed([("g7a", "Volkswagen", "Golf", 2015, 16500, 150000), ("g7b", "Volkswagen", "Golf", 2013, 12000, 190000),
                   ("g5", "Volkswagen", "Golf", 2006, 5000, 250000),  # an old Golf the 7k floor used to let in
                   ("g7x", "Volkswagen", "Golf", 2016, 21000, 110000), ("g8", "Volkswagen", "Golf", 2021, 17900, 60000)])
        reply, _ = self.answer("Golf 7 do 18k")
        self.assertTrue(reply.startswith("Viđeni oglasi · golf · do 18.000 KM · 2012–2020\n"), reply)
        self.assertEqual({"g7a", "g7b"}, self.ids(reply))
        self.assertNotIn("7.000", reply)

    def test_octavia_range_after_a_year_span_and_the_correction(self):  # rows 7, 7b
        self.seed([("o1", "Škoda", "Octavia", 2015, 13000, 190000), ("o2", "Škoda", "Octavia", 2016, 15500, 150000),
                   ("o3", "Škoda", "Octavia", 2014, 11000, 220000), ("o4", "Škoda", "Octavia", 2017, 14000, 120000)])
        reply, markup = self.answer("Octavia 2014-2016 od 12000 do 16000 KM")
        self.assertTrue(reply.startswith("Viđeni oglasi · octavia · 12.000–16.000 KM · 2014–2016\n"), reply)
        self.assertEqual({"o1", "o2"}, self.ids(reply))
        self.assertNotIn("Nisam prepoznao", reply)
        follow, _ = self.answer("a ne, do 14000", context_from_reply(reply))
        self.assertTrue(follow.startswith("Viđeni oglasi · octavia · 12.000–14.000 KM · 2014–2016\n"), follow)
        self.assertEqual({"o1"}, self.ids(follow))
        self.assertNotIn("Nisam prepoznao", follow)

    def test_hiljada_kilometara_and_preko_filter_on_mileage(self):  # rows 18, 20
        self.seed([("p1", "Volkswagen", "Passat", 2016, 20000, 99000), ("p2", "Volkswagen", "Passat", 2018, 25000, 130000),
                   ("p3", "Volkswagen", "Passat", 2017, 21000, 235000), ("p4", "Volkswagen", "Passat", 2012, 9000, 90000)])
        reply, _ = self.answer("Passat B8 do 120 hiljada kilometara")
        self.assertEqual({"p1", "p4"} - {"p4"}, self.ids(reply))  # p4 is a 2012 (older than the B8's 2014)
        self.assertIn("do 120.000 km", reply)
        self.assertNotIn("8.000", reply)
        reply, _ = self.answer("Passat preko 200.000 km")
        self.assertEqual({"p3"}, self.ids(reply))
        self.assertIn("od 200.000 km", reply)
        reply, _ = self.answer("Passat preko 100 hiljada km do 200 hiljada km")
        self.assertEqual({"p2"}, self.ids(reply))

    def test_malo_kilometara_does_not_kill_the_search_and_lowest_km_comes_first(self):  # row 19
        self.seed([("a1", "Volkswagen", "Polo", 2012, 9000, 70000), ("a2", "Volkswagen", "Polo", 2014, 9500, 40000),
                   ("a3", "Škoda", "Fabia", 2013, 8000, 55000), ("a4", "Opel", "Corsa", 2013, 7000, 150000)])
        reply, markup = self.answer("auto sa malo kilometara do 20k, manje od 80000 km")
        self.assertNotIn("Nisam prepoznao", reply)
        self.assertNotIn("Jesi li mislio", reply)
        self.assertTrue(reply.startswith("Viđeni oglasi · malo kilometara · do 20.000 KM · do 80.000 km\n"), reply)
        order = re.findall(r"artikal/__qa_s_(\w+)", reply)
        self.assertEqual(["a2", "a3", "a1"], order)  # lowest mileage first; the 150.000 km car is excluded
        self.assertTrue(markup.filters.low_km)  # the next-page and refine buttons keep the preference

    def test_low_mileage_order_ignores_placeholder_mileages(self):
        self.seed([("m1", "Mazda", "121", 2013, 9999, 261), ("m2", "Volkswagen", "Polo", 2014, 9500, 40000),
                   ("m3", "Škoda", "Fabia", 2013, 8000, 55000)])
        reply, _ = self.answer("auto sa malo kilometara do 20k")
        self.assertEqual(["m2", "m3", "m1"], re.findall(r"artikal/__qa_s_(\w+)", reply))

    def test_price_questions_get_price_statistics(self):  # rows 2, 3, 25
        self.seed([(f"b{n}", "BMW", "320d", 2018, 20000 + n * 500, 150000 + n * 1000) for n in range(6)]
                  + [("b24", "BMW", "320d", 2024, 36200, 20000)])
        for message in ("what does a 2018 BMW 320d cost?", "BMW 320d 2018 cijena", "bmw 320d 2018 kolko je"):
            for lang in ("en", "bs"):
                reply, _ = self.answer(message, lang=lang)
                self.assertNotIn("__qa_s_b24", reply, message)
                self.assertRegex(reply, r"(?i)median|medijan")
                self.assertIn("2017–2019", reply, message)

    def test_the_typo_pasat_finds_the_passats(self):  # row 21
        self.seed([("pa1", "Volkswagen", "Passat", 2016, 17000, 180000), ("pa2", "Volkswagen", "Passat", 2018, 21000, 120000)])
        reply, _ = self.answer("pasat 2015 dizl")
        self.assertEqual({"pa1", "pa2"}, self.ids(reply))
        self.assertTrue(reply.startswith("Viđeni oglasi · passat · dizel · od 2015\n"), reply)

    def test_compare_for_a_family_names_the_family_list(self):  # rows 9, 10, 12
        octavia = [(f"co{y}", "Škoda", "Octavia", y, 1000 * (y - 2000), 150000) for y in range(2012, 2020)]
        golf = [(f"cg{y}", "Volkswagen", "Golf", y, 1000 * (y - 1999), 150000) for y in range(2012, 2020)]
        self.seed(octavia + golf)
        reply, _ = self.answer("Škoda Octavia vs VW Golf za porodicu")
        self.assertIn("Za porodicu: samo škoda octavia je na mojoj kratkoj listi prostranijih modela.", reply)
        self.assertIn("ne koji je prostraniji", reply)
        reply, _ = self.answer("Octavia vs Golf")
        self.assertNotIn("Za porodicu", reply)
        english, _ = self.answer("Octavia or Golf for a family?", lang="en")
        self.assertIn("For a family:", english)

    def test_kia_or_hyundai_is_a_compare(self):  # row 12
        self.seed([(f"k{n}", "Kia", "Sportage", 2019, 25000 + n * 500, 90000) for n in range(4)]
                  + [(f"h{n}", "Hyundai", "Tucson", 2019, 24000 + n * 500, 95000) for n in range(4)])
        reply, _ = self.answer("Kia Sportage ili Hyundai Tucson 2019")
        self.assertIn("Upoređena godišta: 2019", reply)
        self.assertNotIn("Nisam prepoznao", reply)


if __name__ == "__main__":
    unittest.main()
