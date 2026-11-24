"""X ili Y compares: two generations of one model ("golf 6 ili 7") or two models, under shared filters.

Parser-only plus a fake connection for the answer: no Gemini, no Telegram, no database.
"""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

import telegram_bot
from telegram_intent import Filters, parse_question


def read(message):
    intent = parse_question(message)
    return intent.kind, intent.labels, intent.filters


class CompareParserTests(unittest.TestCase):
    def test_golf_6_ili_7_compares_two_generations_with_the_budget(self):
        kind, labels, filters = read("golf 6 ili 7 do 12000")
        self.assertEqual(("compare", ("golf 6", "golf 7")), (kind, labels))
        self.assertEqual(12000, filters.budget)  # unitless "do 12000" stays a price
        self.assertIsNone(filters.mileage_max)
        # the generations' years are not copied into the shared filters: each side keeps its own
        self.assertIsNone(filters.year_min)
        self.assertIsNone(filters.year_max)

    def test_shared_generation_wordings(self):
        for message, labels in (("golf 6 ili 7", ("golf 6", "golf 7")), ("golf 7 ili 6", ("golf 7", "golf 6")),
                                ("golf 6 ili golf 7", ("golf 6", "golf 7")), ("golf 6 vs 7", ("golf 6", "golf 7")),
                                ("golf 6 or 7", ("golf 6", "golf 7")), ("uporedi golf 6 i 7", ("golf 6", "golf 7")),
                                ("passat b6 ili b7", ("passat b6", "passat b7")),
                                ("octavia 2 ili 3", ("octavia 2", "octavia 3"))):
            kind, got, _ = read(message)
            self.assertEqual(("compare", labels), (kind, got), message)

    def test_two_models_with_a_km_budget(self):
        kind, labels, filters = read("octavia ili golf do 15000 KM")
        self.assertEqual(("compare", ("octavia", "golf")), (kind, labels))
        self.assertEqual((15000, None), (filters.budget, filters.mileage_max))

    def test_existing_wordings_still_compare(self):
        for message in ("golf ili passat do 15000", "golf vs astra", "golf versus astra", "golf naspram octavije",
                        "golf protiv octavije", "uporedi golf i astru", "compare golf and octavia"):
            self.assertEqual("compare", read(message)[0], message)

    def test_filters_are_shared_by_both_sides(self):
        _, labels, filters = read("golf 6 vs golf 7 do 12000 KM dizel")
        self.assertEqual(("golf 6", "golf 7"), labels)  # "dizel" is a filter, not part of the second model
        self.assertEqual((12000, "diesel"), (filters.budget, filters.fuel))
        _, _, filters = read("octavia 2 ili 3 od 2015")
        self.assertEqual(2015, filters.year_min)
        _, _, filters = read("golf ili passat dizel do 15000")
        self.assertEqual(("diesel", 15000), (filters.fuel, filters.budget))

    def test_km_case_rule_is_unchanged(self):
        _, labels, lower = read("golf 6 ili 7 do 120000 km")
        self.assertEqual(("golf 6", "golf 7"), labels)
        self.assertEqual((None, 120000), (lower.budget, lower.mileage_max))
        for message in ("golf 6 ili 7 do 12000 KM", "golf 6 ili 7 do 12000 Km", "golf 6 ili 7 do 12000 kM"):
            filters = read(message)[2]
            self.assertEqual((12000, None), (filters.budget, filters.mileage_max), message)

    def test_not_a_comparison(self):
        for message in ("dizel ili benzin", "golf 2015", "golf 7 do 12000", "golf ili nesto"):
            self.assertNotEqual("compare", parse_question(message).kind, message)
        self.assertEqual(2015, parse_question("golf 2015").filters.year_min)
        self.assertEqual(2015, parse_question("golf 2015").filters.year_max)

    def test_a_generation_the_model_does_not_have_is_not_shared(self):
        self.assertNotEqual(("golf 6", "golf 9"), read("golf 6 ili 9")[1])


