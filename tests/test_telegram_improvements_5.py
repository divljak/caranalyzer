"""Improvements #5 for the Garaža bot: the weak rows left in docs/qa/bot-usecase-sweep-2026-11-16.md
(liquidity dead end, "pokaži još", average mileage, unknown models, engine and trim tokens, vague questions,
the stray "Mercedes-Benz CE"), plus two parser bugs the PR #42 re-check found.

The parser tests need nothing; the database tests need a throwaway PostgreSQL (DEALER_TEST_DATABASE_URL).
None of these paths needs Gemini.
"""

from datetime import datetime, timedelta, timezone
import os
import re
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, text

import telegram_bot
import telegram_i18n
from telegram_bot import TelegramConfig, answer_intent, process_update
from telegram_intent import (
    Filters, Intent, context_from_reply, hint_patterns, looks_like_followup, parse_question, split_trim,
)

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


# ---- two parser bugs from the PR #42 re-check -----------------------------------------------------

class PendingParserBugTests(unittest.TestCase):
    def test_ne_manje_od_is_a_minimum(self):
        for message, km in (("Golf ne manje od 100000 km", 100_000), ("golf nikako manje od 100000 km", 100_000),
                            ("golf not less than 200000 km", 200_000), ("golf no less than 150 hiljada kilometara", 150_000)):
            _, filters = parsed(message)
            self.assertEqual((km, None), (filters.mileage_min, filters.mileage_max), message)
            self.assertEqual("golf", filters.search, message)

    def test_ne_manje_od_is_a_price_floor_too(self):
        _, filters = parsed("golf ne manje od 15000 KM")
        self.assertEqual((15_000, None), (filters.price_min, filters.budget))

    def test_plain_manje_od_and_ne_vise_od_stay_a_maximum(self):
        for message in ("golf manje od 100000 km", "golf ne više od 100000 km", "golf do 100000 km"):
            _, filters = parsed(message)
            self.assertEqual((None, 100_000), (filters.mileage_min, filters.mileage_max), message)

    def test_nikako_is_not_part_of_the_model_search(self):
        for message in ("Golf nikako preko 150000 km", "Golf nikako više od 150000 km", "golf never over 150000 km"):
            _, filters = parsed(message)
            self.assertEqual("golf", filters.search, message)
            self.assertEqual(150_000, filters.mileage_max, message)  # "preko" after a negation is still a ceiling


# ---- Ogi's calls: Golf 2015 is exact (17 Nov 2026); compare thresholds are untouched --------------

class PendingDecisionsTests(unittest.TestCase):
    def test_golf_2015_is_exactly_2015(self):
        _, filters = parsed("Golf 2015")
        self.assertEqual(("golf", 2015, 2015), (filters.search, filters.year_min, filters.year_max))

    def test_golf_20_is_asked_about_not_guessed(self):
        intent = parse_question("golf 20")
        self.assertEqual(("clarify", "short_number", 20), (intent.kind, intent.note, intent.offset))
        self.assertEqual(("golf", None, None), (intent.filters.search, intent.filters.year_min,
                                                intent.filters.year_max))

    def test_compare_thresholds_are_unchanged(self):
        self.assertEqual((.1, .9), telegram_bot.COMPARE_SPREAD)
        self.assertEqual(10, telegram_bot.COMPARE_SPREAD_MIN)
        self.assertEqual(3, telegram_bot.COMPARE_BAND_MIN)


# ---- "pokaži još" ---------------------------------------------------------------------------------

