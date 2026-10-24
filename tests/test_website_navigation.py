"""Reproduce renderer/DOM failures without visiting OLX or writing evidence."""
import json
import unittest
from unittest.mock import Mock, patch
from selenium.common.exceptions import TimeoutException, WebDriverException
from scrapers.olx_website_collector import (
    WebsiteBlocked, WebsiteBrowser, WebsiteDOMTimeout, WebsiteReadError, parse_detail)
from tests.test_website_sync import detail_page, absent_page, search_page


def response(status, url='https://olx.ba/artikal/79589391', kind='Document'):
    return {'message': json.dumps({'message': {'method': 'Network.responseReceived',
        'params': {'type': kind, 'response': {'url': url, 'status': status}}}})}


class NavigationTests(unittest.TestCase):
    def browser(self, pages):
        browser = WebsiteBrowser()
        browser.driver = Mock()
        browser.driver.get_log.return_value = []
        browser.driver.execute_async_script.side_effect = pages
        return browser

    def test_navigation_timeout_can_use_ready_dom_without_another_visit(self):
        page = detail_page()
        browser = self.browser([page])
        browser.driver.get.side_effect = TimeoutException()
        result = browser.read(page['url'], 'detail')
        self.assertEqual('listed', parse_detail(result, '79589391')['outcome'])
        self.assertTrue(result['browser']['navigation_timeout'])
        browser.driver.get.assert_called_once_with(page['url'])

    def test_id_renders_before_price_and_vehicle_labels(self):
        partial = detail_page()
        partial['prices'] = []
        partial['attributes'] = {'ID': '79589391'}
        browser = self.browser([partial, detail_page()])
        result = browser.read(partial['url'], 'detail')
        self.assertEqual('listed', parse_detail(result, '79589391')['outcome'])
        self.assertEqual(2, browser.driver.execute_async_script.call_count)
        browser.driver.get.assert_called_once()

    def test_search_waits_for_pagination_on_the_same_reserved_visit(self):
        partial = search_page()
        partial.update(current_page=None, last_page=None)
        browser = self.browser([partial, search_page()])
        page = browser.read(partial['url'], 'search')
        self.assertEqual(16, page['last_page'])
        self.assertEqual(2, browser.driver.execute_async_script.call_count)
        browser.driver.get.assert_called_once_with(partial['url'])

    def test_search_heading_without_loaded_cards_is_not_ready(self):
        partial = search_page()
        partial['cards'] = []
        browser = self.browser([partial])
        browser.DOM_TIMEOUT = 0
        with self.assertRaises(WebsiteDOMTimeout):
            browser.read(partial['url'], 'search')
        browser.driver.get.assert_called_once()

    def test_transient_script_timeouts_poll_existing_dom_only(self):
        browser = self.browser([TimeoutException(), TimeoutException(), detail_page()])
        result = browser.read(detail_page()['url'], 'detail')
        self.assertEqual(2, result['browser']['script_retries'])
        browser.driver.get.assert_called_once()

    def test_repeated_renderer_timeout_stops_after_two_dom_retries(self):
        browser = self.browser([TimeoutException()] * 3)
        with self.assertRaises(WebsiteDOMTimeout):
            browser.read(detail_page()['url'], 'detail')
        self.assertEqual(3, browser.driver.execute_async_script.call_count)
        browser.driver.get.assert_called_once()

    def test_wrong_identity_never_returns_stale_previous_page(self):
        wrong = detail_page('11111111')
        browser = self.browser([wrong] * 2)
        browser.DOM_TIMEOUT = 0
        with self.assertRaises(WebsiteDOMTimeout):
            browser.read(detail_page()['url'], 'detail')
        browser.driver.get.assert_called_once()

    def test_incomplete_dom_at_deadline_is_failure_not_unavailable(self):
        partial = detail_page()
        partial['prices'] = []
        browser = self.browser([partial])
        browser.DOM_TIMEOUT = 0
        with self.assertRaises(WebsiteDOMTimeout) as caught:
            browser.read(partial['url'], 'detail')
        diagnostic = caught.exception.diagnostics
        self.assertEqual('price_not_verified', diagnostic['parser_reason'])
        self.assertTrue(diagnostic['last_dom']['expected_id_matched'])
        self.assertEqual(0, diagnostic['last_dom']['price_values'])
        self.assertNotIn('attributes', diagnostic['last_dom'])

    def test_negative_template_needs_two_consistent_reads(self):
        browser = self.browser([absent_page(), absent_page()])
        result = browser.read(absent_page()['url'], 'detail')
        self.assertEqual('unavailable', parse_detail(result, '79589391')['outcome'])
        self.assertEqual(2, browser.driver.execute_async_script.call_count)

    def test_hydrating_negative_heading_does_not_beat_active_ad(self):
        browser = self.browser([absent_page(), detail_page()])
        result = browser.read(detail_page()['url'], 'detail')
        self.assertEqual('listed', parse_detail(result, '79589391')['outcome'])

    def test_refusal_after_navigation_timeout_still_stops(self):
        browser = self.browser([])
        browser.driver.get.side_effect = TimeoutException()
        browser.driver.get_log.side_effect = [[], [response(403)]]
        with self.assertRaises(WebsiteBlocked):
            browser.read(detail_page()['url'], 'detail')
        browser.driver.execute_async_script.assert_not_called()

    def test_refusal_during_script_retry_still_stops(self):
        browser = self.browser([TimeoutException()])
        browser.driver.get_log.side_effect = [[], [], [], [response(429)]]
        with self.assertRaises(WebsiteBlocked):
            browser.read(detail_page()['url'], 'detail')
        self.assertEqual(1, browser.driver.execute_async_script.call_count)

    def test_last_response_refusal_beats_ready_dom(self):
        browser = self.browser([detail_page()])
        browser.driver.get_log.side_effect = [[], [], [], [response(429)]]
        with self.assertRaises(WebsiteBlocked):
            browser.read(detail_page()['url'], 'detail')

    def test_challenge_dom_and_http_batch_refusal_take_priority(self):
        page = detail_page(); page['blocked'] = True
        browser = self.browser([page])
        with self.assertRaises(WebsiteBlocked):
            browser.read(page['url'], 'detail')
        browser.driver.get_log.return_value = [response(503), response(429, 'https://api.olx.ba/listings/1', 'XHR')]
        with self.assertRaises(WebsiteBlocked):
            browser.check_responses()

    def test_server_error_or_transport_failure_cannot_return_listing(self):
        browser = self.browser([detail_page()])
        browser.driver.get_log.side_effect = [[], [response(503)], []]
        with self.assertRaises(WebsiteReadError):
            browser.read(detail_page()['url'], 'detail')
        browser = self.browser([])
        browser.driver.get.side_effect = WebDriverException('transport failure')
        with self.assertRaises(WebDriverException):
            browser.read(detail_page()['url'], 'detail')
        browser.driver.execute_async_script.assert_not_called()

    def test_unrelated_asset_error_does_not_classify_ad(self):
        browser = self.browser([detail_page()])
        browser.driver.get_log.return_value = [response(503, 'https://olx.ba/asset.js', 'Script'),
                                             response(403, 'https://unrelated.invalid/pixel', 'Image')]
        self.assertEqual('listed', parse_detail(browser.read(detail_page()['url'], 'detail'), '79589391')['outcome'])

    def test_detail_origin_conflicting_status_and_seller_title_remain_conservative(self):
        for url in ['http://olx.ba/artikal/79589391', '/artikal/79589391', 'https://other.invalid/artikal/79589391']:
            page = detail_page(); page['url'] = url
            self.assertEqual('check_failed', parse_detail(page, '79589391')['outcome'])
        page = absent_page(); page['headings'].append('Oglas je prodan')
        self.assertEqual('check_failed', parse_detail(page, '79589391')['outcome'])
        page = detail_page(); page['main_title'] = 'Oglas je prodan'; page['headings'] = ['Oglas je prodan']
        self.assertEqual('listed', parse_detail(page, '79589391')['outcome'])

    def test_cleanup_stops_service_when_quit_or_setup_fails(self):
        browser = self.browser([])
        driver = browser.driver
        browser.service = Mock()
        driver.quit.side_effect = WebDriverException()
        browser.__exit__(WebsiteBlocked, WebsiteBlocked(), None)
        browser.service.stop.assert_called_once()
        self.assertIsNone(browser.driver)
        with patch('scrapers.olx_website_collector.shutil.which', return_value='/driver'), \
             patch('scrapers.olx_website_collector.Service') as service, \
             patch('scrapers.olx_website_collector.webdriver.Chrome') as chrome:
            chrome.return_value.set_script_timeout.side_effect = WebDriverException()
            with self.assertRaises(WebDriverException):
                WebsiteBrowser().__enter__()
            chrome.return_value.quit.assert_called_once()
            service.return_value.stop.assert_called_once()

    def test_cleanup_does_not_suppress_original_error(self):
        browser = self.browser([])
        driver = browser.driver; driver.quit.side_effect = WebDriverException()
        browser.service = Mock()
        with self.assertRaises(WebsiteBlocked):
            try:
                raise WebsiteBlocked()
            finally:
                browser.__exit__(WebsiteBlocked, None, None)


