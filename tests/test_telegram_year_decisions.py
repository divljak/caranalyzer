"""Ogi's year decisions, 17 Nov 2026.

1. "Golf 2015" is exactly 2015. With no match the bot offers older / newer years as two buttons.
   Ranges ("od 2015", "2015+", "novije od 2015", "2015-2018") stay ranges.
2. A bare short number ("golf 20") is asked about (year, price or mileage), never guessed. Clear forms
   ("20k KM", "20.000 KM", "200 KS") are read as before.
3. A 2025+ car under 1.000 km is still "new" and left out of used lists; "za porodicu" in compares is a known gap.

Parser tests need nothing; database tests need a throwaway PostgreSQL (DEALER_TEST_DATABASE_URL).
No test here needs Gemini.
"""

from datetime import datetime, timedelta, timezone
import os
import re
import unittest

from sqlalchemy import create_engine, text

import telegram_bot
from telegram_bot import TelegramConfig, answer_intent, keyboard, parse_refine_callback
from telegram_intent import Filters, parse_question

CONFIG = TelegramConfig.from_env({
    "TELEGRAM_BOT_TOKEN": "test-only-token",
    "TELEGRAM_WEBHOOK_SECRET": "test_only_webhook_secret_123",
    "TELEGRAM_CHAT_ID": "-5332163835",
    "TELEGRAM_ALLOWED_USER_IDS": "123, 456",
})
THIS_YEAR = datetime.now(timezone.utc).year


def years(message):
    f = parse_question(message).filters
    return f.search, f.year_min, f.year_max


class ExactYearParserTests(unittest.TestCase):
    def test_a_lone_year_is_that_year_only(self):
        for message in ("Golf 2015", "golf 2015 dizel", "2015 golf", "audi a4 2018 do 20000 KM", "Golf 2015."):
            _, low, high = years(message)
            self.assertEqual(low, high, message)
            self.assertIsNotNone(low, message)
        self.assertEqual(("golf", 2015, 2015), years("Golf 2015"))

    def test_explicit_ranges_stay_ranges(self):
        for message, low, high in (("golf od 2015", 2015, None), ("golf 2015+", 2015, None),
                                   ("golf novije od 2015", 2015, None), ("golf 2015 ili novije", 2015, None),
                                   ("golf from 2015", 2015, None), ("golf 2015-2018", 2015, 2018),
                                   ("golf od 2015 do 2018", 2015, 2018), ("golf između 2015 i 2018", 2015, 2018),
                                   ("golf do 2018", None, 2018), ("golf prije 2018", None, 2018)):
            self.assertEqual(("golf", low, high), years(message), message)

    def test_a_price_after_do_is_not_a_year_ceiling(self):
        f = parse_question("golf do 2000 KM").filters
        self.assertIsNone(f.year_max)
        self.assertEqual(2000, f.budget)

    def test_price_and_mileage_questions_still_compare_the_year_plus_minus_one(self):
        intent = parse_question("koliko košta Golf 2015 prosječno")
        self.assertEqual(("price_check", 2015, (2014, 2016)),
                         (intent.kind, intent.year, (intent.filters.year_min, intent.filters.year_max)))
        intent = parse_question("prosječna kilometraža Golf 2015")
        self.assertEqual(("mileage_stats", (2014, 2016)),
                         (intent.kind, (intent.filters.year_min, intent.filters.year_max)))

    def test_a_range_price_check_keeps_its_range(self):
        intent = parse_question("koliko košta Golf od 2015")
        self.assertEqual((2015, None), (intent.filters.year_min, intent.filters.year_max))

    def test_generations_are_still_year_ranges(self):
        self.assertEqual(("golf", 2012, 2020), years("golf 7"))

    def test_the_year_survives_a_button_round_trip(self):
        f = Filters("golf", year_min=2015, year_max=2015)
        self.assertEqual(f, parse_refine_callback(telegram_bot.refine_callback(f)))

    def test_a_follow_up_year_replaces_the_exact_year(self):
        from telegram_intent import merge_filters
        merged = merge_filters(Filters("golf", year_min=2015, year_max=2015), parse_question("a 2017?").filters)
        self.assertEqual(("golf", 2017, 2017), (merged.search, merged.year_min, merged.year_max))
        merged = merge_filters(Filters("golf", year_min=2015, year_max=2015), parse_question("od 2017").filters)
        self.assertEqual(("golf", 2017, None), (merged.search, merged.year_min, merged.year_max))