class MoreIntentTests(unittest.TestCase):
    FIND = Intent("find", Filters("golf", budget=15_000), offset=0, page_total=12)

    def test_more_is_the_next_page(self):  # row 34d
        for message in ("pokaži još", "Pokaži još", "još", "jos", "daj mi još", "dalje", "show more", "more",
                        "next page", "još rezultata", "pokaži više", "sljedeća stranica"):
            intent = parse_question(message, self.FIND)
            self.assertEqual(("find", 5, "more"), (intent.kind, intent.offset, intent.note), message)
            self.assertEqual(self.FIND.filters, intent.filters, message)

    def test_the_second_page_leads_to_the_third(self):
        second = Intent("find", Filters("golf"), offset=5, page_total=12)
        self.assertEqual(10, parse_question("pokaži još", second).offset)

    def test_the_last_page_says_there_is_no_more(self):
        last = Intent("find", Filters("golf"), offset=10, page_total=12)
        intent = parse_question("pokaži još", last)
        self.assertEqual(("no_more", 12), (intent.kind, intent.page_total))
        one_page = Intent("find", Filters("golf"), offset=0, page_total=1)  # the sweep case: a single listing
        self.assertEqual("no_more", parse_question("pokaži još", one_page).kind)

    def test_without_a_search_to_continue_it_asks_what_to_search(self):
        for context in (None, Intent("liquidity", Filters()), Intent("trends", days=7)):
            intent = parse_question("pokaži još", context)
            self.assertEqual(("clarify", ("more",)), (intent.kind, intent.labels), context)

    def test_a_search_that_merely_contains_the_word_is_not_a_page_request(self):
        for message in ("golf još jeftiniji do 10000 KM", "pokaži više golfova", "pokaži još jedan golf do 12000 KM"):
            self.assertNotEqual("more", parse_question(message, self.FIND).note, message)

    def test_more_counts_as_a_follow_up_for_the_private_chat_memory(self):
        self.assertTrue(looks_like_followup("pokaži još"))
        self.assertTrue(looks_like_followup("show more"))

    def test_the_page_is_read_back_from_a_reply_to_an_answer(self):
        reply = ("Viđeni oglasi · golf · do 15.000 KM\n"
                 "Prikazujem 6–10 od 26 oglasa viđenih u zadnjih 14 dana · prvo nedavno viđeni\n\n• ...")
        context = context_from_reply(reply)
        self.assertEqual(("find", 5, 26), (context.kind, context.offset, context.page_total))
        self.assertEqual(10, parse_question("pokaži još", context).offset)
        english = "Observed listings · golf\nShowing 26 of 26 listings seen in the last 14 days"
        self.assertEqual("no_more", parse_question("more", context_from_reply(english)).kind)


# ---- average mileage ------------------------------------------------------------------------------

class MileageStatsIntentTests(unittest.TestCase):
    def test_how_many_km_on_average_is_a_mileage_question(self):  # row 38
        for message in ("Mercedes E klasa 2012 dizel koliko km ima prosječno?", "average mileage of an e class 2012 diesel",
                        "prosječna kilometraža Mercedes E klasa 2012 dizel"):
            intent, filters = parsed(message)
            self.assertEqual("mileage_stats", intent.kind, message)
            self.assertEqual((2011, 2013, "diesel"), (filters.year_min, filters.year_max, filters.fuel), message)
            self.assertIn("klasa", filters.search + " class", message)

    def test_a_generation_and_an_open_year_keep_their_years(self):
        intent, filters = parsed("koliko kilometara ima prosječno Golf 7")
        self.assertEqual(("mileage_stats", "golf", 2012, 2020), (intent.kind, filters.search, filters.year_min,
                                                                 filters.year_max))

    def test_without_a_model_it_asks_which(self):
        intent, _ = parsed("koliko km ima prosječno?")
        self.assertEqual(("clarify", "mileage_stats"), (intent.kind, intent.note))

    def test_price_and_mileage_limits_are_not_mileage_questions(self):
        for message, kind in (("koliko košta Golf 2015 prosječno", "price_check"),
                              ("prosječna cijena golf 2015", "price_check"),
                              ("golf do 150000 km", "find"),
                              ("golf 2015 sa malo kilometara", "find"),
                              ("Golf do 15000 KM", "find")):
            self.assertEqual(kind, parsed(message)[0].kind, message)


