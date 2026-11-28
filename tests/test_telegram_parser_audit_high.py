"""The four high-priority parser bugs of the 28 Nov 2026 audit of the Telegram bot (telegram_intent.py).

1. A model number before "do 15k" ("bmw 320 do 15k KM") was read as the low end of a price range.
2. A negation before "preko / više od" ("golf nikako preko 15k KM") was read as a price / mileage floor.
3. Several model years ("golf 2015 ili 2016"), "stariji od 2010", "poslije 2015", "15 godište" were misread.
4. Unknown leftover words ("limit 10k KM za auto", "4x4 do 15k KM") became a model and ended in a dead end.

Parser-only: no Gemini, Telegram or database. Year-bound semantics (see the PR): "od / from / since / do / until"
include the year they name; "prije / before / starije od" and "poslije / nakon / iza / novije od" leave it out.
"""

from datetime import date
import unittest

import telegram_bot
from telegram_intent import parse_question, looks_like_followup, stated_filters

THIS_YEAR = date.today().year


class Chat:
    """A private chat through the bot's real follow-up memory."""

    def __init__(self, key=(902, 902)):
        for store in (telegram_bot._SCOPES, telegram_bot._BUDGETS, telegram_bot._HISTORY, telegram_bot._LANGS,
                      telegram_bot._ACTIVE):
            store.clear()
        self.key = key

    def say(self, message):
        intent = telegram_bot._understand(message, {}, self.key, None, chat_kind="private")
        telegram_bot._remember_scope(self.key, intent)
        return intent


def price(message):
    filters = parse_question(message).filters
    return filters.search, filters.budget, filters.price_min


def mileage(message):
    filters = parse_question(message).filters
    return filters.mileage_min, filters.mileage_max, filters.price_min, filters.budget


def years(message):
    filters = parse_question(message).filters
    return filters.search, filters.year_min, filters.year_max, filters.budget


class ModelNumberBeforeDoTests(unittest.TestCase):
    def test_a_model_number_stays_in_the_model_and_do_is_the_cap(self):
        for message, search, budget in (("bmw 320 do 15k KM", "bmw 320", 15000),
                                        ("mercedes e 220 do 20k KM", "mercedes e 220", 20000),
                                        ("a 180 do 12k KM", "a 180", 12000),
                                        ("vito 111 do 15k", "vito 111", 15000),
                                        ("bmw 118 do 15000 KM", "bmw 118", 15000),
                                        ("bmw 320 do 15000", "bmw 320", 15000),
                                        ("mercedes c 200 do 15k KM", "mercedes c 200", 15000)):
            self.assertEqual((search, budget, None), price(message), message)

    def test_a_bmw_series_pair_is_not_an_amount_and_the_range_after_it_is_read(self):
        self.assertEqual(("bmw", 20000, 15000), price("bmw 3/5 do 15–20k"))
        self.assertEqual(("bmw", 20000, 15000), price("bmw 3/5 do 15-20k"))

    def test_existing_model_and_cap_forms_keep_working(self):
        for message, search, budget in (("bmw 520d do 18k", "bmw 520d", 18000), ("audi a3 do 12k", "audi a3", 12000),
                                        ("golf do 12000", "golf", 12000), ("golf 7 do 12k KM", "golf", 12000)):
            self.assertEqual((search, budget, None), price(message), message)

    def test_genuine_ranges_stay_ranges(self):
        for message in ("10 do 15k", "10-15k", "od 10 do 15k", "10 – 15k", "od 10 do 15 hiljada"):
            self.assertEqual(("", 15000, 10000), price(message), message)
        self.assertEqual(("golf", 15000, 10000), price("golf 10 do 15k"))
        self.assertEqual(("golf", 8000, 5000), price("golf 5000 do 8000 KM"))

    def test_a_three_digit_mileage_range_is_not_taken_for_a_model_number(self):
        self.assertEqual((100000, 150000, None, None), mileage("golf 100 do 150 hiljada km"))
        self.assertEqual((100000, 200000, None, None), mileage("a 100 do 200k km"))
        self.assertEqual((100000, 200000, None, None), mileage("golf 100 do 200k km"))


