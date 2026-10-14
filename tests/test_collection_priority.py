"""No-network priority tests: limits, preferences, rotation and preserved evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.collection_priority import followed_match, select_priority, PriorityWebsiteStore
from database.models import Base
from database.website_tracking import WebsiteRun, WebsiteBudget, BudgetExhausted
from scripts.collection_coverage_report import model_coverage

NOW = datetime(2026, 10, 13, 18, 43)


def car(lid, **changes):
    row = dict(listing_id=str(lid), source='olx.ba', collection_method='olx_website', make='Volkswagen',
               model='Golf', year=2021, mileage=100000, price=25000, price_status='priced', fuel_type='diesel',
               transmission='automatic', category='hatchback', first_seen_at=NOW-timedelta(days=60),
               last_verified_at=NOW-timedelta(days=20), last_checked_at=None, last_success_at=None,
               next_check_at=None, status='listed', candidate_status=None)
    row['recognized_model'] = True
    row.update(changes)
    return row


class PriorityTests(unittest.TestCase):
    def test_capacity_dedup_exclusions_due_and_source_guards(self):
        rows = [car(1), car(1), car(2, next_check_at=NOW+timedelta(seconds=1)), car(3),
                car('abc'), car('١٢٣'), car(4, source='other'), car(5, collection_method='legacy_rendered')]
        plan = select_priority(rows, NOW, 20, excluded=['3'], used=18)
        self.assertEqual(['1'], plan['selected_ids'])
        self.assertEqual(1, plan['due_eligible'])
        self.assertEqual(2, plan['remaining_allowance'])

    def test_pause_and_exhausted_budgets_never_allocate(self):
        rows = [car(1)]
        for options in [dict(used=20), dict(used=21), dict(pause_until=NOW+timedelta(days=1)), dict(limit=0)]:
            self.assertEqual([], select_priority(rows, NOW, **options)['selected'])
        self.assertEqual(['1'], select_priority(rows, NOW, pause_until=NOW)['selected_ids'])
        for options in [dict(limit=21), dict(used=-1), dict(limit=1.5)]:
            with self.assertRaises(ValueError):
                select_priority(rows, NOW, **options)

    def test_candidate_and_pending_get_priority_without_starving_oldest(self):
        rows = [car(1, first_seen_at=NOW-timedelta(days=200)), car(2, status='unavailable_pending'),
                car(3, candidate_status='inspect'), car(4, candidate_status='shortlisted'),
                car(5, candidate_status='passed')]
        plan = select_priority(rows, NOW, 4)
        self.assertEqual(['3', '4', '2', '1'], plan['selected_ids'])
        self.assertEqual('oldest_attempt', plan['selected'][-1]['reason'])
        self.assertEqual(1, plan['oldest_slots'])

    def test_saved_scope_matches_known_attributes_and_labels_unknowns(self):
        filters = dict(make='volkswagen', model='Golf', year_min=2020, year_max=2023, fuel='diesel',
                       transmission='automatic', mileage_min=50000, mileage_max=150000, price_max=30000, days=30)
        self.assertEqual('followed_scope', followed_match(car(1), filters))
        self.assertEqual('followed_unknown_attributes', followed_match(car(2, transmission=None), filters))
        for change in [dict(model='Polo'), dict(year=2019), dict(mileage=200000),
                       dict(transmission='manual'), dict(price=40000)]:
            self.assertIsNone(followed_match(car(1, **change), filters))
        self.assertEqual('followed_scope', followed_match(car(1, last_verified_at=NOW-timedelta(days=100)), filters))
        rows = [car(1), car(2, transmission=None), car(3, transmission='manual'), car(4, model='Polo')]
        plan = select_priority(rows, NOW, 4, searches=[filters])
        self.assertEqual(['followed_scope', 'followed_unknown_attributes'],
                         [r['reason'] for r in plan['selected'][:2]])
        self.assertEqual('attributes_missing', plan['selected'][1]['match_confidence'])

    def test_unknown_category_and_missing_price_are_honest(self):
        filters = dict(make='Volkswagen', model='Golf', category='unknown')
        self.assertEqual('followed_scope', followed_match(car(1, category=None), filters))
        self.assertIsNone(followed_match(car(1), filters))
        filters = dict(make='Volkswagen', model='Golf', price_max=30000)
        self.assertEqual('followed_unknown_attributes', followed_match(car(1, price=0, price_status='missing'), filters))

    def test_model_rotation_prevents_largest_model_consuming_whole_lane(self):
        rows = [car(n) for n in range(1, 100)] + [car(100, model='Polo'), car(101, make='Audi', model='A4')]
        plan = select_priority(rows, NOW, 4)
        self.assertEqual(3, len({(r['make'], r['model']) for r in plan['selected'][:3]}))
        tomorrow = select_priority(rows, NOW+timedelta(days=1), 4)
        self.assertNotEqual(plan['selected'][0]['model'], tomorrow['selected'][0]['model'])

    def test_old_attempts_advance_after_failures_and_search_sightings_do_not_reset_age(self):
        rows = [car(n, candidate_status='inspect', first_seen_at=NOW-timedelta(days=1)) for n in range(1, 40)]
        rows += [car(100, first_seen_at=NOW-timedelta(days=300), last_verified_at=NOW)]
        first = select_priority(rows, NOW, 4)
        self.assertIn('100', first['selected_ids'])
        for row in rows:
            if row['listing_id'] in first['selected_ids']:
                row['last_checked_at'] = NOW  # check_failed also advances this clock
                row['next_check_at'] = NOW+timedelta(days=1)
        second = select_priority(rows, NOW+timedelta(days=1), 4)
        self.assertNotIn('100', second['selected_ids'])
        self.assertEqual(None, rows[-1]['last_success_at'])

    def test_starvation_protected_under_continuing_priority_arrivals(self):
        rows = [car(n, first_seen_at=NOW-timedelta(days=100)) for n in range(1, 21)]
        seen = set()
        for day in range(20):
            now = NOW+timedelta(days=day)
            rows += [car(1000+day*10+n, candidate_status='inspect', first_seen_at=now) for n in range(10)]
            plan = select_priority(rows, now, 4)
            seen.update(plan['selected_ids'])
            for row in rows:
                if row['listing_id'] in plan['selected_ids']:
                    row['last_checked_at'] = now
                    row['next_check_at'] = now+timedelta(days=1)
        self.assertTrue({str(n) for n in range(1, 21)} <= seen)

    def test_shuffle_duplicate_latest_state_and_timezone_are_deterministic(self):
        import random
        rows = [car(n, model=f'Model {n%3}') for n in range(1, 35)]
        rows += [car(1, next_check_at=NOW+timedelta(days=1), last_checked_at=NOW)]
        plan = select_priority(rows, NOW, 20)
        random.Random(42).shuffle(rows)
        aware = NOW.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=2)))
        self.assertEqual(plan, select_priority(rows, aware, 20))
        self.assertNotIn('1', plan['selected_ids'])
        before = deepcopy(rows)
        select_priority(rows, NOW, 20)
        self.assertEqual(before, rows)
        self.assertEqual(5, plan['oldest_slots'])

    def test_fair_default_does_not_invent_buying_scope_or_sales(self):
        plan = select_priority([car(1)], NOW)
        self.assertEqual('all_existing_tracked_models', plan['default_basis'])
        self.assertEqual('unknown', plan['source_coverage'])
        self.assertIsNone(plan['confirmed_sales'])
        self.assertEqual('bounded_known_listing_check_queue', plan['sample_label'])

    def test_small_plan_still_reserves_one_oldest_slot(self):
        rows = [car(1, first_seen_at=NOW-timedelta(days=300)), car(2, candidate_status='inspect')]
        self.assertEqual(['1'], select_priority(rows, NOW, 1)['selected_ids'])
        self.assertEqual(['2', '1'], select_priority(rows, NOW, 20, used=18)['selected_ids'])
        self.assertEqual(['1'], select_priority(rows, NOW, 20, max_selected=1)['selected_ids'])

    def test_model_report_does_not_count_failed_attempt_as_success(self):
        rows = [car(1, last_checked_at=NOW), car(2, last_success_at=NOW), car(3, model=None, price=0)]
        report = model_coverage(rows, NOW)
        self.assertEqual('small_retained_sample', report[0]['sample_label'])
        self.assertEqual(1, report[0]['successfully_checked_7d'])
        self.assertEqual('unknown_model', report[1]['sample_label'])

    def test_unrecognized_title_fragments_share_one_fairness_pool(self):
        rows = [car(n, model=f'Unparsed {n}', recognized_model=False) for n in range(1, 100)]
        rows += [car(100, model='Golf'), car(101, model='Polo')]
        plan = select_priority(rows, NOW, 4)
        self.assertEqual(1, sum(r['listing_id'] not in {'100','101'} for r in plan['selected'][:3]))


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.store = PriorityWebsiteStore(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_selection_uses_callers_pinned_connection_and_records_serializable_plan(self):
        import json
        with self.engine.connect() as connection:
            self.store.sessions = sessionmaker(bind=connection)
            run = self.store.start_run('unit-priority', NOW, 'no-network-test', 3, 20)
            plan = select_priority([car(1, last_checked_at=NOW-timedelta(days=30))], NOW, 20, used=18)
            with patch('database.collection_priority.database_plan', return_value=plan) as select:
                self.assertEqual(['1'], self.store.candidates(NOW, 2, excluded=['9']))
                self.assertIs(connection, select.call_args.args[0])
                self.assertEqual({'excluded': ['9'], 'max_selected': 2}, select.call_args.kwargs)
            report = dict(status='partial', search_attempted=0, detail_attempted=0, discovery_complete=0,
                          details_completed=0, observations=0, new_listings=0, errors=0)
            self.store.finish_run(run, NOW, report)
            with self.store.sessions() as session:
                saved = json.loads(session.get(WebsiteRun, run).report)
                self.assertEqual(plan, saved['priority_selection'])
                self.assertEqual(0, saved['detail_attempted'])
                self.assertIsNone(session.get(WebsiteBudget, 1))
            self.store.start_run('unit-priority-paused', NOW, 'no-network-test', 3, 20)
            self.assertIsNone(self.store.last_priority_plan)

    def test_adapter_inherits_daily_budget_failure_and_pause_protection(self):
        for _ in range(20):
            self.store.reserve('detail', NOW, 3, 20, 15)
        with self.assertRaises(BudgetExhausted):
            self.store.reserve('detail', NOW, 3, 20, 15)
        self.assertEqual(20, self.store.budget(NOW)['detail_used'])


if __name__ == '__main__':
    unittest.main()