class ShortNumberParserTests(unittest.TestCase):
    def test_golf_20_is_a_question(self):
        intent = parse_question("golf 20")
        self.assertEqual(("clarify", "short_number", 20), (intent.kind, intent.note, intent.offset))
        self.assertEqual("golf", intent.filters.search)
        self.assertFalse(intent.filters.numeric())

    def test_the_other_things_asked_with_it_are_kept(self):
        intent = parse_question("golf 20 dizel")
        self.assertEqual(("clarify", "golf", "diesel"), (intent.kind, intent.filters.search, intent.filters.fuel))

    def test_clear_forms_are_read_as_before(self):
        f = parse_question("golf 20k KM").filters
        self.assertEqual(("golf", 20000, None, None), (f.search, f.budget, f.year_min, f.mileage_max))
        f = parse_question("golf 20.000 KM").filters
        self.assertEqual(("golf", 20000, None), (f.search, f.budget, f.year_min))
        f = parse_question("golf do 20000 KM").filters
        self.assertEqual(20000, f.budget)
        f = parse_question("golf 200 KS").filters
        self.assertEqual((None, None, None), (f.budget, f.year_min, f.mileage_max))
        self.assertNotEqual("clarify", parse_question("golf 200 KS").kind)
        f = parse_question("golf do 150000 km").filters
        self.assertEqual(150000, f.mileage_max)
        self.assertNotEqual("short_number", parse_question("golf 20 kW").note)
        self.assertNotEqual("short_number", parse_question("golf 20 godina").note)

    def test_a_leading_zero_is_a_year_and_a_generation_is_not_a_number(self):
        self.assertEqual(("passat", 2008, 2008), years("passat 08"))
        self.assertEqual(("golf", 2012, 2020), years("golf 7"))

    def test_the_question_never_goes_to_gemini(self):
        self.assertFalse(telegram_bot._needs_interpreter(parse_question("golf 20"), "golf 20"))


class ShortNumberAnswerTests(unittest.TestCase):
    def answer(self, message, lang="bs"):
        return answer_intent(None, parse_question(message), CONFIG, lang=lang)

    def test_the_question_is_short_bosnian_with_three_buttons(self):
        reply, choices = self.answer("golf 20")
        self.assertEqual("Šta znači 20: godište (2020), cijena (20.000 KM) ili nešto drugo?", reply.split(" Ako")[0])
        buttons = keyboard(CONFIG, choices, "bs")["inline_keyboard"][0]
        self.assertEqual(["📅 Godište 2020", "💰 Do 20.000 KM", "🛣 Do 20.000 km"], [b["text"] for b in buttons])
        year, price, km = (parse_refine_callback(b["callback_data"]) for b in buttons)
        self.assertEqual(("golf", 2020, 2020, None, None), (year.search, year.year_min, year.year_max, year.budget,
                                                           year.mileage_max))
        self.assertEqual(("golf", 20000, None), (price.search, price.budget, price.year_min))
        self.assertEqual(("golf", 20000, None), (km.search, km.mileage_max, km.year_min))

    def test_a_reading_the_message_already_has_is_not_offered_again(self):
        _, choices = self.answer("golf 20 dizel")
        data = [b["callback_data"] for b in keyboard(CONFIG, choices, "bs")["inline_keyboard"][0]]
        self.assertEqual(3, len(data))
        self.assertTrue(all(parse_refine_callback(d).fuel == "diesel" for d in data))

    def test_english_asks_too(self):
        reply, _ = self.answer("golf 20", lang="en")
        self.assertTrue(reply.startswith("What does 20 mean: model year (2020), price (20.000 KM)"), reply)


class YearAlternativeSelectionTests(unittest.TestCase):
    """_year_alternatives picks the +-1 window, else +-2, else nothing; no database needed."""

    def run_helper(self, cars, year=2016, **extra):
        calls = []

        def fake_find_rows(connection, filters, offset, probe=False, probe_new=False, hints=()):
            calls.append((filters.year_min, filters.year_max))
            return [{"year": y} for y in cars if filters.year_min <= y <= filters.year_max]

        original = telegram_bot._find_rows
        telegram_bot._find_rows = fake_find_rows
        try:
            rows, low, high = telegram_bot._year_alternatives(None, Filters("golf", year_min=year, year_max=year,
                                                                           **extra))
        finally:
            telegram_bot._find_rows = original
        return [r["year"] for r in rows], low, high, calls

    def test_plus_minus_one_wins_and_stops(self):
        self.assertEqual(([2015, 2017], 2015, 2017, [(2015, 2017)]), self.run_helper([2014, 2015, 2017, 2018]))

    def test_plus_minus_two_only_when_one_is_empty(self):
        self.assertEqual(([2018], 2014, 2018, [(2015, 2017), (2014, 2018)]), self.run_helper([2012, 2018]))

    def test_nothing_within_two_years(self):
        self.assertEqual(([], None, None, [(2015, 2017), (2014, 2018)]), self.run_helper([2012, 2019]))

    def test_the_window_stays_inside_the_years_the_bot_accepts(self):
        low, high = telegram_bot.YEAR_RANGE
        self.assertEqual([(low, low + 1), (low, low + 2)], self.run_helper([], year=low)[3])
        self.assertEqual([(high - 1, high), (high - 2, high)], self.run_helper([], year=high)[3])