class NegatedAboveTests(unittest.TestCase):
    def test_a_negation_before_preko_or_vise_od_is_a_price_cap(self):
        for message in ("golf nikako preko 15k KM", "golf bez preko 15k KM", "golf ne bi preko 15k KM",
                        "golf ne želim preko 15k KM", "golf ne zelim preko 15k KM", "golf ništa preko 15k KM",
                        "golf nista preko 15k KM", "golf nemoj preko 15k KM", "golf ne smije preko 15k KM",
                        "golf ne treba preko 15k KM", "golf nemam preko 15k KM",
                        "golf, da ne košta više od 15k KM", "golf koji ne košta više od 15k KM"):
            self.assertEqual(("golf", 15000, None), price(message), message)

    def test_a_negated_cap_without_a_model(self):
        for message, cap in (("da ne košta više od 15k KM", 15000),
                             ("Hoću auto koji ne košta više od 12000 KM", 12000)):
            self.assertEqual(("", cap, None), price(message), message)

    def test_a_negation_before_preko_is_a_mileage_cap(self):
        self.assertEqual((None, 100000, None, None), mileage("golf nemoj preko 100000 km"))
        self.assertEqual((None, 100000, None, None), mileage("golf bez preko 100000 km"))
        self.assertEqual((None, 100000, None, 15000), mileage("golf do 15000 KM ne preko 100000 km"))

    def test_the_follow_up_keeps_the_model_and_caps_the_mileage(self):
        chat = Chat()
        chat.say("golf do 15000 KM")
        intent = chat.say("a bez preko 200000 km")
        filters = intent.filters
        self.assertEqual(("golf", 15000, None, 200000), (filters.search, filters.budget, filters.mileage_min,
                                                         filters.mileage_max))

    def test_a_plain_preko_is_still_a_floor(self):
        self.assertEqual(("", None, 30000), price("preko 30k KM"))
        self.assertEqual(("golf", None, 30000), price("golf preko 30k KM"))
        self.assertEqual(("golf", None, 30000), price("golf iznad 30.000 KM"))
        self.assertEqual((100000, None, None, None), mileage("golf preko 100000 km"))
        self.assertEqual(("golf", None, 15000), price("golf, ne dizel, preko 15k KM"))  # a comma ends the negation

    def test_ne_manje_od_and_ne_prelazi_keep_their_meaning(self):
        self.assertEqual(("golf", None, 15000), price("golf ne manje od 15k KM"))
        self.assertEqual(("golf", 30000, None), price("golf ne prelazi preko 30k KM"))
        self.assertEqual(("golf", 30000, None), price("golf cijena ne ide preko 30k KM"))

    def test_the_negation_words_never_become_a_model(self):
        for message in ("golf ništa preko 15k KM", "golf nemoj preko 15k KM", "golf ne smije preko 15k KM"):
            self.assertEqual("golf", parse_question(message).filters.search, message)


