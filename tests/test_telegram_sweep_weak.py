"""Fixes for the five cases still WEAK after the 15 Nov 2026 sweep re-run
(docs/qa/bot-usecase-sweep-2026-11-15-rerun.md): S2, I3, E3, E4 and E9.

Every quoted message is the real sweep message (case id in a comment) or a close variant of it. The
parser tests need nothing; the database tests need a throwaway PostgreSQL (DEALER_TEST_DATABASE_URL).
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
from telegram_intent import Filters, Intent, context_from_reply, parse_question

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


def dm(text_value, uid=123):
    return {"update_id": 1, "message": {"message_id": 5, "text": text_value, "chat": {"id": uid, "type": "private"},
                                        "from": {"id": uid, "language_code": "bs"}}}


def sent_text(call):
    texts = [c.args[2].get("text") for c in call.call_args_list if c.args[1] in {"sendMessage", "editMessageText"}]
    return texts[-1]


def button_labels(markup):
    return [label for row in markup.rows for label, _ in row]


# ---- E9: a short number after a model is asked about, never guessed (Ogi, 17 Nov 2026) -------------

class ShortModelYearTests(unittest.TestCase):
    def test_golf_15_asks_what_the_number_means(self):  # E9, superseded: "golf 15" is no longer guessed as 2015
        intent = parse_question("golf 15")
        self.assertEqual(("clarify", "short_number", 15, "golf"),
                         (intent.kind, intent.note, intent.offset, intent.filters.search))
        self.assertIsNone(intent.filters.year_min)

    def test_it_needs_no_gemini(self):
        self.assertFalse(telegram_bot._needs_interpreter(parse_question("golf 15"), "golf 15"))

    def test_a_leading_zero_can_only_be_a_year(self):
        for message, search, year in (("passat 08", "passat", 2008), ("golf 00", "golf", 2000)):
            f = parse_question(message).filters
            self.assertEqual((search, year, year), (f.search, f.year_min, f.year_max), message)

    def test_other_short_numbers_are_asked_about_too(self):
        for message, search, n in (("audi a4 17 dizel", "audi a4", 17), ("Octavia 19", "octavia", 19)):
            intent = parse_question(message)
            self.assertEqual(("clarify", "short_number", n, search),
                             (intent.kind, intent.note, intent.offset, intent.filters.search), message)

    def test_generations_amounts_and_stated_years_are_not_short_numbers(self):
        self.assertEqual((2012, 2020), (parse_question("golf 7").filters.year_min,
                                        parse_question("golf 7").filters.year_max))
        self.assertEqual(("passat", 2014), (parse_question("passat b8").filters.search,
                                            parse_question("passat b8").filters.year_min))
        f = parse_question("golf 15k").filters
        self.assertEqual((15000, None), (f.budget, f.year_min))
        f = parse_question("golf 15 2017").filters  # a full year wins
        self.assertEqual(2017, f.year_min)
        for message in ("golf 31", "golf 15 godina", "golf 15 hiljada", "golf 20 kw"):
            intent = parse_question(message)
            self.assertNotEqual("short_number", intent.note, message)
            self.assertNotIn(intent.filters.year_min, (2015, 2020, 2031), message)

    def test_the_title_shows_one_year_and_reads_back(self):
        f = Filters("golf", year_min=2015, year_max=2015)
        title = telegram_bot._filter_title(f, "bs")
        self.assertEqual("Viđeni oglasi · golf · 2015", title)
        self.assertEqual(f, context_from_reply(title).filters)
        self.assertEqual(f, telegram_bot.parse_refine_callback(telegram_bot.refine_callback(f)))


# ---- E4: small talk -------------------------------------------------------------------------------

class SmallTalkTests(unittest.TestCase):
    def setUp(self):
        for store in (telegram_bot._SCOPES, telegram_bot._HISTORY, telegram_bot._LANGS, telegram_bot._ACTIVE,
                      telegram_bot._PENDING, telegram_bot._OPEN):
            store.clear()

    def test_small_talk_is_not_a_car_search(self):
        for message, topic in (("kakvo je vrijeme danas?", "weather"),  # E4
                               ("What's the weather like today?", "weather"),
                               ("ko si ti?", "about"), ("šta možeš?", "about"), ("what can you do?", "about"),
                               ("kako ste?", "how"), ("how are you?", "how"), ("zdravo bote", "greeting"),
                               ("dobro veče", "greeting"), ("ispričaj mi vic", "joke")):
            intent = parse_question(message)
            self.assertEqual(("chitchat", (topic,)), (intent.kind, intent.labels), message)

    def test_car_questions_and_long_sentences_are_left_alone(self):
        self.assertEqual("find", parse_question("Golf do 15000 KM").kind)
        self.assertEqual("find", parse_question("kako je golf 7?").kind)
        self.assertEqual("unsupported", parse_question(
            "ćao kako si danas imaš li možda vremena da mi pomogneš oko nečega").kind)

    def test_the_reply_is_short_bosnian_with_what_the_bot_can_do(self):
        text_value, markup = answer_intent(Mock(), parse_question("kakvo je vrijeme danas?"), CONFIG, lang="bs")
        self.assertTrue(text_value.startswith("Vrijeme ne pratim, samo polovna auta s olx.ba."), text_value)
        self.assertIn("Golf do 15000 KM", text_value)
        self.assertIn("dobre prilike", text_value)
        self.assertLess(len(text_value), 400)
        self.assertEqual(["🔎 Golf do 15000 KM", "💡 Šta da kupim do 30000 KM", "🆕 Šta je novo"], button_labels(markup))

    def test_without_gemini_the_whole_update_answers_it(self):  # E4, parser only
        call = Mock(return_value={"message_id": 9})
        result = process_update(dm("kakvo je vrijeme danas?"), Mock(), CONFIG, call=call, llm=None, phraser=None)
        self.assertEqual("chitchat", result["command"])
        self.assertIn("Vrijeme ne pratim", sent_text(call))
        self.assertNotIn("vrijeme“ se ne poklapa", sent_text(call))

    def test_gemini_still_gets_the_first_go_and_its_wording_wins(self):
        self.assertTrue(telegram_bot._needs_interpreter(parse_question("kakvo je vrijeme danas?"), ""))
        llm = Mock(return_value=Intent("chitchat", reply="Ne znam za vrijeme, ali znam za auta! Šta tražiš?"))
        call = Mock(return_value={"message_id": 9})
        process_update(dm("kakvo je vrijeme danas?", uid=456), Mock(), CONFIG, call=call, llm=llm, phraser=None)
        self.assertEqual("Ne znam za vrijeme, ali znam za auta! Šta tražiš?", sent_text(call))

    def test_english_too(self):
        text_value, _ = answer_intent(Mock(), parse_question("how are you?"), CONFIG, lang="en")
        self.assertTrue(text_value.startswith("All good, thanks!"), text_value)
        self.assertIn("Golf under 15000 KM", text_value)


# ---- I3: "dobre ponude" goes to the deals section -----------------------------------------------

class DealsParsingTests(unittest.TestCase):
    def test_sweep_message_is_a_deals_question(self):  # I3
        intent = parse_question("najjeftinije dobre ponude ove sedmice")
        self.assertEqual(("deals", 7, ("cheapest",), Filters()), (intent.kind, intent.days, intent.labels,
                                                                   intent.filters))

    def test_other_wordings(self):
        for message, filters in (("dobre prilike", Filters()), ("ima li nešto povoljno?", Filters()),
                                 ("good deals under 15k", Filters(budget=15000)),
                                 ("dobre ponude do 20 hiljada od 2015", Filters(budget=20000, year_min=2015)),
                                 ("povoljne Octavie dizel", Filters("octavie", fuel="diesel")),
                                 ("any bargains?", Filters())):
            intent = parse_question(message)
            self.assertEqual(("deals", filters), (intent.kind, intent.filters), message)
            self.assertEqual(14, intent.days, message)

    def test_a_price_question_stays_a_price_check(self):
        for message in ("da li je 22.000 KM dobra cijena za Audi A4 2017?",  # I5
                        "is this a good price: Audi A4 2017 22.000 KM",  # I4
                        "je li ovo dobra ponuda: Golf 2017 15000 KM"):
            self.assertEqual("price_check", parse_question(message).kind, message)

    def test_the_title_reads_back_and_a_follow_up_narrows_it(self):
        filters = Filters(budget=20000, year_min=2015)
        title = telegram_bot.deals_title(filters, "bs")
        self.assertEqual("🏷 Dobre prilike · do 20.000 KM · od 2015", title)
        context = context_from_reply(title)
        self.assertEqual(("deals", filters), (context.kind, context.filters))
        followup = parse_question("a dizel?", context)
        self.assertEqual(("deals", "diesel", 20000), (followup.kind, followup.filters.fuel, followup.filters.budget))
        for titles, lang in zip(__import__("telegram_intent")._TITLES, ("en", "bs")):
            self.assertEqual(titles["deals"], telegram_i18n.t(lang, "deals_title"))


# ---- S2: compare like with like ------------------------------------------------------------------

class CompareParsingTests(unittest.TestCase):
    def test_years_in_the_message_apply_to_both_models(self):
        for message, years in (("Octavia ili Passat 2016-2019?", (2016, 2019)),
                               ("uporedi Golf i Passat od 2018", (2018, None)),
                               ("Octavia vs Passat 2017", (2017, 2017))):
            intent = parse_question(message)
            self.assertEqual(("compare", years), (intent.kind, (intent.filters.year_min, intent.filters.year_max)),
                             message)
        self.assertEqual(("octavia", "passat"), parse_question("Octavia ili Passat za porodicu?").labels)  # S2

    def test_which_years_are_compared(self):
        none = (None, None)
        pick = telegram_bot._compare_years
        self.assertEqual(([(2016, 2019)] * 2, "user", None), pick([none, none], (2016, 2019), [None, None]))
        self.assertEqual(([(2014, 2019)] * 2, "overlap", None), pick([none, none], none, [(2008, 2019), (2014, 2022)]))
        self.assertEqual(([none] * 2, "none", None), pick([none, none], none, [(2001, 2008), (2014, 2022)]))
        self.assertEqual(([(2012, 2020)] * 2, "generation", 0), pick([(2012, 2020), none], none, [None, None]))
        self.assertEqual(([(2012, 2020), (2004, 2013)], "generations", None),
                         pick([(2012, 2020), (2004, 2013)], none, [None, None]))
        # user years inside a named generation keep to that generation
        self.assertEqual(([(2016, 2020), (2016, 2022)], "user", None),
                         pick([(2012, 2020), none], (2016, 2022), [None, None]))


# ---- database ------------------------------------------------------------------------------------

def _naive_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@unittest.skipUnless(os.getenv("DEALER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class WeakCasesDatabaseTests(unittest.TestCase):
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
            c.execute(text("DELETE FROM car_listings WHERE listing_id LIKE '__qa_w_%'"))

    def seed(self, rows, *, first_days=20, last_days=1):
        """rows: (id, make, model, year, price, mileage[, fuel])"""
        with self.engine.begin() as c:
            for lid, make, model, year, price, mileage, *fuel in rows:
                c.execute(text("""INSERT INTO car_listings(listing_id,make,model,year,price,price_status,mileage,
                    fuel_type,source,collection_method,last_verified_at,first_seen_at,is_active,
                    consecutive_missing_runs) VALUES (:id,:make,:model,:year,:price,'priced',:mileage,:fuel,
                    'olx.ba','olx_api',:last,:first,true,0)"""),
                    {"id": f"__qa_w_{lid}", "make": make, "model": model, "year": year, "price": price,
                     "mileage": mileage, "fuel": fuel[0] if fuel else "diesel",
                     "first": self.now - timedelta(days=first_days), "last": self.now - timedelta(days=last_days)})

    def answer(self, message, lang="bs"):
        text_value, markup = answer_intent(self.engine, parse_question(message), CONFIG, lang=lang)
        return telegram_bot.html_to_text(text_value, keep_links=True), markup

    def ids(self, reply):
        return set(re.findall(r"artikal/__qa_w_(\w+)", reply))

    # E9 (Ogi, 17 Nov 2026): "golf 2015" lists only 2015 golfs; "golf 15" asks first
    def test_golf_2015_lists_only_2015_golfs(self):
        self.seed([("g15", "Volkswagen", "Golf", 2015, 15000, 180000), ("g17", "Volkswagen", "Golf", 2017, 19000,
                                                                         140000)])
        reply, _ = self.answer("golf 2015")
        self.assertTrue(reply.startswith("Viđeni oglasi · golf · 2015\n"), reply)
        self.assertEqual({"g15"}, self.ids(reply))
        self.assertNotIn("Nisam prepoznao", reply)

    def test_golf_15_asks_instead_of_listing(self):
        self.seed([("g15", "Volkswagen", "Golf", 2015, 15000, 180000)])
        reply, _ = self.answer("golf 15")
        self.assertTrue(reply.startswith("Šta znači 15: godište (2015), cijena (15.000 KM) ili nešto drugo?"), reply)
        self.assertEqual(set(), self.ids(reply))

    # E3
    def test_an_unrealistic_request_says_so_and_gives_the_cheapest_real_match(self):
        self.seed([(f"t{n}", "Tesla", "Model 3", 2024, price, 20000 + n * 1000)
                   for n, price in enumerate((58000, 61000, 64000, 70000, 75000))]
                  + [("tjunk", "Tesla", "Model 3", 2024, 15000, 15000)])  # a placeholder price, over the cap
        reply, markup = self.answer("Tesla 2024 do 10k")  # E3
        self.assertIn("Nema odgovarajućih oglasa u zadnjih 90 dana.", reply)
        self.assertIn("Ništa poput „tesla · do 10.000 KM · 2024“ ne postoji u našim podacima.", reply)
        self.assertIn("Najjeftiniji stvarni oglas (tesla · 2024, bilo koja cijena) je Tesla Model 3", reply)
        self.assertIn("__qa_w_t0", reply)
        self.assertIn("za 58.000 KM", reply)
        self.assertNotIn("__qa_w_tjunk", reply)
        self.assertIn("Tipična tražena cijena (tesla · 2024, bilo koja cijena): medijan 62.500 KM (6 oglasa", reply)
        self.assertEqual("🔎 tesla · 2024, sve cijene", markup.rows[0][0][0])
        self.assertEqual(Filters("tesla", year_min=2024, year_max=2024), telegram_bot.parse_refine_callback(markup.rows[0][0][1]))

    def test_without_that_model_year_it_falls_back_to_the_model(self):
        self.seed([("t19", "Tesla", "Model S", 2019, 60000, 90000), ("t20", "Tesla", "Model 3", 2020, 52000, 80000)])
        reply, markup = self.answer("Tesla 2024 do 10k")
        self.assertIn("Najjeftiniji stvarni oglas (tesla, sva godišta i cijene) je Tesla Model 3 "
                      "(https://olx.ba/artikal/__qa_w_t20) · 2020 · 80.000 km za 52.000 KM.", reply)
        self.assertIn("medijan 56.000 KM (2 oglasa", reply)
        self.assertEqual(Filters("tesla"), telegram_bot.parse_refine_callback(markup.rows[0][0][1]))

    def test_a_realistic_search_without_fresh_matches_is_unchanged(self):
        self.seed([("g1", "Volkswagen", "Golf", 2016, 14000, 150000)], first_days=60, last_days=40)
        reply, _ = self.answer("Golf do 15000 KM")
        self.assertNotIn("ne postoji", reply)

    # I3
    def test_good_deals_lists_listings_priced_below_the_same_model_and_year(self):
        cohort = [(f"o{n}", "Škoda", "Octavia", 2016, 16000 + n * 500, 160000) for n in range(5)]
        self.seed(cohort + [("deal", "Škoda", "Octavia", 2016, 12000, 140000),
                            ("old", "Škoda", "Octavia", 2016, 11500, 140000, "diesel")])
        with self.engine.begin() as c:  # seen 10 days ago: in the 14-day window, not in "this week"
            c.execute(text("UPDATE car_listings SET last_verified_at=:at WHERE listing_id='__qa_w_old'"),
                      {"at": self.now - timedelta(days=10)})
        reply, markup = self.answer("najjeftinije dobre ponude ove sedmice")  # I3
        self.assertTrue(reply.startswith("🏷 Dobre prilike\n"), reply)
        self.assertIn("viđeni u zadnjih 7 dana, prvo najjeftiniji:", reply)
        self.assertEqual({"deal"}, self.ids(reply))
        self.assertIn("ispod medijana", reply)
        self.assertTrue(button_labels(markup)[0].startswith("⭐ Škoda Octavia · 2016 · 12.000 KM"))
        reply, _ = self.answer("dobre prilike")
        self.assertEqual({"deal", "old"}, self.ids(reply))
        reply, _ = self.answer("povoljne Octavie")  # plural: searched as octavia
        self.assertTrue(reply.startswith("🏷 Dobre prilike · octavia\n"), reply)
        self.assertEqual({"deal", "old"}, self.ids(reply))
        reply, _ = self.answer("povoljni Passati")
        self.assertEqual(set(), self.ids(reply))
        self.assertIn("Trenutno nijedan svjež oglas nije znatno jeftiniji od sličnih.", reply)

    # S2
    def test_compare_uses_the_years_both_models_have_and_says_which(self):
        octavia = [(f"o{y}", "Škoda", "Octavia", y, 1000 * (y - 2000), 150000) for y in range(2008, 2021)]
        passat = [(f"p{y}", "Volkswagen", "Passat", y, 1000 * (y - 1999), 150000) for y in range(2012, 2023)]
        self.seed(octavia + passat)
        reply, markup = self.answer("Octavia ili Passat za porodicu?")  # S2
        self.assertIn("Upoređena godišta: 2013–2019, godišta koja oba modela imaju u našim podacima.", reply)
        self.assertIn("octavia (2013–2019)\nUzorak: 7 oglasa", reply)
        self.assertIn("passat (2013–2019)\nUzorak: 7 oglasa", reply)
        self.assertIn("Medijan tražene cijene: 16.000 KM", reply)  # octavia 2013-2019, not 2008-2020
        self.assertIn("Medijan tražene cijene: 17.000 KM", reply)
        self.assertIn("Medijan po godištu:", reply)
        self.assertIn("ista godišta", reply)
        self.assertEqual([Filters("octavia", year_min=2013, year_max=2019), Filters("passat", year_min=2013,
                                                                                     year_max=2019)],
                         [telegram_bot.parse_refine_callback(data) for _, data in markup.rows[0]])

    def test_compare_uses_the_years_the_user_gave(self):
        octavia = [(f"o{y}", "Škoda", "Octavia", y, 1000 * (y - 2000), 150000) for y in range(2008, 2021)]
        passat = [(f"p{y}", "Volkswagen", "Passat", y, 1000 * (y - 1999), 150000) for y in range(2012, 2023)]
        self.seed(octavia + passat)
        reply, _ = self.answer("Octavia ili Passat 2016-2019?")
        self.assertIn("Upoređena godišta: 2016–2019 (kako si tražio), za oba modela.", reply)
        self.assertIn("octavia (2016–2019)\nUzorak: 4 oglasa", reply)
        self.assertIn("passat (2016–2019)\nUzorak: 4 oglasa", reply)

    def test_a_generation_on_one_side_sets_the_years_for_both(self):
        self.seed([("g6", "Volkswagen", "Golf", 2010, 9000, 200000), ("g7", "Volkswagen", "Golf", 2016, 17000, 150000),
                   ("p05", "Volkswagen", "Passat", 2005, 4000, 300000),
                   ("p16", "Volkswagen", "Passat", 2016, 18000, 150000)])
        reply, _ = self.answer("uporedi Golf 7 i Passat")
        self.assertIn("Upoređena godišta: 2012–2020 (golf 7), za oba modela.", reply)
        self.assertIn("passat (2012–2020)\nUzorak: 1 oglasa", reply)


if __name__ == "__main__":
    unittest.main()