class FakeConnection:
    """Answers ``_cohort_stats`` and ``_cohort_best`` per model pattern and records the parameters."""

    def __init__(self):
        self.calls = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, dict(params or {})))
        now = datetime.now(timezone.utc) - timedelta(hours=2)
        if "WITH side AS" in sql:
            rows = [{"listing_id": f"{params['pattern'][1:-1]}1", "year": 2012, "price": 9900, "mileage": 180000,
                     "last_verified_at": now}]
        elif "AS band" in sql:
            rows = [{"band": 0, "n": 5, "median": 10000}, {"band": 1, "n": 5, "median": 12000}]
        elif "percentile_disc" in sql:
            rows = [{"n": 40, "low": 2010, "high": 2018}]
        else:
            rows = [{"sample_size": 12, "priced_sample": 10, "median": 11000, "minimum": 9900, "maximum": 12000,
                     "newest": now}]
        return Result(rows)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class FakeEngine:
    def __init__(self):
        self.connection = FakeConnection()

    def begin(self):
        engine = self

        class Context:
            def __enter__(self):
                return engine.connection

            def __exit__(self, *exc):
                return False
        return Context()


class CompareAnswerTests(unittest.TestCase):
    def run_compare(self, message):
        intent = parse_question(message)
        engine = FakeEngine()
        with patch.object(telegram_bot, "_is_known_model", return_value=True), \
                patch.object(telegram_bot, "_statement_timeout"):
            text, used = telegram_bot.compare(engine, " | ".join(intent.labels), "bs", intent.filters.year_min,
                                              intent.filters.year_max, family=intent.filters.family,
                                              shared=intent.filters)
        return text, used, engine.connection.calls

    def test_golf_6_vs_7_answer_has_count_median_and_best_listings_under_the_budget(self):
        text, used, calls = self.run_compare("golf 6 ili 7 do 12000")
        self.assertEqual([("golf", 2008, 2013), ("golf", 2012, 2020)], used)  # each side in its own generation
        self.assertIn("golf 6 (2008–2013)", text)
        self.assertIn("golf 7 (2012–2020)", text)
        self.assertIn("Uzorak: 12 oglasa", text)
        self.assertIn("11.000 KM", text)  # the median
        self.assertEqual(2, text.count("https://olx.ba/artikal/"))  # best listings: one per side here
        self.assertIn("12.000 KM", text.splitlines()[1])  # the shared filter is stated
        for _, params in calls:
            self.assertEqual(12000, params["budget"])
        year_params = [(p["year_min"], p["year_max"]) for sql, p in calls if "WITH side AS" in sql]
        self.assertEqual([(2008, 2013), (2012, 2020)], year_params)

    def test_no_filters_means_no_shared_line_and_unbounded_queries(self):
        text, _, calls = self.run_compare("golf ili passat")
        self.assertNotIn("Isti filteri", text)
        bounded = [p for _, p in calls if "budget" in p]
        self.assertTrue(bounded and all(p["budget"] is None and p["fuel"] == "" for p in bounded))

    def test_octavia_ili_golf_with_km_budget(self):
        text, used, calls = self.run_compare("octavia ili golf do 15000 KM")
        self.assertEqual(["octavia", "golf"], [search for search, _, _ in used])
        bounded = [p for _, p in calls if "budget" in p]
        self.assertTrue(bounded and all(p["budget"] == 15000 and p["mileage_max"] is None for p in bounded))
        self.assertIn("15.000 KM", text)

    def test_the_compare_sql_applies_the_shared_bounds(self):
        _, _, calls = self.run_compare("golf 6 ili 7 do 12000 dizel")
        for sql, params in calls:
            if "FROM car_listings" in sql and "percentile_disc" not in sql:
                self.assertIn(":budget", sql)
                self.assertIn(":fuel", sql)
        self.assertTrue(any(p["fuel"] == "diesel" for _, p in calls))


if __name__ == "__main__":
    unittest.main()