class BrowserBudgetTests(unittest.TestCase):
    # Reuse the existing isolated database fixture, never the collector database.
    from tests.test_website_sync import StoreAndRunTests as Fixture
    setUp = Fixture.setUp
    tearDown = Fixture.tearDown
    seed = Fixture.seed
    now = Fixture.now
    sleep = Fixture.sleep
    state = Fixture.state

    def run_detail_browser(self, pages):
        from run_website_sync import sync
        browser = WebsiteBrowser()
        browser.driver = Mock()
        browser.driver.get_log.return_value = []
        browser.driver.execute_async_script.side_effect = pages
        self.store.reserve('search', self.clock, 1, 20, 15)
        report = sync(self.db, self.store, browser, search_pages=1, detail_checks=20,
                      now=self.now, sleep=self.sleep)
        return report, browser

    def test_recovered_dom_uses_one_persistent_visit_and_appends_one_observation(self):
        from database.models import ListingSnapshot, ScrapingLog
        from database.website_tracking import WebsiteListingCheck
        report, browser = self.run_detail_browser([TimeoutException(), detail_page()])
        self.assertEqual(1, self.store.budget(self.clock)['detail_used'])
        self.assertEqual((1, 1, 1), (report['detail_attempted'], report['details_completed'], report['observations']))
        self.assertEqual('partial', report['status'])
        browser.driver.get.assert_called_once()
        with self.sessions() as session:
            self.assertEqual(2, session.query(ListingSnapshot).count())
            check = session.query(WebsiteListingCheck).one()
            self.assertEqual(1, json.loads(check.evidence)['browser']['script_retries'])
            self.assertEqual(0, session.query(ScrapingLog).filter_by(status='completed').count())

    def test_renderer_failures_consume_two_visits_stop_and_preserve_prior_state(self):
        from database.models import CarListing, ListingSnapshot, ScrapingLog
        from database.website_tracking import WebsiteListingCheck
        self.seed('101'); self.seed('102')
        self.store.record_check(self.run, '79589391', self.clock, parse_detail(detail_page(), '79589391'))
        report, browser = self.run_detail_browser([TimeoutException()] * 6)
        self.assertEqual(2, self.store.budget(self.clock)['detail_used'])
        self.assertEqual((2, 0), (report['detail_attempted'], report['details_completed']))
        self.assertEqual(2, browser.driver.get.call_count)
        self.assertIn('Two consecutive detail failures', report['stopped_reason'])
        self.assertEqual('listed', self.state().status)
        self.assertEqual(0, self.state().unavailable_checks)
        with self.sessions() as session:
            checks = session.query(WebsiteListingCheck).filter_by(outcome='check_failed').all()
            self.assertEqual(2, len(checks))
            self.assertTrue(all(json.loads(c.evidence)['reason'] == 'WebsiteDOMTimeout' for c in checks))
            self.assertTrue(all(json.loads(c.evidence)['browser']['stage'] == 'detail' for c in checks))
            self.assertEqual(3, session.query(ListingSnapshot).count())
            self.assertEqual(0, session.query(CarListing).filter_by(is_active=False).count())
            self.assertEqual(0, session.query(ScrapingLog).filter_by(status='completed').count())

    def test_refusal_uses_one_visit_and_persistent_pause_prevents_rerun(self):
        from database.website_tracking import SourcePaused
        blocked = detail_page(); blocked['blocked'] = True
        report, browser = self.run_detail_browser([blocked])
        self.assertEqual('paused', report['status'])
        self.assertEqual(1, self.store.budget(self.clock)['detail_used'])
        browser.driver.get.assert_called_once()
        with self.assertRaises(SourcePaused):
            self.store.reserve('detail', self.clock, 1, 20, 15)