class YearTests(unittest.TestCase):
    def test_several_years_are_a_year_range_and_never_a_price(self):
        for message, search, low, high in (("golf 2015 ili 2016", "golf", 2015, 2016),
                                           ("golf 7 2017 ili 2018 dizel", "golf", 2017, 2018),
                                           ("golf 6 2010 2011 2012", "golf", 2010, 2012),
                                           ("golf 2015, 2017", "golf", 2015, 2017),
                                           ("golf 2016 ili 2014", "golf", 2014, 2016),
                                           ("passat 2012 ili 2013 do 9000 KM", "passat", 2012, 2013)):
            self.assertEqual((search, low, high), years(message)[:3], message)
            self.assertIsNone(parse_question(message).filters.price_min, message)
        self.assertIsNone(years("golf 2015 ili 2016")[3])
        self.assertIsNone(years("golf 6 2010 2011 2012")[3])
        self.assertEqual(9000, years("passat 2012 ili 2013 do 9000 KM")[3])
        self.assertEqual("diesel", parse_question("golf 7 2017 ili 2018 dizel").filters.fuel)

    def test_a_second_year_in_a_compare_is_not_a_price(self):
        self.assertIsNone(parse_question("octavia 2016 ili passat 2018").filters.budget)

    def test_the_follow_up_year_range_keeps_the_model_and_has_no_price(self):
        chat = Chat()
        chat.say("golf do 15000 KM")
        filters = chat.say("a 2014 ili 2017?").filters
        self.assertEqual(("golf", 15000, 2014, 2017), (filters.search, filters.budget, filters.year_min,
                                                       filters.year_max))
        self.assertTrue(looks_like_followup("a 2014 ili 2017?"))

    def test_older_than_n_years_is_a_newest_model_year(self):
        self.assertEqual((None, THIS_YEAR - 10), years("stariji od 10 godina")[1:3])
        self.assertEqual(("golf", None, THIS_YEAR - 10), years("golf starije od 10 godina")[:3])
        self.assertEqual((None, THIS_YEAR - 10), years("older than 10 years")[1:3])

    def test_not_older_than_n_years_stays_a_floor(self):
        self.assertEqual((THIS_YEAR - 8, None), years("golf ne starije od 8 godina do 9000 KM")[1:3])
        self.assertEqual((THIS_YEAR - 10, None), years("Auto do 30 000km da nije starije od 10 godina")[1:3])
        self.assertEqual((THIS_YEAR - 5, None), years("golf staro do 5 godina")[1:3])

    def test_older_than_a_year_is_not_read_backwards(self):
        self.assertEqual(("golf", None, 2009), years("golf stariji od 2010")[:3])
        self.assertEqual(("golf", None, 2009), years("golf starije od 2010")[:3])
        self.assertEqual(("golf", None, 2009), years("golf older than 2010")[:3])

    def test_a_negated_older_than_a_year_is_a_floor(self):
        self.assertEqual(("golf", 2014, None), years("golf ne starije od 2014")[:3])
        self.assertEqual(("golf", 2014, None), years("golf nije starije od 2014")[:3])

    def test_prije_and_novije_od_leave_the_named_year_out_and_do_includes_it(self):
        for message, low, high in (("golf prije 2010", None, 2009), ("golf before 2010", None, 2009),
                                   ("golf do 2010", None, 2010), ("golf until 2010", None, 2010),
                                   ("golf novije od 2016", 2017, None), ("golf newer than 2016", 2017, None),
                                   ("golf poslije 2015", 2016, None), ("golf posle 2015", 2016, None),
                                   ("golf nakon 2015", 2016, None), ("golf after 2015", 2016, None),
                                   ("golf od 2015", 2015, None), ("golf from 2015", 2015, None),
                                   ("golf 2015+", 2015, None)):
            self.assertEqual(("golf", low, high), years(message)[:3], message)

    def test_poslije_does_not_leak_into_the_model(self):
        self.assertEqual("golf", parse_question("golf poslije 2015").filters.search)
        self.assertEqual("golf", parse_question("golf prije 2000 KM").filters.search)

    def test_x_ili_newer_or_older_is_an_open_range(self):
        for message, low, high in (("golf 2016 ili mlađi", 2016, None), ("golf 2016 ili mlađe", 2016, None),
                                   ("golf 2016 ili noviji", 2016, None), ("golf 2016 ili novije", 2016, None),
                                   ("golf 2012 ili stariji", None, 2012), ("golf 2012 ili starije", None, 2012)):
            self.assertEqual(("golf", low, high), years(message)[:3], message)

    def test_godiste_is_that_model_year(self):
        for message in ("golf 15 godište", "golf godište 15", "golf godište 2015", "golf 15. godište",
                        "golf godiste 15"):
            self.assertEqual(("golf", 2015, 2015), years(message)[:3], message)
        self.assertEqual(("golf", 1999, 1999), years("golf godište 99")[:3])

    def test_the_exact_year_rule_and_the_clarification_are_untouched(self):
        self.assertEqual(("golf", 2015, 2015), years("Golf 2015")[:3])
        self.assertEqual(("golf", 2015, 2015), years("golf 2015 dizel")[:3])
        intent = parse_question("golf 20")
        self.assertEqual(("clarify", "short_number"), (intent.kind, intent.note))
        self.assertEqual(("golf", 2015, 2015, 12000), years("golf 2015 12000 KM"))

    def test_price_checks_keep_their_one_year_window(self):
        intent = parse_question("koliko košta Golf 2015 prosječno")
        self.assertEqual(("price_check", (2014, 2016)),
                         (intent.kind, (intent.filters.year_min, intent.filters.year_max)))

    def test_year_ranges_and_a_year_with_a_price_unit_are_unchanged(self):
        self.assertEqual(("golf", 2015, 2018), years("golf 2015-2018")[:3])
        self.assertEqual(("golf", 2015, 2018), years("golf od 2015 do 2018")[:3])
        filters = parse_question("golf do 2000 KM").filters
        self.assertEqual((None, 2000), (filters.year_max, filters.budget))
        compare = parse_question("golf 6 ili 7 do 12000")
        self.assertEqual(("compare", 12000, 2), (compare.kind, compare.filters.budget, len(compare.labels)))


