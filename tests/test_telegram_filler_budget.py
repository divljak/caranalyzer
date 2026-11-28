"""Production bugs of 28 Nov 2026: filler words became a model ("platformi prelazi", "hajde uzmemo obzir") and
"cijena ne prelazi preko 30k KM" was read as a price floor. Parser-only: no Gemini, Telegram or database."""

import unittest

import telegram_bot
from telegram_intent import parse_question, looks_like_followup

BEST_SELLING = "Koja auta se najbolje prodaju na platformi a da cijena ne prelazi preko 30k KM"
RANGE_ONLY = "Ok hajde da uzmemo u obzir od 20.000KM do 30.000 KM"


class Chat:
    """A private chat through the bot's real follow-up memory."""

    def __init__(self, key=(901, 901)):
        for store in (telegram_bot._SCOPES, telegram_bot._BUDGETS, telegram_bot._HISTORY, telegram_bot._LANGS,
                      telegram_bot._ACTIVE):
            store.clear()
        self.key = key

    def say(self, message):
        intent = telegram_bot._understand(message, {}, self.key, None, chat_kind="private")
        telegram_bot._remember_scope(self.key, intent)
        return intent


class PriceCapTests(unittest.TestCase):
    def test_production_message_is_a_cap_with_no_model_and_routes_to_most_listed(self):
        intent = parse_question(BEST_SELLING)
        self.assertEqual("suggest", intent.kind)
        self.assertEqual("", intent.filters.search)
        self.assertEqual(30000, intent.filters.budget)
        self.assertIsNone(intent.filters.price_min)

    def test_upper_bound_phrases_are_never_a_floor(self):
        for message in ("koja auta se najbolje prodaju ne prelazi 30k KM",
                        "koja auta se najbolje prodaju da cijena ne prelazi preko 30k KM",
                        "koja auta se najbolje prodaju cijena ne ide preko 30.000 KM",
                        "koja auta se najbolje prodaju ispod 30.000 KM",
                        "koja auta se najbolje prodaju do 30k KM",
                        "koja auta se najbolje prodaju max 30k KM"):
            intent = parse_question(message)
            self.assertEqual(("suggest", "", 30000, None),
                             (intent.kind, intent.filters.search, intent.filters.budget, intent.filters.price_min),
                             message)

    def test_a_bare_cap_is_still_a_cap(self):
        for message in ("ne prelazi 30k KM", "do 30k KM", "ispod 30.000 KM"):
            intent = parse_question(message)
            self.assertEqual((30000, None, ""), (intent.filters.budget, intent.filters.price_min,
                                                 intent.filters.search), message)

    def test_preko_alone_is_still_a_floor(self):
        filters = parse_question("golf preko 30k KM").filters
        self.assertEqual(("golf", 30000, None), (filters.search, filters.price_min, filters.budget))


class FillerWordTests(unittest.TestCase):
    def test_filler_never_becomes_a_model(self):
        for message in (RANGE_ONLY,
                        "hajde uzmemo u obzir do 25.000 KM",
                        "ok hajde da uzmemo u obzir do 25.000 KM",
                        "koja auta se najbolje prodaju na platformi do 25.000 KM",
                        "najbolje se prodaju auta, cijena ne prelazi 25.000 KM"):
            self.assertEqual("", parse_question(message).filters.search, message)

    def test_unknown_words_in_a_sentence_are_not_a_model(self):
        filters = parse_question("ok hajde pokazi mi nesto zanimljivo tipa blabla xyzqw do 20000 KM").filters
        self.assertEqual(("", 20000), (filters.search, filters.budget))

    def test_a_fuzzy_match_to_a_real_model_is_kept(self):
        filters = parse_question("ok hajde da uzmemo u obzir pasat do 20000 KM").filters
        self.assertEqual(("passat", 20000), (filters.search, filters.budget))

    def test_short_messages_keep_their_words_for_the_database(self):
        self.assertEqual("mg", parse_question("mg under 9000 KM").filters.search)
        self.assertEqual("merc", parse_question("merc do 15000 KM").filters.search)


class RangeFollowUpTests(unittest.TestCase):
    def test_range_only_message_reads_as_a_follow_up(self):
        self.assertTrue(looks_like_followup(RANGE_ONLY))

    def test_follow_up_keeps_most_listed_and_applies_the_new_range(self):
        chat = Chat()
        first = chat.say(BEST_SELLING)
        self.assertEqual(("suggest", 30000), (first.kind, first.filters.budget))
        second = chat.say(RANGE_ONLY)
        self.assertEqual(("suggest", "", 20000, 30000),
                         (second.kind, second.filters.search, second.filters.price_min, second.filters.budget))

    def test_follow_up_changes_only_the_range(self):
        chat = Chat()
        chat.say("najbolje se prodaju auta do 30k KM")
        second = chat.say("hajde da razmotrimo od 10.000 KM do 15.000 KM")
        self.assertEqual(("suggest", "", 10000, 15000),
                         (second.kind, second.filters.search, second.filters.price_min, second.filters.budget))

    def test_without_state_the_range_still_answers_most_listed(self):
        intent = Chat().say(RANGE_ONLY)
        self.assertEqual(("suggest", "", 20000, 30000),
                         (intent.kind, intent.filters.search, intent.filters.price_min, intent.filters.budget))


class ExistingRulesTests(unittest.TestCase):
    def test_km_case_rule(self):
        mileage = parse_question("golf do 200000 km").filters
        price = parse_question("golf do 30000 KM").filters
        self.assertEqual((200000, None), (mileage.mileage_max, mileage.budget))
        self.assertEqual((None, 30000), (price.mileage_max, price.budget))

    def test_golf_20_still_asks_and_golf_2015_is_exact(self):
        self.assertEqual("clarify", parse_question("golf 20").kind)
        filters = parse_question("Golf 2015").filters
        self.assertEqual((2015, 2015), (filters.year_min, filters.year_max))

    def test_ili_comparison_and_unitless_do_still_work(self):
        self.assertEqual("compare", parse_question("golf ili passat do 12000 KM").kind)
        self.assertEqual(12000, parse_question("golf do 12000").filters.budget)


if __name__ == "__main__":
    unittest.main()
