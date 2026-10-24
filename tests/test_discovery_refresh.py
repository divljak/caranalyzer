"""No-network coverage/queue regressions using actual saved observations."""
from copy import deepcopy
from datetime import timedelta
import json
import unittest
from unittest.mock import patch
from sqlalchemy.exc import SQLAlchemyError

from database.discovery_coverage import discovery_window
from database.collection_priority import select_priority
from database.website_tracking import WebsiteRun, WebsiteListingState
from scripts.collection_efficiency_report import summarize
from tests.test_collection_priority import NOW, car
from tests import test_website_sync as fixtures
from tests.test_website_sync import search_page
from scrapers.olx_website_collector import search_url


class WindowTests(unittest.TestCase):
    def baseline(self, age=24):
        return {'run_id': 7, 'finished_at': NOW-timedelta(hours=age), 'ids': [str(n) for n in range(10)]}

    def assess(self, pages, baseline=True, complete=True, terminal=False, rejected=0):
        return discovery_window([{'page': n+1, 'ids': ids} for n, ids in enumerate(pages)],
                                self.baseline() if baseline is True else baseline,
                                complete=complete, terminal=terminal, rejected=rejected, started_at=NOW)

    def test_one_familiar_ad_and_old_database_overlap_are_not_a_boundary(self):
        result = self.assess([['0','100','101','102','103'], ['104','105','106','107','108']])
        self.assertEqual('possible_gap', result['status'])
        self.assertEqual(1, result['prior_window_ids_seen'])
        self.assertEqual('unknown', result['full_market_coverage'])
        self.assertIsNone(result['missed_listing_count'])

    def test_two_substantial_overlapping_pages_are_a_signal_not_complete_coverage(self):
        result = self.assess([['0','1','2','3','4','100'], ['5','6','7','8','9','101']])
        self.assertEqual('overlap_observed', result['status'])
        self.assertTrue(result['overlap_signal'])
        self.assertEqual(12, result['distinct_saved_ids'])
        self.assertEqual('unknown', result['full_market_coverage'])

    def test_old_baseline_failures_and_rejections_do_not_prove_catchup(self):
        pages = [list(map(str, range(5))), list(map(str, range(5,10)))]
        self.assertEqual('possible_gap_stale_baseline', self.assess(pages, self.baseline(72))['status'])
        self.assertEqual('incomplete', self.assess(pages, complete=False)['status'])
        self.assertEqual('incomplete', self.assess(pages, rejected=1)['status'])
        self.assertEqual('baseline_unavailable', self.assess(pages, baseline=None)['status'])
        self.assertEqual('not_observed', self.assess([], baseline=None)['status'])

    def test_verified_empty_and_fully_exhausted_searches_need_no_overlap(self):
        self.assertEqual('current_results_exhausted', self.assess([[]], baseline=None, terminal=True)['status'])
        self.assertEqual('current_results_exhausted', self.assess([['100']], baseline=None, terminal=True)['status'])

    def test_repeated_source_ids_are_not_new_discoveries_or_extra_observations(self):
        result = self.assess([['0','1','2'],['1','2','3']])
        self.assertEqual(4, result['distinct_saved_ids'])
        self.assertEqual(4, result['prior_window_ids_seen'])


class RefreshTests(unittest.TestCase):
    def test_complete_recent_search_sighting_defers_availability_only(self):
        row = car(1, last_listed_at=NOW)
        before = deepcopy(row)
        plan = select_priority([row], NOW)
        self.assertEqual([], plan['selected_ids'])
        self.assertEqual({'recent_positive_sighting': 1}, plan['refresh_deferred'])
        self.assertEqual(before, row)
        self.assertEqual(['1'], select_priority([row], NOW+timedelta(days=7))['selected_ids'])

    def test_search_does_not_replace_missing_details_or_focused_refresh(self):
        rows = [car(1,last_listed_at=NOW,transmission=None),
                car(2,last_listed_at=NOW,candidate_status='inspect',details_checked_at=NOW-timedelta(days=2))]
        plan = select_priority(rows,NOW)
        self.assertEqual({'1','2'},set(plan['selected_ids']))
        self.assertEqual('candidate',plan['selected'][0]['priority_basis'])
        self.assertEqual('missing_attributes',plan['selected'][1]['check_need'])

    def test_missing_but_recently_checked_attribute_is_not_hammered_daily(self):
        rows = [car(1,transmission=None,details_checked_at=NOW-timedelta(days=1)),
                car(2,details_checked_at=NOW-timedelta(days=31))]
        plan = select_priority(rows,NOW)
        self.assertEqual(['2'],plan['selected_ids'])
        self.assertEqual({'recent_detail_check': 1},plan['refresh_deferred'])
        candidate=select_priority([car(3,candidate_status='inspect',details_checked_at=NOW)],NOW)
        self.assertEqual([],candidate['selected_ids'])
        self.assertEqual('saved_scopes_and_candidates',candidate['default_basis'])

    def test_new_followed_missing_details_are_prioritized_with_aging_preserved(self):
        rows = [car(1,transmission=None,first_seen_at=NOW-timedelta(days=1))]
        rows += [car(n) for n in range(2,8)]
        plan=select_priority(rows,NOW,4,searches=[{'make':'Volkswagen','model':'Golf','transmission':'automatic'}])
        self.assertEqual('1',plan['selected_ids'][0])
        self.assertEqual('new_followed_details',plan['selected'][0]['reason'])
        self.assertEqual(1,plan['oldest_slots'])

    def test_pending_removal_and_attempt_backoff_remain_authoritative(self):
        rows=[car(1,status='unavailable_pending',last_listed_at=NOW),
              car(2,status='unavailable_pending',next_check_at=NOW+timedelta(days=1)),
              car(3,status='source_reported_ended',last_success_at=NOW-timedelta(days=8))]
        plan=select_priority(rows,NOW)
        self.assertEqual(['1'],plan['selected_ids'])
        self.assertEqual('confirm_availability',plan['selected'][0]['check_need'])
        self.assertEqual({'unavailable_backoff':1},plan['refresh_deferred'])


