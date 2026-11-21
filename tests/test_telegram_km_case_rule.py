"""The km / KM case rule (Ogi, 21 Nov 2026).

Lower-case "km" is mileage (kilometres). Any form with an upper-case K ("KM", and also "Km" / "kM") is the
currency (konvertibilna marka). Cyrillic follows the same rule (км -> km, КМ -> KM). Parser-only.
"""

import unittest

from telegram_intent import Filters, Intent, parse_question


def filters(message, context=None):
    return parse_question(message, context).filters


class KmCaseRuleTests(unittest.TestCase):
    def test_uppercase_km_is_a_price_cap_and_lowercase_is_mileage_with_a_model(self):
        price = filters("audi a4 do 25 000 KM")
        self.assertEqual(("audi a4", 25000, None), (price.search, price.budget, price.mileage_max))
        mileage = filters("audi a4 do 25 000 km")
        self.assertEqual(("audi a4", None, 25000), (mileage.search, mileage.budget, mileage.mileage_max))

    def test_glued_units(self):
        for message, budget, mileage in (("audi a4 do 25000KM", 25000, None), ("audi a4 do 25000km", None, 25000),
                                         ("audi a4 do 25.000KM", 25000, None), ("audi a4 do 25.000km", None, 25000)):
            f = filters(message)
            self.assertEqual((budget, mileage), (f.budget, f.mileage_max), message)

    def test_thousand_words(self):
        for message in ("golf do 150 hiljada km", "golf do 150 tisuća km"):
            self.assertEqual(150000, filters(message).mileage_max, message)
            self.assertIsNone(filters(message).budget, message)
        for message in ("golf do 15 hiljada KM", "golf do 15 tisuća KM"):
            self.assertEqual(15000, filters(message).budget, message)

    def test_mixed_case_is_currency_and_never_asks(self):
        for message in ("audi a4 do 25 000 Km", "audi a4 do 25 000 kM", "auto do 30 000 Km", "auto do 30000kM"):
            intent = parse_question(message)
            self.assertNotEqual("clarify", intent.kind, message)
            self.assertIsNone(intent.filters.mileage_max, message)
            self.assertIn(intent.filters.budget, (25000, 30000), message)

    def test_mixed_case_in_a_range_is_a_price_range(self):
        f = filters("golf 50.000 Km do 60.000 Km")
        self.assertEqual((50000, 60000, None), (f.price_min, f.budget, f.mileage_max))

    def test_cyrillic(self):
        self.assertEqual(25000, filters("ауди а4 до 25 000 км").mileage_max)
        price = filters("ауди а4 до 25 000 КМ")
        self.assertEqual((25000, None), (price.budget, price.mileage_max))

    def test_no_model_lowercase_km_is_mileage_without_asking(self):
        intent = parse_question("Auto do 30 000km")
        self.assertNotEqual("clarify", intent.kind)
        self.assertEqual((30000, None), (intent.filters.mileage_max, intent.filters.budget))

    def test_currency_words_still_read_as_price(self):
        for message in ("golf do 15000 maraka", "golf do 15000 BAM", "golf do 15000 KAM"):
            self.assertEqual(15000, filters(message).budget, message)

    def test_a_price_correction_with_lowercase_km_stays_mileage(self):
        previous = Intent("find", Filters("golf", budget=20000))
        corrected = parse_question("ne, do 100.000 km", previous)
        self.assertEqual(100000, corrected.filters.mileage_max)
        again = parse_question("ne, do 30000 KM", previous)
        self.assertEqual((30000, None), (again.filters.budget, again.filters.mileage_max))
        ranged = parse_question("50.000km do 60.000km", Intent("find", Filters(budget=20000)))
        self.assertEqual((None, 60000), (ranged.filters.price_min, ranged.filters.mileage_max))


if __name__ == "__main__":
    unittest.main()