# ---- vague questions ------------------------------------------------------------------------------

class VagueQuestionTests(unittest.TestCase):
    def test_a_wish_without_a_car_asks_for_a_budget_or_a_use(self):  # rows 26-28
        for message in ("nešto jeftino i pouzdano", "koji auto je najbolji za početnike?", "show me something nice",
                        "need a reliable car"):
            intent, _ = parsed(message)
            self.assertEqual(("clarify", ("vague",)), (intent.kind, intent.labels), message)

    def test_the_answer_asks_for_a_budget_and_a_use_with_budget_buttons(self):
        for lang, word in (("bs", "budžet"), ("en", "budget")):
            reply, markup = answer_intent(None, parse_question("show me something nice"), CONFIG, lang=lang)
            self.assertIn(word, reply)
            self.assertNotIn("Jesi li mislio", reply)
            self.assertNotIn("Did you mean", reply)
            self.assertTrue(markup, lang)

    def test_wishes_with_a_car_or_a_filter_are_still_searches(self):
        for message, kind in (("reliable golf", "find"), ("best golf do 10k", "find"), ("nešto jeftino do 15000 KM", "find"),
                              ("jeftin dizel", "find"), ("šta ima novo danas?", "latest"), ("nešto novo", "latest")):
            self.assertEqual(kind, parsed(message)[0].kind, message)

    def test_the_stray_mercedes_benz_ce_is_never_suggested(self):  # row 28
        labels = [("mercedes benz ce", "Mercedes-Benz CE", 3), ("renault scenic", "Renault Scenic", 40),
                  ("kia stonic", "Kia Stonic", 30), ("volkswagen golf", "Volkswagen Golf", 100)]
        resolved, candidates = telegram_bot.resolve_search(labels, "nice")
        self.assertIsNone(resolved)
        self.assertNotIn("Mercedes-Benz CE", candidates)

    def test_a_real_typo_still_gets_its_suggestion(self):
        labels = [("volkswagen passat", "Volkswagen Passat", 80), ("mercedes benz ce", "Mercedes-Benz CE", 3)]
        self.assertEqual("passat", telegram_bot.resolve_search(labels, "pasat")[0].split()[-1])


# ---- engine and trim tokens -----------------------------------------------------------------------

class TrimTokenTests(unittest.TestCase):
    def test_engine_size_and_trim_are_split_from_the_model(self):  # row 24
        self.assertEqual(("audi a6", ("3.0", "quattro")), split_trim("audi a6 3.0 quattro"))
        self.assertEqual(("golf", ("2.0",)), split_trim("golf 2.0"))
        self.assertEqual(("bmw 320d", ("xdrive",)), split_trim("bmw 320d xdrive"))

    def test_nothing_is_split_when_no_model_would_remain(self):
        self.assertEqual(("quattro", ()), split_trim("quattro"))
        self.assertEqual(("golf 7", ()), split_trim("golf 7"))
        self.assertEqual(("320d", ()), split_trim("320d"))

    def test_the_parser_keeps_the_tokens_in_the_search_text(self):
        _, filters = parsed("audi a6 3.0 tdi quattro")
        self.assertEqual(("audi a6 3.0 quattro", "diesel"), (filters.search, filters.fuel))
        _, filters = parsed("audi a6 3,0 tdi quattro")  # a comma is how people write it here
        self.assertEqual("audi a6 3.0 quattro", filters.search)

    def test_the_hint_regexes_match_whole_tokens_only(self):
        engine, trim = hint_patterns(("3.0", "quattro"))
        trim = trim.replace(r"\m", r"\b").replace(r"\M", r"\b")  # PostgreSQL word edges, as Python writes them
        self.assertTrue(re.search(engine, "audi a6 3.0 tdi", re.I))
        self.assertTrue(re.search(engine, "motor 3,0 tdi", re.I))
        self.assertFalse(re.search(engine, "13.05", re.I))
        self.assertTrue(re.search(trim, "A6 Quattro S-line", re.I))
        self.assertFalse(re.search(trim, "quattroporte", re.I))