@unittest.skipUnless(os.getenv("DEALER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class YearDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["DEALER_TEST_DATABASE_URL"])

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.addCleanup(self.clean)
        self.clean()

    def clean(self):
        with self.engine.begin() as c:
            c.execute(text("DELETE FROM car_listings WHERE listing_id LIKE '__qa_y_%'"))

    def seed(self, rows):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self.engine.begin() as c:
            for key, make, model, year, price, km in rows:
                c.execute(text("""INSERT INTO car_listings(listing_id,make,model,year,price,price_status,mileage,
                    fuel_type,source,collection_method,last_verified_at,first_seen_at,is_active,
                    consecutive_missing_runs) VALUES (:id,:make,:model,:year,:price,'priced',:km,'diesel',
                    'olx.ba','olx_api',:last,:first,true,0)"""),
                          {"id": f"__qa_y_{key}", "make": make, "model": model, "year": year, "price": price,
                           "km": km, "last": now - timedelta(days=1), "first": now - timedelta(days=20)})

    def answer(self, message, lang="bs"):
        return answer_intent(self.engine, parse_question(message), CONFIG, lang=lang)

    def ids(self, reply):
        return set(re.findall(r"artikal/__qa_y_(\w+)", reply))

    GOLFS = [("g12", "Volkswagen", "Golf", 2012, 9000, 200000), ("g14", "Volkswagen", "Golf", 2014, 11000, 180000),
             ("g15", "Volkswagen", "Golf", 2015, 12000, 160000), ("g17", "Volkswagen", "Golf", 2017, 15000, 140000),
             ("g18", "Volkswagen", "Golf", 2018, 17000, 110000), ("g20", "Volkswagen", "Golf", 2020, 21000, 60000)]

    def test_golf_2015_lists_only_2015(self):
        self.seed(self.GOLFS)
        reply, _ = self.answer("Golf 2015")
        self.assertEqual({"g15"}, self.ids(reply))

    def test_ranges_still_list_the_range(self):
        self.seed(self.GOLFS)
        self.assertEqual({"g15", "g17", "g18", "g20"}, self.ids(self.answer("Golf od 2015")[0]))
        self.assertEqual({"g15", "g17", "g18", "g20"}, self.ids(self.answer("Golf 2015+")[0]))
        self.assertEqual({"g15", "g17", "g18"}, self.ids(self.answer("Golf 2015-2018")[0]))
        self.assertEqual({"g12", "g14", "g15"}, self.ids(self.answer("Golf do 2015")[0]))

    def test_no_match_in_the_year_lists_the_neighbouring_years_as_alternatives(self):
        self.seed(self.GOLFS)
        reply, choices = self.answer("Golf 2016")
        self.assertIn("Nema oglasa za „golf · 2016“", reply)
        self.assertIn("Alternativa, nije 2016", reply)
        self.assertIn("(2015–2017)", reply)
        self.assertEqual({"g15", "g17"}, self.ids(reply))  # +-1 only: 2014 and 2018 are not shown
        self.assertNotIn("Želite li vidjeti starije", reply)
        buttons = keyboard(CONFIG, choices, "bs")["inline_keyboard"][0]
        self.assertEqual(["🔎 Sve iz 2015–2017"], [b["text"] for b in buttons])
        alt = parse_refine_callback(buttons[0]["callback_data"])
        self.assertEqual(("golf", 2015, 2017), (alt.search, alt.year_min, alt.year_max))

    def test_an_empty_plus_minus_one_widens_to_two(self):
        self.seed(self.GOLFS)
        reply, _ = self.answer("Golf 2019")  # 2018 and 2020 are +-1; 2017 (+-2) is not needed
        self.assertEqual({"g18", "g20"}, self.ids(reply))
        self.clean()
        self.seed([("g12", "Volkswagen", "Golf", 2012, 9000, 200000), ("g18", "Volkswagen", "Golf", 2018, 17000, 110000)])
        reply, _ = self.answer("Golf 2016")  # +-1 empty, 2018 is +-2
        self.assertEqual({"g18"}, self.ids(reply))
        self.assertIn("(2014–2018)", reply)
        self.assertIn("nije 2016", reply)

    def test_nothing_within_two_years_falls_back_to_the_nearest_year_buttons(self):
        self.seed(self.GOLFS)
        reply, choices = self.answer("Golf 1995")
        self.assertEqual(set(), self.ids(reply))
        self.assertIn("Želite li vidjeti starije ili novije modele?", reply)
        buttons = keyboard(CONFIG, choices, "bs")["inline_keyboard"][0]
        self.assertEqual(["⏩ Novije (2012–2014)"], [b["text"] for b in buttons])
        reply, choices = self.answer("Golf 2030")
        buttons = keyboard(CONFIG, choices, "bs")["inline_keyboard"][0]
        self.assertEqual(["⏪ Starije (2018–2020)"], [b["text"] for b in buttons])
        self.clean()
        self.seed([("a", "Volkswagen", "Golf", 2005, 4000, 250000), ("b", "Volkswagen", "Golf", 2012, 9000, 200000)])
        reply, choices = self.answer("Golf 2009")  # 2005 and 2012 are both 3+ years away
        self.assertEqual(set(), self.ids(reply))
        buttons = keyboard(CONFIG, choices, "bs")["inline_keyboard"][0]
        self.assertEqual(["⏪ Starije (2003–2005)", "⏩ Novije (2012–2014)"], [b["text"] for b in buttons])

    def test_the_alternatives_button_reruns_the_search_for_those_years(self):
        self.seed(self.GOLFS)
        _, choices = self.answer("Golf 2016")
        alt = parse_refine_callback(keyboard(CONFIG, choices, "bs")["inline_keyboard"][0][0]["callback_data"])
        self.assertEqual({"g15", "g17"}, self.ids(telegram_bot.find_message(self.engine, alt, 0, "bs")))

    def test_the_other_filters_carry_into_the_alternatives(self):
        self.seed(self.GOLFS + [("c17", "Volkswagen", "Golf", 2017, 15500, 140000)])
        reply, choices = self.answer("Golf 2016 do 15200 KM")
        self.assertEqual({"g15", "g17"}, self.ids(reply))  # the 15.500 KM car of 2017 is over the budget
        alt = parse_refine_callback(keyboard(CONFIG, choices, "bs")["inline_keyboard"][0][0]["callback_data"])
        self.assertEqual(15200, alt.budget)

    def test_an_explicit_range_keeps_the_nearest_year_buttons(self):
        self.seed(self.GOLFS)
        reply, _ = self.answer("Golf 2021-2022")
        self.assertNotIn("Alternativa", reply)

    def test_new_cars_stay_out_of_the_alternatives(self):
        self.seed([("n1", "Volkswagen", "Golf", THIS_YEAR - 1, 30000, 50), ("u1", "Volkswagen", "Golf", THIS_YEAR - 3,
                                                                            22000, 40000)])
        reply, _ = self.answer(f"Golf {THIS_YEAR - 2}")
        self.assertEqual({"u1"}, self.ids(reply))

    def test_no_year_choice_when_the_model_has_no_cars_at_all(self):
        reply, choices = self.answer("Golf 2015")  # empty table
        self.assertNotIn("Želite li", reply)

    def test_the_other_filters_carry_into_the_nearest_year_buttons(self):
        self.seed([("a", "Volkswagen", "Golf", 2005, 4000, 250000), ("b", "Volkswagen", "Golf", 2012, 9000, 200000)])
        _, choices = self.answer("Golf 2009 do 16000 KM")
        older, newer = (parse_refine_callback(b["callback_data"])
                        for b in keyboard(CONFIG, choices, "bs")["inline_keyboard"][0])
        self.assertEqual((2003, 2005, 16000), (older.year_min, older.year_max, older.budget))
        self.assertEqual((2012, 2014, 16000), (newer.year_min, newer.year_max, newer.budget))

    def test_new_cars_under_1000_km_stay_out_of_an_exact_year_list(self):
        self.seed([("n1", "Volkswagen", "Golf", THIS_YEAR - 1, 30000, 50), ("u1", "Volkswagen", "Golf", THIS_YEAR - 1,
                                                                            22000, 40000)])
        reply, _ = self.answer(f"Golf {THIS_YEAR - 1}")
        self.assertEqual({"u1"}, self.ids(reply))

    def test_a_button_answer_to_the_short_number_question_runs_the_search(self):
        self.seed(self.GOLFS)
        _, choices = self.answer("golf 20")
        year, price, km = (parse_refine_callback(b["callback_data"])
                           for b in keyboard(CONFIG, choices, "bs")["inline_keyboard"][0])
        self.assertEqual({"g20"}, self.ids(telegram_bot.find_message(self.engine, year, 0, "bs")))
        self.assertEqual({"g12", "g14", "g15", "g17", "g18"}, self.ids(telegram_bot.find_message(self.engine, price, 0, "bs")))
        self.assertEqual(set(), self.ids(telegram_bot.find_message(self.engine, km, 0, "bs")), "nothing under 20.000 km")


if __name__ == "__main__":
    unittest.main()