class UnknownLeftoverWordsTests(unittest.TestCase):
    def test_unknown_words_with_a_hard_filter_run_a_filter_only_search(self):
        for message, kind, budget, fuel in (("limit 10k KM za auto", "find", 10000, ""),
                                            ("budžet 12000 KM šta mi preporučuješ", "suggest", 12000, ""),
                                            ("koji je najpouzdaniji dizel do 15k KM", "find", 15000, "diesel"),
                                            ("4x4 do 15k KM", "find", 15000, "")):
            intent = parse_question(message)
            self.assertEqual((kind, "", budget, fuel),
                             (intent.kind, intent.filters.search, intent.filters.budget, intent.filters.fuel),
                             message)

    def test_a_follow_up_with_unknown_words_keeps_the_model_from_memory(self):
        chat = Chat()
        chat.say("passat do 15000 KM")
        filters = chat.say("Ok sad pokaži od 10.000 KM do 15.000 KM").filters
        self.assertEqual(("passat", 10000, 15000), (filters.search, filters.price_min, filters.budget))
        chat = Chat()
        chat.say("golf do 15000 KM")
        filters = chat.say("a ako povećamo budžet na 20k").filters
        self.assertEqual(("golf", 20000, None), (filters.search, filters.budget, filters.price_min))

    def test_a_real_model_with_noise_is_just_the_model(self):
        for message, search in (("hajde pokaži passat", "passat"), ("Hej bot, treba mi golf 6 dizel", "golf")):
            self.assertEqual(search, parse_question(message).filters.search, message)
        filters = parse_question("Hej bot, treba mi golf 6 dizel").filters
        self.assertEqual(("diesel", 2008, 2013), (filters.fuel, filters.year_min, filters.year_max))

    def test_plausible_names_keep_their_words_even_when_the_catalogue_lacks_them(self):
        for message, search in (("mg under 9000 KM", "mg"), ("merc do 15000 KM", "merc"),
                                ("lexus do 20000 KM", "lexus"), ("zastava do 3000 KM", "zastava"),
                                ("renault astre do 5000 KM", "renault astre"), ("dizel 2.0 do 15k KM", "2.0")):
            self.assertEqual(search, parse_question(message).filters.search, message)

    def test_without_a_filter_the_words_are_kept(self):
        self.assertEqual("blabla", parse_question("blabla").filters.search)
        self.assertEqual("limit", parse_question("limit").filters.search)

    def test_a_drive_type_next_to_a_model_is_kept_as_a_hint(self):
        self.assertEqual("passat 4x4", parse_question("passat 4x4 do 15k KM").filters.search)


class StatedFiltersTests(unittest.TestCase):
    def test_gemini_grounding_reads_the_same_bounds(self):
        filters = stated_filters("golf nikako preko 15k KM")
        self.assertEqual((15000, None), (filters.budget, filters.price_min))
        filters = stated_filters("bmw 320 do 15k KM")
        self.assertEqual((15000, None), (filters.budget, filters.price_min))
        filters = stated_filters("golf 2015 ili 2016")
        self.assertEqual((2015, 2016, None), (filters.year_min, filters.year_max, filters.budget))


if __name__ == "__main__":
    unittest.main()
