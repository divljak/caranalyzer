"""Website fixtures and simulated runs; these tests make no network requests."""
from copy import deepcopy
from datetime import datetime, timedelta
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db_manager import DatabaseManager
from database.models import Base, CarListing, ListingSnapshot, ScrapingLog
from database.website_tracking import BudgetExhausted, SourcePaused, WebsiteListingState, WebsiteListingCheck, WebsiteStore
from run_website_sync import parse_args, sync
from scrapers.olx_website_collector import WebsiteBlocked, WebsiteReadError, parse_detail, parse_search, search_url


def search_page(page=1, ids=('79589391', '79806723'), total=601):
    return dict(url=search_url(page), blocked=False, result_heading=f'{total} REZULTATA', current_page=page,
                last_page=16, cards=[dict(url=f'/artikal/{lid}', title='Volkswagen Polo 1.4 benzin',
                price='Na upit' if lid == '79806723' else '7.550 KM', tags=['Benzin','172.000 km','2010'], dealer=False) for lid in ids])


def detail_page(lid='79589391'):
    # Selected fields observed on the rendered website on 2026-10-01; no contact details.
    return dict(url=f'https://olx.ba/artikal/{lid}', blocked=False, main_title='Volkswagen Polo 1.4 benzin',
                headings=['Volkswagen Polo 1.4 benzin','Detaljne informacije'], prices=['7.550 KM'], on_request=False,
                attributes={'ID':lid,'Proizvođač':'Volkswagen','Model':'Polo','Gorivo':'Benzin','Godište':'2010',
                'Transmisija':'Manuelni','Kilometraža':'172.000km','Datum objave':'15.09.2026','Obnovljen':'01.10.2026 u 17:26'})


def absent_page(lid='79589391'):
    return dict(url=f'https://olx.ba/artikal/{lid}', blocked=False, main_title='',
                headings=['Oglas nije dostupan'], attributes={}, prices=[])


class ParserTests(unittest.TestCase):
    def test_search_keeps_on_request_ads_and_explicit_card_attributes(self):
        result = parse_search(search_page(),1,search_url())
        self.assertEqual(2,len(result['listings']))
        priced, missing = result['listings']
        self.assertEqual((7550,172000,2010,'petrol'),(priced['price'],priced['mileage'],priced['year'],priced['fuel_type']))
        self.assertEqual((0,'missing'),(missing['price'],missing['price_status']))
        self.assertEqual('olx_website',priced['collection_method'])

    def test_discount_current_price_is_retained(self):
        page = search_page(ids=('77729480',))
        # Current .discounted-price, verified live: old 11,999 KM / current 10,999 KM.
        page['cards'][0]['price'] = '10.999 KM'
        self.assertEqual(10999,parse_search(page,1,search_url())['listings'][0]['price'])

    def test_clamped_page_changed_scope_and_refusals_are_rejected(self):
        with self.assertRaises(WebsiteReadError):
            parse_search(search_page(1),2,search_url())
        page = search_page(); page['url'] = search_url(1,89,1599)
        with self.assertRaises(WebsiteReadError):
            parse_search(page,1,search_url(1,90))
        page['blocked'] = True
        with self.assertRaises(WebsiteBlocked):
            parse_search(page,1,search_url())

    def test_duplicate_cards_unknown_price_and_single_page(self):
        page = search_page(ids=('79589391','79589391','79806723'))
        page['current_page'], page['last_page'] = None, None
        self.assertEqual(2,len(parse_search(page,1,search_url())['listings']))
        page['cards'][0]['price'] = 'unexpected'
        self.assertGreater(parse_search(page,1,search_url())['rejected'],0)

    def test_details_use_fields_and_convert_renewal_timezone(self):
        result = parse_detail(detail_page(),'79589391')
        self.assertEqual('listed',result['outcome'])
        self.assertEqual('manual',result['listing']['transmission'])
        self.assertEqual(datetime(2026,10,1,15,26),result['renewed_at'])
        self.assertEqual('Polo',result['listing']['model'])

    def test_unknown_redirect_wrong_id_and_price_conflict_are_failures(self):
        for change in [dict(url='https://olx.ba/'),dict(attributes={}),dict(prices=['7.550 KM','7.750 KM'])]:
            page=detail_page();page.update(change)
            self.assertEqual('check_failed',parse_detail(page,'79589391')['outcome'])
        self.assertEqual('unavailable',parse_detail(absent_page(),'79589391')['outcome'])

    def test_seller_title_or_description_cannot_claim_sold(self):
        page=detail_page();page['main_title']='Oglas je prodan';page['headings']=['Oglas je prodan','Oglas nije dostupan']
        self.assertEqual('listed',parse_detail(page,'79589391')['outcome'])

    def test_pilot_limits_cannot_be_bypassed_by_cli(self):
        for args in [['--search-pages','4'],['--detail-checks','51'],['--delay','1'],['--model','1599']]:
            with self.assertRaises(SystemExit):
                parse_args(args)


class StoreAndRunTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.sessions=sessionmaker(bind=self.engine)
        self.db=DatabaseManager.__new__(DatabaseManager)
        self.db.engine,self.db.SessionLocal=self.engine,self.sessions
        self.store=WebsiteStore(self.engine)
        self.clock=datetime(2026,10,1,7,17)
        self.run=self.store.start_run('unit-run',self.clock,search_url(),3,20)
        self.seed('79589391')

    def seed(self,lid):
        self.db.upsert_verified_listing(parse_detail(detail_page(lid),lid)['listing'],observed_at=self.clock)

    def tearDown(self):
        self.engine.dispose()

    def state(self,lid='79589391'):
        with self.sessions() as s:
            return s.get(WebsiteListingState,lid)

    def now(self):
        return self.clock

    def sleep(self,seconds):
        self.clock+=timedelta(seconds=seconds)

    def test_failed_visits_and_reruns_consume_persistent_budget(self):
        self.assertEqual(0,self.store.reserve('search',self.clock,3,20,15))
        self.assertEqual(15,self.store.reserve('search',self.clock,3,20,15))
        self.assertEqual(30,self.store.reserve('search',self.clock,3,20,15))
        with self.assertRaises(BudgetExhausted):
            WebsiteStore(self.engine).reserve('search',self.clock,3,20,15)
        with self.assertRaises(ValueError):
            self.store.reserve('detail',self.clock,3,51,15)
        self.assertEqual(0,self.store.reserve('search',self.clock+timedelta(days=1),3,20,15))

    def test_pause_survives_midnight_and_process_restart(self):
        self.store.pause(self.clock)
        with self.assertRaises(SourcePaused):
            WebsiteStore(self.engine).reserve('detail',self.clock+timedelta(hours=23),3,20,15)
        self.assertEqual(0,self.store.reserve('detail',self.clock+timedelta(hours=24),3,20,15))

    def test_failure_preserves_previous_fields_and_does_not_remove(self):
        self.store.record_check(self.run,'79589391',self.clock,parse_detail(detail_page(),'79589391'))
        self.store.record_check(self.run,'79589391',self.clock+timedelta(days=1),{'outcome':'check_failed'})
        state=self.state()
        self.assertEqual('listed',state.status)
        self.assertEqual('manual',state.transmission)
        self.assertEqual(0,state.unavailable_checks)
        with self.sessions() as s:
            self.assertTrue(s.get(CarListing,'79589391').is_active)

    def test_three_checks_over_72_hours_mark_removal_with_no_sale_claim(self):
        result=parse_detail(absent_page(),'79589391')
        for hours in (0,24):
            self.store.record_check(self.run,'79589391',self.clock+timedelta(hours=hours),result)
            self.assertEqual('unavailable_pending',self.state().status)
        self.store.record_check(self.run,'79589391',self.clock+timedelta(hours=72),result)
        self.assertEqual('removed_unconfirmed',self.state().status)
        with self.sessions() as s:
            self.assertFalse(s.get(CarListing,'79589391').is_active)
            self.assertEqual(3,s.query(WebsiteListingCheck).count())
            self.assertEqual(1,s.query(ListingSnapshot).count())
        self.db.upsert_verified_listing(parse_detail(detail_page(),'79589391')['listing'],self.clock+timedelta(days=4))
        self.store.note_listed('79589391',self.clock+timedelta(days=4))
        self.assertEqual('listed',self.state().status)
        self.assertEqual(0,self.state().unavailable_checks)

    def test_queue_checks_recent_and_stale_and_prioritizes_due_unavailable(self):
        for lid in ('101','102','103','104'):
            self.seed(lid)
        self.store.record_check(self.run,'101',self.clock,{'outcome':'unavailable'})
        self.assertEqual('101',self.store.candidates(self.clock+timedelta(days=1),3)[0])
        self.assertNotIn('101',self.store.candidates(self.clock+timedelta(hours=1),3))

    def run_browser(self,read,search_pages=1,detail_checks=1):
        class Browser:
            calls=[]
            def read(self,url,kind):
                self.calls.append((url,kind))
                return read(url,kind)
        browser=Browser()
        report=sync(self.db,self.store,browser,search_pages=search_pages,detail_checks=detail_checks,
                    now=self.now,sleep=self.sleep)
        return report,browser.calls

    def test_success_preserves_history_and_never_marks_unseen_ads_missing(self):
        self.seed('101')
        def read(url,kind):
            return search_page(ids=('79589391',)) if kind=='search' else detail_page(url.rsplit('/',1)[-1])
        report,calls=self.run_browser(read)
        self.assertEqual('completed',report['status'])
        self.assertEqual((1,1),(report['search_attempted'],report['detail_attempted']))
        self.assertFalse(report['sale_inference'])
        with self.sessions() as s:
            self.assertTrue(s.get(CarListing,'101').is_active)
            self.assertEqual(0,s.get(CarListing,'101').consecutive_missing_runs)
            self.assertEqual(4,s.query(ListingSnapshot).count())

    def test_block_stops_remaining_pages_and_persists_pause(self):
        def read(url,kind):
            raise WebsiteBlocked('refused')
        report,calls=self.run_browser(read,search_pages=3,detail_checks=20)
        self.assertEqual('paused',report['status'])
        self.assertEqual(1,len(calls))
        with self.assertRaises(SourcePaused):
            self.store.reserve('detail',self.clock,3,20,15)
        with self.sessions() as s:
            self.assertEqual(0,s.query(ScrapingLog).filter_by(status='completed').count())

    def test_repeated_result_page_stops_discovery_without_inactivating_history(self):
        def read(url,kind):
            page=search_page();page['url']=url;page['current_page']=int(url.rsplit('=',1)[-1]);return page
        report,calls=self.run_browser(read,search_pages=3)
        self.assertEqual('partial',report['status'])
        self.assertEqual(2,len(calls))
        self.assertEqual(0,report['discovery_complete'])

    def test_two_detail_failures_stop_without_retry_or_negative_status(self):
        self.seed('101');self.seed('102')
        def read(url,kind):
            return search_page(ids=('79589391',)) if kind=='search' else dict(url=url,blocked=False,attributes={},headings=[])
        report,calls=self.run_browser(read,detail_checks=20)
        self.assertEqual('partial',report['status'])
        self.assertEqual(2,report['detail_attempted'])
        with self.sessions() as s:
            self.assertEqual(0,s.query(CarListing).filter_by(is_active=False).count())
            self.assertEqual(0,s.query(ScrapingLog).filter_by(status='completed').count())

    def test_rerun_can_use_remaining_detail_budget_without_repeating_discovery(self):
        self.store.reserve('search',self.clock,1,2,15)
        report,calls=self.run_browser(lambda url,kind: detail_page(url.rsplit('/',1)[-1]),detail_checks=2)
        self.assertEqual(0,report['search_attempted'])
        self.assertEqual(1,report['detail_attempted'])
        self.assertEqual(['detail'],[kind for _,kind in calls])
        self.assertEqual('partial', report['status'])
        self.assertEqual('discovery_budget_incomplete', report['stopped_reason'])


if __name__=='__main__':
    unittest.main()
