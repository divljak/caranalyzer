"""PIK relaunch (10 Nov 2026) fixtures and the bounded page-1 retry. No network requests."""
from datetime import datetime, timedelta
import json
import unittest

from selenium.common.exceptions import TimeoutException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db_manager import DatabaseManager
from database.models import Base, CarListing
from database.website_tracking import BudgetExhausted, WebsiteBudget, WebsiteRun, WebsiteStore
from run_website_sync import sync
from scrapers.olx_website_collector import (WebsiteBlocked, WebsiteDOMTimeout, WebsiteReadError, listing_id,
                                            parse_detail, parse_search, search_url)


def pik_search_page(page=1, ids=('76348132', '81138257'), total='47.810'):
    # Shape returned by EXTRACT_SCRIPT for the PIK layout, observed live on 14 Nov 2026.
    slugs = {'76348132': 'peugeot-308-2013', '81138257': 'peugeot-407-16-hdi-prvi-vlasnik-veliki-servis-novo'}
    return dict(url=search_url(page), blocked=False, result_heading=f'{total} oglasa', current_page=page,
                last_page=50, cards=[dict(url=f'/artikal/{slugs.get(lid, "audi-a3")}-{lid}', title='Peugeot 308 2013',
                price='Na upit' if lid == '81138257' else '9.400 KM',
                tags=['dizel', '210.000 km', '2013'], dealer=None) for lid in ids])


def pik_detail_page(lid='80634552'):
    return dict(url=f'https://olx.ba/artikal/{lid}', blocked=False, main_title='Audi A3 1.6 TDI',
                headings=['Audi A3 1.6 TDI', 'Karakteristike', 'Specifikacije', 'Opis oglasa'],
                prices=['10.650 KM'], on_request=False, document_status=200,
                attributes={'ID': lid, 'Proizvođač': 'Audi', 'Model': 'A3', 'Gorivo': 'Dizel', 'Godište': '2012',
                            'Transmisija': 'Manuelni', 'Kilometraža': '233.000 km', 'Datum objave': '25.10.2026.',
                            'Obnovljen': '14.11.2026. u 22:09', 'Broj pregleda': '2.586'})


def pik_missing_page(lid='60000001', status=404):
    return dict(url=f'https://olx.ba/artikal/{lid}', blocked=False, main_title='', attributes={}, prices=[],
                headings=['Oglas nije pronađen', 'Informacije i kontakt'], document_status=status)


class PikParserTests(unittest.TestCase):
    def test_slug_urls_keep_the_numeric_listing_id(self):
        self.assertEqual('76348132', listing_id('/artikal/peugeot-308-2013-76348132'))
        self.assertEqual('76348132', listing_id('https://olx.ba/artikal/golf-7-2015-76348132'))
        self.assertEqual('76348132', listing_id('/artikal/76348132'))
        for bad in ['/artikal/peugeot-308', '/artikal/76348132/edit', 'https://evil.example/artikal/a-1']:
            with self.assertRaises(WebsiteReadError):
                listing_id(bad)

    def test_pik_search_page_parses_count_cards_and_unknown_seller(self):
        result = parse_search(pik_search_page(), 1, search_url())
        self.assertEqual((47810, 50, 0), (result['source_total'], result['last_page'], result['rejected']))
        priced, on_request = result['listings']
        self.assertEqual(('76348132', 9400, 210000, 2013, 'diesel'),
                         (priced['listing_id'], priced['price'], priced['mileage'], priced['year'], priced['fuel_type']))
        self.assertEqual('https://olx.ba/artikal/76348132', priced['listing_url'])
        self.assertEqual((0, 'missing'), (on_request['price'], on_request['price_status']))
        # PIK cards do not mark shops: unknown, never guessed as a private seller.
        self.assertIsNone(priced['seller_type'])

    def test_shop_flag_from_the_page_payload_sets_the_seller_type(self):
        page = pik_search_page(ids=('76348132', '81138257', '81000001'))
        for card, dealer in zip(page['cards'], (True, False, None)):
            card['dealer'] = dealer
        kinds = [item['seller_type'] for item in parse_search(page, 1, search_url())['listings']]
        self.assertEqual(['dealer', 'individual', None], kinds)

    def test_unrecognised_result_count_is_not_a_verified_search(self):
        page = pik_search_page()
        page['result_heading'] = 'Automobili'
        with self.assertRaises(WebsiteReadError):
            parse_search(page, 1, search_url())

    def test_pik_detail_dates_with_trailing_dot_and_renewal_timezone(self):
        result = parse_detail(pik_detail_page(), '80634552')
        self.assertEqual('listed', result['outcome'])
        listing = result['listing']
        self.assertEqual(('Audi', 'A3', 10650, 2012, 233000, 'manual'),
                         (listing['make'], listing['model'], listing['price'], listing['year'],
                          listing['mileage'], listing['transmission']))
        self.assertEqual('2026-10-25', listing['posted_date'].isoformat())
        self.assertEqual(datetime(2026, 11, 14, 21, 9), result['renewed_at'])
        self.assertNotIn('Broj pregleda', result['evidence']['attributes'])

    def test_pik_missing_ad_needs_the_404_document(self):
        result = parse_detail(pik_missing_page(), '60000001')
        self.assertEqual(('unavailable', 'oglas nije pronađen', 404),
                         (result['outcome'], result['evidence']['message'], result['evidence']['http_status']))
        # The same heading on an HTTP 200 page is not evidence of removal.
        self.assertEqual('check_failed', parse_detail(pik_missing_page(status=200), '60000001')['outcome'])


class PageOneRetryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.db = DatabaseManager.__new__(DatabaseManager)
        self.db.engine, self.db.SessionLocal = self.engine, self.sessions
        self.store = WebsiteStore(self.engine)
        self.clock = datetime(2026, 11, 15, 7, 15)

    def tearDown(self):
        self.engine.dispose()

    def now(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += timedelta(seconds=seconds)

    def run_sync(self, failures, search_pages=3):
        """failures: exceptions raised by the first search reads, in order."""
        failures = list(failures)
        test = self

        class Browser:
            calls, restarts = [], 0

            def read(self, url, kind):
                self.calls.append((url, kind))
                if failures:
                    raise failures.pop(0)
                page_number = int(url.rsplit('=', 1)[-1])
                page = pik_search_page(page_number, ids=(f'{page_number}000001', f'{page_number}000002'))
                page['url'] = url
                return page

            def restart(self):
                self.restarts += 1

        browser = Browser()
        report = sync(self.db, self.store, browser, search_pages=search_pages, detail_checks=0,
                      now=self.now, sleep=self.sleep, page_order='forward')
        return report, browser

    @staticmethod
    def dom_timeout():
        exc = WebsiteDOMTimeout('Reserved visit did not produce a verified DOM')
        exc.diagnostics = {'stage': 'search', 'navigation_timeout': True, 'navigation_ms': 30012, 'dom_ms': 20004,
                           'final_url': search_url(1), 'title': 'Automobili · PIK', 'ready_state': 'interactive',
                           'document_status': 200, 'parser_reason': 'search_cards_or_pagination_not_verified'}
        return exc

    def test_one_page_one_timeout_is_retried_once_with_a_fresh_driver(self):
        report, browser = self.run_sync([self.dom_timeout()])
        self.assertEqual(1, browser.restarts)
        self.assertEqual(4, len(browser.calls))
        self.assertEqual((1, 1, 'completed', 6), (report['search_retries'], report['discovery_complete'],
                                                   report['status'], report['observations']))
        failure = report['failures'][0]
        self.assertEqual(('search', 1, 1, 'WebsiteDOMTimeout', 30012, 20004, 200, 'interactive'),
                         (failure['phase'], failure['page'], failure['attempt'], failure['exception'],
                          failure['navigation_ms'], failure['dom_ms'], failure['document_status'],
                          failure['ready_state']))
        with self.sessions() as s:
            self.assertEqual(4, s.get(WebsiteBudget, 1).search_visits)  # the retry is counted
            stored = json.loads(s.query(WebsiteRun).one().report)
            self.assertEqual('WebsiteDOMTimeout', stored['failures'][0]['exception'])

    def test_navigation_timeout_from_the_driver_is_also_retried(self):
        report, browser = self.run_sync([TimeoutException('renderer timeout')])
        self.assertEqual((1, 1, 'completed'), (browser.restarts, report['search_retries'], report['status']))

    def test_second_timeout_fails_with_both_attempts_recorded(self):
        report, browser = self.run_sync([self.dom_timeout(), self.dom_timeout()])
        self.assertEqual((1, 2, 'failed'), (browser.restarts, len(browser.calls), report['status']))
        self.assertEqual([1, 2], [f['attempt'] for f in report['failures']])
        self.assertTrue(all(f['final_url'] == search_url(1) for f in report['failures']))
        self.assertEqual('Reserved visit did not produce a verified DOM', report['stopped_reason'])

    def test_refusal_is_never_retried(self):
        report, browser = self.run_sync([WebsiteBlocked('Website access was refused')])
        self.assertEqual((0, 1, 'paused', 'source_refusal'),
                         (browser.restarts, len(browser.calls), report['status'], report['stopped_reason']))
        self.assertEqual('WebsiteBlocked', report['failures'][0]['exception'])

    def test_parser_rejection_and_later_pages_are_not_retried(self):
        report, browser = self.run_sync([WebsiteReadError('Search filters changed during navigation')])
        self.assertEqual((0, 1, 'failed'), (browser.restarts, len(browser.calls), report['status']))

        self.engine.dispose()
        self.setUp()
        later = self.dom_timeout()

        class Page2Fails:
            calls, restarts = [], 0

            def read(self, url, kind):
                self.calls.append(url)
                if url.endswith('page=2'):
                    raise later
                page = pik_search_page(1)
                page['url'] = url
                return page

            def restart(self):
                self.restarts += 1

        browser = Page2Fails()
        report = sync(self.db, self.store, browser, search_pages=3, detail_checks=0, now=self.now, sleep=self.sleep,
                      page_order='forward')
        self.assertEqual((0, 2, 'partial'), (browser.restarts, len(browser.calls), report['status']))
        self.assertEqual(2, report['failures'][0]['page'])

    def test_retry_allowance_is_search_only_and_single(self):
        for _ in range(3):
            self.store.reserve('search', self.clock, 3, 20, 15)
        with self.assertRaises(BudgetExhausted):
            self.store.reserve('search', self.clock, 3, 20, 15)
        self.store.reserve('search', self.clock, 3, 20, 15, retry_allowance=1)
        with self.assertRaises(BudgetExhausted):
            self.store.reserve('search', self.clock, 3, 20, 15, retry_allowance=1)
        for bad in [dict(kind='detail', retry_allowance=1), dict(kind='search', retry_allowance=2)]:
            with self.assertRaises(ValueError):
                self.store.reserve(bad['kind'], self.clock, 3, 20, 15, retry_allowance=bad['retry_allowance'])


if __name__ == '__main__':
    unittest.main()
