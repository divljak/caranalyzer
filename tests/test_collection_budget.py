"""Budget planning for the twice-daily collection: pure logic, no database, no network."""
import unittest

from database.collection_budget import (HARD_MAX_DETAIL_PER_DAY, HARD_MAX_SEARCH_PER_DAY, config_from_env,
                                        normalize_slot, plan_run)
from scripts.github_collector_verification import ConfigurationError, budget_settings

CFG = config_from_env({})


def used(search=0, detail=0):
    return {'search_used': search, 'detail_used': detail}


class DefaultsTests(unittest.TestCase):
    def test_defaults_are_the_approved_plan(self):
        self.assertEqual((3, 6, 20, 10), (CFG['search_per_run'], CFG['search_day_cap'],
                                          CFG['detail_day_cap'], CFG['detail_morning']))
        self.assertEqual((10, 50), (HARD_MAX_SEARCH_PER_DAY, HARD_MAX_DETAIL_PER_DAY))

    def test_env_can_change_budgets_but_never_past_the_hard_maximum(self):
        self.assertEqual(8, config_from_env({'COLLECTOR_SEARCH_DAY_CAP': '8'})['search_day_cap'])
        for bad in ({'COLLECTOR_SEARCH_DAY_CAP': '11'}, {'COLLECTOR_DETAIL_DAY_CAP': '51'},
                    {'COLLECTOR_SEARCH_PAGES_PER_RUN': '7'},  # above the default day cap of 6
                    {'COLLECTOR_DETAIL_MORNING': '21'}, {'COLLECTOR_SEARCH_DAY_CAP': 'lots'},
                    {'COLLECTOR_SEARCH_DAY_CAP': '-1'}):
            with self.assertRaises(ValueError, msg=bad):
                config_from_env(bad)


class PlanTests(unittest.TestCase):
    def test_fresh_day_morning_and_evening(self):
        morning = plan_run('morning', used(), CFG)
        self.assertEqual((3, 10, False), (morning['search_pages'], morning['detail_checks'], morning['exhausted']))
        evening = plan_run('evening', used(3, 10), CFG)
        self.assertEqual((3, 10), (evening['search_pages'], evening['detail_checks']))

    def test_failed_morning_does_not_starve_the_evening_of_search_pages(self):
        evening = plan_run('evening', used(search=1, detail=0), CFG)  # morning died after one page
        self.assertEqual(3, evening['search_pages'])
        self.assertEqual(20, evening['detail_checks'])  # unused morning details carry over

    def test_morning_retry_costs_the_evening_at_most_one_page(self):
        self.assertEqual(2, plan_run('evening', used(search=4), CFG)['search_pages'])

    def test_morning_never_takes_the_evenings_detail_share(self):
        self.assertEqual(10, plan_run('morning', used(), CFG)['detail_checks'])
        self.assertEqual(4, plan_run('morning', used(detail=16), CFG)['detail_checks'])

    def test_day_caps_hold_for_extra_and_auto_runs(self):
        extra = plan_run('auto', used(6, 20), CFG)
        self.assertEqual((0, 0, True), (extra['search_pages'], extra['detail_checks'], extra['exhausted']))
        partial = plan_run('auto', used(5, 18), CFG)
        self.assertEqual((1, 2), (partial['search_pages'], partial['detail_checks']))

    def test_pages_exhausted_but_details_left_still_runs(self):
        plan = plan_run('evening', used(6, 10), CFG)
        self.assertEqual((0, 10, False), (plan['search_pages'], plan['detail_checks'], plan['exhausted']))

    def test_over_budget_usage_never_goes_negative(self):
        plan = plan_run('evening', used(9, 40), CFG)
        self.assertEqual((0, 0, True), (plan['search_pages'], plan['detail_checks'], plan['exhausted']))

    def test_slot_names(self):
        self.assertEqual(['auto', 'auto', 'morning', 'evening'], [normalize_slot(v) for v in (None, 'AUTO', 'Morning', ' evening ')])
        with self.assertRaises(ValueError):
            normalize_slot('noon')


class WrapperForwardingTests(unittest.TestCase):
    def test_only_valid_slot_and_whole_numbers_are_forwarded(self):
        self.assertEqual({'COLLECTOR_SLOT': 'evening', 'COLLECTOR_SEARCH_DAY_CAP': '6'},
                         budget_settings({'COLLECTOR_SLOT': 'Evening', 'COLLECTOR_SEARCH_DAY_CAP': '6', 'OTHER': 'x'}))
        self.assertEqual({}, budget_settings({}))
        for bad in ({'COLLECTOR_SLOT': 'noon'}, {'COLLECTOR_DETAIL_DAY_CAP': '2; rm'}):
            with self.assertRaises(ConfigurationError):
                budget_settings(bad)


if __name__ == '__main__':
    unittest.main()