class BaselineAndRunTests(unittest.TestCase):
    setUp=fixtures.StoreAndRunTests.setUp
    tearDown=fixtures.StoreAndRunTests.tearDown
    seed=fixtures.StoreAndRunTests.seed
    now=fixtures.StoreAndRunTests.now
    sleep=fixtures.StoreAndRunTests.sleep
    state=fixtures.StoreAndRunTests.state
    run_browser=fixtures.StoreAndRunTests.run_browser

    def test_baseline_uses_saved_search_rows_of_same_scope_not_detail_observations(self):
        report,_=self.run_browser(lambda *_:search_page(ids=('200001','200002')),detail_checks=0)
        self.assertEqual('baseline_unavailable',report['discovery_window']['status'])
        self.clock+=timedelta(days=1)
        baseline=self.store.discovery_baseline(search_url(),self.clock)
        self.assertEqual(['200001','200002'],baseline['ids'])
        self.assertIsNone(self.store.discovery_baseline(search_url(1,89),self.clock))
        with self.sessions() as s:
            run=s.get(WebsiteRun,baseline['run_id']);run.status='partial';s.commit()
        self.assertIsNotNone(self.store.discovery_baseline(search_url(),self.clock))
        with self.sessions() as s:
            run=s.get(WebsiteRun,baseline['run_id']);run.discovery_complete=0;s.commit()
        self.assertIsNone(self.store.discovery_baseline(search_url(),self.clock))

    def test_efficiency_uses_committed_rows_and_failure_does_not_advance_freshness(self):
        report,_=self.run_browser(lambda *_:search_page(ids=('200001','200002')),detail_checks=0)
        efficiency=report['request_efficiency']
        self.assertEqual(2,efficiency['saved_discovery_observations'])
        self.assertEqual(0,efficiency['saved_direct_observations'])
        self.assertEqual(2,efficiency['new_ids_per_search_visit'])
        with self.sessions() as s:
            row=s.get(WebsiteListingState,'200001')
            self.assertEqual(self.clock,row.last_listed_at)
            self.assertIsNone(row.details_checked_at)
            self.assertIsNone(row.last_success_at)

    def test_partial_state_failure_keeps_committed_discovery_in_efficiency_counts(self):
        with patch.object(self.store,'note_listed',side_effect=SQLAlchemyError('fixture failure')):
            report,_=self.run_browser(lambda *_:search_page(ids=('200001',)),detail_checks=0)
        self.assertEqual('partial',report['status'])
        self.assertEqual('incomplete',report['discovery_window']['status'])
        self.assertEqual(1,report['request_efficiency']['saved_discovery_observations'])
        self.assertEqual(0,report['request_efficiency']['saved_direct_observations'])


class MeasurementTests(unittest.TestCase):
    def run_row(self, day, instrumented=True, status='partial'):
        report={'discovery_window':{'version':'discovery_window_v1','status':'possible_gap'}} if instrumented else {}
        return {'id':day+10,'started_at':NOW-timedelta(days=day),'finished_at':NOW-timedelta(days=day),
                'status':status,'search_attempted':3,'detail_attempted':2,'details_completed':1,
                'observations':40,'new_listings':30,'report':json.dumps(report)}

    def test_missing_days_are_explicit_and_legacy_runs_are_unmeasured(self):
        result=summarize([self.run_row(0),self.run_row(2,False)],NOW)
        self.assertEqual(1,result['instrumented_calendar_days'])
        self.assertFalse(result['measurement_window_complete'])
        self.assertEqual(5,len(result['days_without_run_records']))
        today=result['days'][-1]
        self.assertEqual(10,today['new_ids_per_search_visit'])
        self.assertEqual(.5,today['detail_completion_fraction'])

    def test_seven_actual_days_required_and_running_counters_excluded(self):
        rows=[self.run_row(n) for n in range(7)]
        self.assertTrue(summarize(rows,NOW)['measurement_window_complete'])
        rows[0].update(finished_at=None,status='running',new_listings=0)
        result=summarize(rows,NOW)
        self.assertFalse(result['measurement_window_complete'])
        self.assertEqual(0,result['days'][-1]['finished_runs'])