class WordingTests(unittest.TestCase):
    def test_every_new_string_exists_in_both_languages(self):
        en, bs = telegram_i18n.STRINGS["en"], telegram_i18n.STRINGS["bs"]
        self.assertEqual(set(en), set(bs))
        for key in ("liquidity_proxy", "no_more_last", "clarify_vague", "ms_median", "hint_found", "assumed_detail"):
            self.assertIn(key, en)


# ---- answers from a database ----------------------------------------------------------------------

def _naive_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@unittest.skipUnless(os.getenv("DEALER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class Improvements5AnswerTests(unittest.TestCase):
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
        telegram_bot._SCOPES.clear()
        telegram_bot._BUDGETS.clear()  # a budget remembered by another test must not leak in
        telegram_bot._HISTORY.clear()

    def cleanup(self):
        with self.engine.begin() as c:
            c.execute(text("DELETE FROM car_listings WHERE listing_id LIKE '__qa_i5_%'"))

    def seed(self, rows, *, first_days=20, last_days=1):
        """rows: (id, make, model, year, price, mileage[, description])"""
        with self.engine.begin() as c:
            for lid, make, model, year, price, mileage, *description in rows:
                c.execute(text("""INSERT INTO car_listings(listing_id,make,model,year,price,price_status,mileage,
                    fuel_type,description,source,collection_method,last_verified_at,first_seen_at,is_active,
                    consecutive_missing_runs) VALUES (:id,:make,:model,:year,:price,'priced',:mileage,'diesel',:desc,
                    'olx.ba','olx_api',:last,:first,true,0)"""),
                    {"id": f"__qa_i5_{lid}", "make": make, "model": model, "year": year, "price": price,
                     "mileage": mileage, "desc": description[0] if description else None,
                     "first": self.now - timedelta(days=first_days), "last": self.now - timedelta(days=last_days)})

    def answer(self, message, context=None, lang="bs"):
        intent = parse_question(message, context)
        text_value, markup = answer_intent(self.engine, intent, CONFIG, lang=lang)
        return telegram_bot.html_to_text(text_value, keep_links=True), markup

    def chat(self, message, uid):
        """One private-chat turn through process_update; returns the text that was sent."""
        call = Mock(return_value={"message_id": 1})
        update = {"update_id": uid, "message": {"chat": {"id": 123, "type": "private"}, "from": {"id": 123},
                                                "text": message}}
        with patch("telegram_log.record"):
            process_update(update, self.engine, CONFIG, call=call, llm=None, phraser=None)
        sent = [c.args[2]["text"] for c in call.call_args_list if c.args[1] in {"sendMessage", "editMessageText"}]
        return telegram_bot.html_to_text(sent[-1], keep_links=True)

    def ids(self, reply):
        return set(re.findall(r"artikal/__qa_i5_(\w+)", reply))

    # -- pagination (row 34d)

    def test_pokazi_jos_pages_through_the_results_and_then_says_there_is_no_more(self):
        self.seed([(f"g{i}", "Volkswagen", "Golf", 2010 + i, 10_000 + 100 * i, 150_000) for i in range(7)])
        first = self.chat("golf", 1)
        second = self.chat("pokaži još", 2)
        third = self.chat("pokaži još", 3)
        self.assertEqual(5, len(self.ids(first)))
        self.assertEqual(2, len(self.ids(second)))
        self.assertFalse(self.ids(first) & self.ids(second))
        self.assertIn("Prikazujem 6–7 od 7", second)
        self.assertIn("To je sve što imam za ovu pretragu", third)
        self.assertNotIn("ne mogu povezati", third)

    def test_a_single_listing_gets_no_more_not_unsupported(self):  # the sweep case
        self.seed([("only", "Volkswagen", "Passat", 2016, 16_000, 150_000)])
        self.assertEqual(1, len(self.ids(self.chat("passat do 18k", 10))))
        reply = self.chat("pokaži još", 11)
        self.assertIn("To je sve što imam", reply)
        self.assertNotIn("ne mogu povezati", reply)

    def test_more_without_an_earlier_search_asks_what_to_search(self):
        reply = self.chat("pokaži još", 20)
        self.assertIn("Nemam prethodnu pretragu", reply)

    # -- liquidity (rows 35, 35b)

    def test_liquidity_offers_the_most_listed_models_when_it_cannot_rank(self):
        # every ad vanished: that is collector churn, so the ranking is refused, but the answer is not a dead end
        rows = [(f"g{i}", "Volkswagen", "Golf", 2014, 11_000 + 100 * i, 160_000) for i in range(6)]
        rows += [(f"p{i}", "Volkswagen", "Passat", 2015, 14_000 + 100 * i, 170_000) for i in range(3)]
        self.seed(rows, first_days=20, last_days=10)
        reply, markup = self.answer("koji se najbrže prodaje?")
        self.assertIn("ne mogu poredati šta se najbrže prodaje", reply)
        self.assertIn("modeli s najviše oglasa", reply)
        self.assertIn("1. Volkswagen Golf: 6 oglasa", reply)
        self.assertIn("2. Volkswagen Passat: 3 oglasa", reply)
        self.assertIn("ne brzina prodaje", reply)
        self.assertTrue(markup)  # 🔎 buttons for the listed models
        self.assertEqual("🔎 Volkswagen Golf", markup.rows[0][0][0])

    def test_the_liquidity_follow_up_keeps_its_scope_in_the_proxy(self):
        rows = [(f"g{i}", "Volkswagen", "Golf", 2014, 11_000, 160_000) for i in range(4)]
        rows += [(f"a{i}", "Audi", "A6", 2014, 22_000, 160_000) for i in range(4)]
        self.seed(rows, first_days=20, last_days=10)
        first = parse_question("koji se najbrže prodaje?")
        follow = parse_question("a do 15 hiljada?", first)
        self.assertEqual(("liquidity", 15_000), (follow.kind, follow.filters.budget))
        reply, _ = answer_intent(self.engine, follow, CONFIG, lang="bs")
        reply = telegram_bot.html_to_text(reply)
        self.assertIn("Volkswagen Golf: 4 oglasa", reply)
        self.assertNotIn("Audi A6", reply)

    def test_liquidity_that_can_rank_has_no_proxy(self):
        # ads that stayed 3 to 8 days and then left, spread over the window: a real ranking
        with self.engine.begin() as c:
            for i in range(6):
                c.execute(text("""INSERT INTO car_listings(listing_id,make,model,year,price,price_status,mileage,
                    fuel_type,source,collection_method,last_verified_at,first_seen_at,is_active,
                    consecutive_missing_runs) VALUES (:id,'Skoda','Octavia',2014,9000,'priced',150000,'diesel',
                    'olx.ba','olx_api',:last,:first,true,0)"""),
                    {"id": f"__qa_i5_o{i}", "first": self.now - timedelta(days=40 + i), "last": self.now - timedelta(days=30 + i)})
            for i in range(10):  # alive ads keep the churn share below the refusal threshold
                c.execute(text("""INSERT INTO car_listings(listing_id,make,model,year,price,price_status,mileage,
                    fuel_type,source,collection_method,last_verified_at,first_seen_at,is_active,
                    consecutive_missing_runs) VALUES (:id,'Skoda','Fabia',2014,7000,'priced',150000,'diesel',
                    'olx.ba','olx_api',:last,:first,true,0)"""),
                    {"id": f"__qa_i5_f{i}", "first": self.now - timedelta(days=5), "last": self.now - timedelta(hours=3)})
        reply, _ = self.answer("koji se najbrže prodaje?")
        self.assertIn("Skoda Octavia: medijan", reply)
        self.assertNotIn("modeli s najviše oglasa", reply)

    # -- average mileage (row 38)

    def test_average_mileage_is_the_median_mileage_not_a_price(self):
        self.seed([("e1", "Mercedes-Benz", "E 220", 2012, 15_000, 250_000), ("e2", "Mercedes-Benz", "E 220", 2012, 16_000, 270_000),
                   ("e3", "Mercedes-Benz", "E 250", 2011, 14_000, 300_000), ("e4", "Mercedes-Benz", "E 200", 2013, 17_000, 200_000),
                   ("e5", "Mercedes-Benz", "E 220", 2012, 15_500, 260_000), ("e6", "Mercedes-Benz", "E 220", 2012, 15_200, 240_000),
                   ("e0", "Mercedes-Benz", "E 220", 2012, 15_100, 0),  # a placeholder reading: left out
                   ("e9", "Mercedes-Benz", "E 220", 2007, 7_000, 390_000)])  # another generation's year
        reply, markup = self.answer("Mercedes E klasa 2012 dizel koliko km ima prosječno?")
        self.assertTrue(reply.startswith("Tipična kilometraža · mercedes e klasa · dizel · 2011–2013\n"), reply)
        self.assertIn("Uporedivih oglasa s kilometražom (zadnjih 90 dana): 6", reply)
        self.assertIn("Medijan kilometraže: 255.000 km", reply)
        self.assertIn("Srednja polovina: 242.500–267.500 km", reply)
        self.assertNotIn("Medijan tražene cijene", reply)
        self.assertIn("ispod 1.000 km", reply)
        self.assertEqual("🔎 Pokaži oglase", markup.rows[0][0][0])

    def test_average_mileage_without_data_is_honest(self):
        reply, _ = self.answer("average mileage of a 2012 e class", lang="en")
        self.assertIn("No comparable listings with a recorded mileage", reply)

    # -- unknown models (row 33)

    def test_tesla_model_3_is_honest_that_the_ads_do_not_name_the_model(self):
        self.seed([("t1", "Tesla", "Model", 2021, 55_000, 82_747), ("t2", "Tesla", "Model", 2020, 49_000, 98_000)])
        stats, _ = self.answer("Tesla Model 3 cijena")
        self.assertIn("Nisam našao „tesla 3“, pa su ovi brojevi za „tesla“.", stats)
        self.assertIn("U našim oglasima se vodi kao „Tesla Model“, pa ne mogu izdvojiti „3“.", stats)
        self.assertIn("Uzorak: 2 oglasa", stats)
        self.assertNotIn("Nema odgovarajućih oglasa", stats)
        listing, _ = self.answer("Tesla Model 3")
        self.assertIn("Nisam prepoznao „tesla 3“, pa tražim „tesla“.", listing)
        self.assertIn("U našim oglasima se vodi kao „Tesla Model“", listing)
        self.assertEqual({"t1", "t2"}, self.ids(listing))

    def test_a_stray_word_is_not_explained_as_a_model_the_ads_cannot_name(self):  # row 23: "polo grad"
        self.seed([("p1", "Volkswagen", "Polo", 2008, 4_600, 144_000)])
        reply, _ = self.answer("vw polo za grad do 11k")
        # "za grad" (for the city) is filler since the 21 Nov parser round: Polo is searched directly
        self.assertTrue(reply.startswith("Viđeni oglasi · polo · do 11.000 KM"), reply)
        self.assertNotIn("Nisam prepoznao", reply)
        self.assertNotIn("ne mogu izdvojiti", reply)

    def test_a_price_check_for_a_model_that_is_not_in_the_data_says_so(self):
        self.seed([("g1", "Volkswagen", "Golf", 2015, 12_000, 150_000)])
        reply, _ = self.answer("koliko košta Lamborghini Urus 2020")
        self.assertIn("Nema uporedivih oglasa", reply)
        self.assertIn("Nisam prepoznao „lamborghini urus“ kao marku ili model, pa nema brojeva.", reply)

    # -- engine and trim tokens (row 24)

    def test_engine_and_trim_tokens_are_kept_and_the_ads_that_mention_them_come_first(self):
        self.seed([("a1", "Audi", "A6", 2014, 15_000, 250_000, "3.0 TDI quattro, servisna"),
                   ("a2", "Audi", "A6", 2013, 14_000, 240_000, "2.0 TDI, odlično"),
                   ("a3", "Audi", "A6", 2016, 21_000, 190_000, "Audi A6 3.0 tdi quattro S-line"),
                   ("a4", "Audi", "A6", 2017, 24_000, 120_000, None),
                   ("a5", "Audi", "A6", 2012, 11_000, 280_000, "3,0 TDI Quattro")])
        reply, _ = self.answer("audi a6 3.0 tdi quattro")
        self.assertTrue(reply.startswith("Viđeni oglasi · audi a6 3.0 quattro · dizel\n"), reply)
        self.assertNotIn("Nisam prepoznao", reply)
        self.assertIn("„3.0 quattro“ nije polje u našim podacima. 3 od 5 oglasa to spominje u tekstu; ti su prvi.", reply)
        order = re.findall(r"artikal/__qa_i5_(\w+)", reply)
        self.assertEqual({"a1", "a3", "a5"}, set(order[:3]))
        self.assertEqual({"a2", "a4"}, set(order[3:]))

    def test_tokens_nobody_mentions_are_said_so_and_nothing_is_filtered(self):
        self.seed([("a1", "Audi", "A6", 2014, 15_000, 250_000, "2.0 TDI"), ("a2", "Audi", "A6", 2015, 16_000, 200_000)])
        reply, _ = self.answer("audi a6 3.0 quattro")
        self.assertIn("nijedan oglas to ne spominje u tekstu, pa je ovo svaki audi a6 koji imamo", reply)
        self.assertEqual({"a1", "a2"}, self.ids(reply))

    def test_the_next_page_button_keeps_the_tokens(self):
        self.seed([(f"a{i}", "Audi", "A6", 2010 + i, 15_000, 250_000, "3.0 quattro") for i in range(7)])
        reply, markup = self.answer("audi a6 3.0 quattro")
        self.assertEqual(5, len(self.ids(reply)))
        data = [button[1] for row in telegram_bot.keyboard(CONFIG, markup, "bs")["inline_keyboard"] for button in
                [(b["text"], b["callback_data"]) for b in row]]
        self.assertTrue(any(item.endswith(":audi a6 3.0 quattro") for item in data), data)

    def test_stats_set_the_tokens_aside_with_a_note(self):
        self.seed([(f"a{i}", "Audi", "A6", 2014, 15_000 + 100 * i, 250_000) for i in range(3)])
        reply, _ = self.answer("koliko košta audi a6 3.0 quattro 2014")
        self.assertIn("„3.0 quattro“ nije polje u našim podacima, pa sam brojao sve audi a6.", reply)
        self.assertIn("Uporedivih oglasa (zadnjih 90 dana): 3", reply)

    # -- pre-existing bugs, end to end

    def test_ne_manje_od_km_lists_the_high_mileage_cars(self):
        self.seed([("h", "Volkswagen", "Golf", 2010, 6_000, 180_000), ("l", "Volkswagen", "Golf", 2016, 15_000, 60_000)])
        reply, _ = self.answer("Golf ne manje od 100000 km")
        self.assertTrue(reply.startswith("Viđeni oglasi · golf · od 100.000 km\n"), reply)
        self.assertEqual({"h"}, self.ids(reply))
        reply, _ = self.answer("Golf nikako preko 150000 km")
        self.assertTrue(reply.startswith("Viđeni oglasi · golf · do 150.000 km\n"), reply)
        self.assertEqual({"l"}, self.ids(reply))


if __name__ == "__main__":
    unittest.main()
