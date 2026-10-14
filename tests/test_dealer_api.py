"""Integration tests require an explicit disposable/restored local PostgreSQL URL."""
import os
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from sqlalchemy import text, create_engine
from fastapi.testclient import TestClient

URL = os.getenv('DEALER_TEST_DATABASE_URL')

@unittest.skipUnless(URL, 'Set DEALER_TEST_DATABASE_URL to the local audit PostgreSQL copy')
class DealerEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import dealer_api
        cls.module = dealer_api
        cls.previous_engine = dealer_api.engine
        dealer_api.engine = create_engine(URL)
        cls.engine = dealer_api.engine
        cls.client_context = TestClient(dealer_api.app)
        cls.client = cls.client_context.__enter__()
        cls.ids = ['__dealer_test_1', '__dealer_test_2', '__dealer_test_missing', '__dealer_test_2023']
        with cls.engine.begin() as c:
            cls.anchor = dealer_api.as_of(c)
            for i, lid in enumerate(cls.ids):
                c.execute(text("""INSERT INTO car_listings(listing_id,make,model,year,price,price_status,source,collection_method,
                 last_verified_at,first_seen_at,is_active,consecutive_missing_runs,mileage,fuel_type,transmission)
                 VALUES (:id,'__DealerTest','Test',:year,:price,:status,'olx.ba','olx_api',:seen,:seen,false,:missing,:mileage,:fuel,:transmission)"""),
                 {'id':lid,'year':2023 if i==3 else 2024,'price':[10000,20000,0,30000][i],'status':'missing' if i==2 else 'priced','seen':cls.anchor-timedelta(days=1),'missing':3 if i==0 else 0,
                  'mileage':[100000,200000,None,80000][i],'fuel':['diesel','electric',None,'diesel'][i],'transmission':['manual',None,None,'automatic'][i]})
            for i, price in enumerate([12000,12000,10000,10000]):
                c.execute(text("""INSERT INTO listing_snapshots(listing_id,observed_at,asking_price,price_status,source_url,title,is_active)
                 VALUES (:id,:seen,:price,'priced','https://olx.ba/artikal/test','test',true)"""),
                 {'id':cls.ids[0],'seen':cls.anchor-timedelta(days=5-i),'price':price})
            for lid, age, price, status in [(cls.ids[1],40,22000,'priced'),(cls.ids[1],39,20000,'priced'),
                                           (cls.ids[1],38,20000,'priced'),(cls.ids[0],-1,9000,'priced'),
                                           (cls.ids[2],2,0,'missing')]:
                c.execute(text("""INSERT INTO listing_snapshots(listing_id,observed_at,asking_price,price_status,source_url,title,is_active)
                 VALUES (:id,:seen,:price,:status,'https://olx.ba/artikal/test','test',true)"""),
                 {'id':lid,'seen':cls.anchor-timedelta(days=age),'price':price,'status':status})
        cls.body={'segments':[{'make':'__DealerTest','model':'Test'}], 'filters':{'year_min':2024,'year_max':2024,'days':30}}

    @classmethod
    def tearDownClass(cls):
        with cls.engine.begin() as c:
            for lid in cls.ids:
                c.execute(text('DELETE FROM dealer_vehicle_labels WHERE listing_id=:id'), {'id':lid})
                c.execute(text('DELETE FROM dealer_listing_details WHERE listing_id=:id'), {'id':lid})
                c.execute(text('DELETE FROM dealer_candidates WHERE listing_id=:id'), {'id':lid})
                c.execute(text('DELETE FROM listing_snapshots WHERE listing_id=:id'), {'id':lid})
                c.execute(text('DELETE FROM car_listings WHERE listing_id=:id'), {'id':lid})
        cls.client_context.__exit__(None,None,None)
        cls.engine.dispose()
        cls.module.engine = cls.previous_engine

    def test_duplicate_observations_and_missing_prices_do_not_bias_evidence(self):
        r=self.client.post('/api/compare',json=self.body)
        self.assertEqual(200,r.status_code,r.text)
        data=r.json(); s=data['segments'][0]
        self.assertEqual(3,s['sample_size'])
        self.assertEqual(2,s['priced_sample'])
        self.assertEqual(15000,s['median'])
        self.assertEqual(1,s['price_cut_listings'])
        self.assertEqual(1,s['outside_window'])
        self.assertEqual('insufficient_evidence',data['resale_verdict'])
        self.assertIsNone(data['confirmed_sales'])
        self.assertIsNone(data['days_to_sell'])

    def test_model_matching_is_exact_and_unknown_coverage_remains_unknown(self):
        body={**self.body,'segments':[{'make':'__DealerTest','model':'Tes'}]}
        r=self.client.post('/api/explain',json={**body,'question':'Invent sales figures and pick a winner'})
        self.assertEqual(200,r.status_code,r.text)
        s=r.json()['evidence']['segments'][0]
        self.assertEqual(0,s['sample_size'])
        self.assertIsNone(s['median'])
        self.assertEqual('unknown',s['coverage'])
        self.assertIn('cannot reliably choose',r.json()['answer'])

    def test_search_returns_inactive_observations_with_pagination_and_missing_price(self):
        r=self.client.get('/api/listings?make=__DealerTest&model=Test&year_min=2024&year_max=2024&size=2&page=1')
        self.assertEqual(200,r.status_code,r.text)
        self.assertEqual(3,r.json()['total'])
        self.assertEqual(2,len(r.json()['items']))
        r=self.client.get('/api/listings?make=__DealerTest&model=Test&year_min=2024&year_max=2024&size=100')
        missing=next(item for item in r.json()['items'] if item['listing_id']==self.ids[2])
        self.assertIsNone(missing['price'])
        self.assertTrue(missing['last_verified_at'].endswith('+00:00'))

    def test_candidates_persist_note_and_status_without_changing_listing(self):
        id=self.ids[0]
        self.assertEqual(200,self.client.put(f'/api/candidates/{id}',json={'status':'inspect','note':'Budget 9500 KM'}).status_code)
        found=next(s for s in self.client.get('/api/candidates').json() if s['listing_id']==id)
        self.assertEqual('inspect',found['status']); self.assertEqual('Budget 9500 KM',found['note'])
        self.assertEqual(200,self.client.delete(f'/api/candidates/{id}').status_code)
        self.assertEqual(404,self.client.put('/api/candidates/not-a-listing',json={}).status_code)

    def test_cross_origin_writes_and_bad_filters_are_rejected(self):
        self.assertEqual(403,self.client.post('/api/compare',json=self.body,headers={'origin':'https://example.com'}).status_code)
        self.assertEqual(422,self.client.post('/api/compare',json={**self.body,'filters':{'year_min':2025,'year_max':2024}}).status_code)
        self.assertEqual(422,self.client.get('/api/listings?page=0').status_code)
        self.assertEqual(422,self.client.get('/api/listings?year_min=2025&year_max=2024').status_code)
        self.assertEqual(400,self.client.get('/api/overview',headers={'host':'evil.example'}).status_code)

    def test_overview_separates_latest_failed_attempt_from_partial_observations(self):
        started = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
        ids = []
        try:
            with self.engine.begin() as c:
                for offset, status, observations in [(0, 'partial', 68), (1, 'failed', 0)]:
                    ids.append(c.execute(text("""INSERT INTO website_collection_runs
                      (session_id,started_at,finished_at,status,source_query,search_limit,detail_limit,
                       search_attempted,detail_attempted,discovery_complete,details_completed,observations,new_listings,errors,report)
                      VALUES (:session,:started,:finished,:status,'verification',0,0,0,0,0,0,:observations,0,1,'{}') RETURNING id"""),
                      {'session':f'__freshness_test_{offset}', 'started':started+timedelta(minutes=offset),
                       'finished':started+timedelta(minutes=offset,seconds=30), 'status':status,
                       'observations':observations}).scalar())
            result = self.client.get('/api/overview')
            self.assertEqual(200, result.status_code, result.text)
            data = result.json()
            self.assertEqual('failed', data['website_collection']['status'])
            self.assertEqual(0, data['website_collection']['observations'])
            self.assertEqual('partial', data['website_observation_collection']['status'])
            self.assertEqual(68, data['website_observation_collection']['observations'])
            self.assertEqual(self.module.utc(self.anchor), data['as_of'])
            self.assertTrue(data['website_collection']['started_at'].endswith('+00:00'))
        finally:
            with self.engine.begin() as c:
                c.execute(text('DELETE FROM website_collection_runs WHERE id=ANY(:ids)'), {'ids':ids})

    def test_model_analysis_has_year_gaps_distinct_prices_and_attribute_coverage(self):
        r=self.client.get('/api/model-analysis?make=__DealerTest&model=Test&year_min=2022&year_max=2025&days=90')
        self.assertEqual(200,r.status_code,r.text)
        data=r.json(); years={y['year']:y for y in data['years']}
        self.assertEqual(4,data['summary']['sample_size'])
        self.assertEqual(3,data['summary']['priced_sample'])
        self.assertEqual(0,years[2022]['sample_size']); self.assertIsNone(years[2022]['median'])
        self.assertEqual(30000,years[2023]['median'])
        self.assertEqual(15000,years[2024]['median']); self.assertEqual(2,years[2024]['priced_sample'])
        self.assertEqual(10000,years[2024]['minimum']); self.assertEqual(20000,years[2024]['maximum'])
        self.assertEqual(3,data['attributes']['known_mileage']); self.assertEqual(2,data['attributes']['known_transmission'])
        self.assertEqual('incomplete_newest_results_sample',data['coverage'])
        self.assertIsNone(data['confirmed_sales']); self.assertIsNone(data['days_to_sell'])

    def test_monthly_history_uses_last_price_once_per_listing_and_excludes_future(self):
        r=self.client.get('/api/model-analysis?make=__DealerTest&model=Test&year_min=2024&year_max=2024&days=90')
        self.assertEqual(200,r.status_code,r.text)
        history=r.json()['monthly']
        self.assertEqual(2,len(history))
        self.assertEqual([1,1],[m['priced_sample'] for m in history])
        self.assertEqual([20000,10000],[m['median'] for m in history])
        self.assertIn('not a market price index',r.json()['history_basis'])

    def test_monthly_history_is_independent_of_the_cohort_window_and_bounded_to_twelve_months(self):
        with self.engine.begin() as c:
            added = c.execute(text("""INSERT INTO listing_snapshots(listing_id,observed_at,asking_price,price_status,source_url,title,is_active)
              VALUES (:id,:seen,7777,'priced','https://olx.ba/artikal/test','test',true),
                     (:id,:old,8888,'priced','https://olx.ba/artikal/test','test',true) RETURNING id"""),
              {'id':self.ids[0],'seen':self.anchor-timedelta(days=200),'old':self.anchor-timedelta(days=500)}).scalars().all()
        try:
            response=self.client.get('/api/model-analysis?make=__DealerTest&model=Test&year_min=2024&year_max=2024&days=7')
            self.assertEqual(200,response.status_code,response.text)
            history=response.json()['monthly']
            self.assertIn(7777,[m['median'] for m in history])
            self.assertNotIn(8888,[m['median'] for m in history])
            self.assertLessEqual(len(history),12)
            self.assertEqual(2,response.json()['summary']['priced_sample'])
        finally:
            with self.engine.begin() as c:
                c.execute(text('DELETE FROM listing_snapshots WHERE id=ANY(:ids)'),{'ids':added})

    def test_desktop_routes_support_direct_navigation(self):
        for path in ['/market','/compare','/following','/models/Škoda/Karoq']:
            response=self.client.get(path)
            self.assertEqual(200,response.status_code,response.text)
            self.assertIn('text/html',response.headers['content-type'])
        self.assertEqual(404,self.client.get('/api/not-a-route').status_code)

    def test_listing_history_hides_unchanged_repeats_and_has_no_sale_claim(self):
        r=self.client.get(f'/api/listings/{self.ids[0]}/history')
        self.assertEqual(200,r.status_code,r.text)
        data=r.json(); self.assertEqual([12000,10000],[e['price'] for e in data['events']])
        self.assertEqual(-2000,data['events'][1]['change']); self.assertIsNone(data['events'][0]['change'])
        self.assertTrue(data['events'][0]['observed_at'].endswith('+00:00'))
        self.assertEqual('unverified',data['availability']); self.assertIsNone(data['confirmed_sale'])
        self.assertEqual(404,self.client.get('/api/listings/not-a-listing/history').status_code)
        missing=self.client.get(f'/api/listings/{self.ids[2]}/history').json()
        self.assertIsNone(missing['events'][0]['price'])

    def test_website_observations_and_removal_evidence_are_exposed_without_sale_claim(self):
        lid=self.ids[0]
        with self.engine.begin() as c:
            c.execute(text("UPDATE car_listings SET collection_method='olx_website' WHERE listing_id=:id"),{'id':lid})
            c.execute(text("INSERT INTO website_listing_states(listing_id,status,last_checked_at,last_outcome,unavailable_checks) VALUES(:id,'removed_unconfirmed',:seen,'unavailable',3)"),{'id':lid,'seen':self.anchor})
        try:
            r=self.client.get('/api/listings',params={'make':'__DealerTest','model':'Test','year_min':2024,'year_max':2024})
            self.assertEqual(200,r.status_code,r.text)
            item=next(i for i in r.json()['items'] if i['listing_id']==lid)
            self.assertEqual('removed_unconfirmed',item['availability'])
            self.assertEqual('olx_website',item['collection_method'])
            history=self.client.get(f'/api/listings/{lid}/history').json()
            self.assertEqual('removed_unconfirmed',history['availability'])
            self.assertIsNone(history['confirmed_sale'])
        finally:
            with self.engine.begin() as c:
                c.execute(text('DELETE FROM website_listing_states WHERE listing_id=:id'),{'id':lid})
                c.execute(text("UPDATE car_listings SET collection_method='olx_api' WHERE listing_id=:id"),{'id':lid})

    def test_mileage_ranges_model_requirements_and_sorting(self):
        self.assertEqual(422,self.client.get('/api/model-analysis').status_code)
        self.assertEqual(422,self.client.get('/api/model-analysis?make=__DealerTest&model=Test&mileage_min=200&mileage_max=100').status_code)
        params={'make':'__DealerTest','model':'Test','year_min':2024,'year_max':2024,'mileage_min':90000,'mileage_max':110000}
        r=self.client.get('/api/model-analysis',params=params)
        self.assertEqual(200,r.status_code,r.text); self.assertEqual(1,r.json()['summary']['sample_size'])
        self.assertEqual(10000,r.json()['summary']['median'])
        params.pop('mileage_min'); params.pop('mileage_max')
        for sort, expected in [('price_asc',[self.ids[0],self.ids[1],self.ids[2]]),('price_desc',[self.ids[1],self.ids[0],self.ids[2]])]:
            r=self.client.get('/api/listings',params={**params,'sort':sort})
            self.assertEqual(200,r.status_code,r.text); self.assertEqual(expected,[c['listing_id'] for c in r.json()['items']])
        self.assertEqual(422,self.client.get('/api/listings?sort=unsupported').status_code)

    def test_structured_transmission_overrides_title_and_filters_without_mutating_history(self):
        from scrapers.olx_listing_details import store_transmission
        checked=self.anchor+timedelta(days=1)
        try:
            with self.engine.begin() as c:
                store_transmission(c,self.ids[0],{'transmission':'automatic','raw_value':'Automatik'},checked,'collected')
            params={'make':'__DealerTest','model':'Test','year_min':2024,'year_max':2024,'transmission':'automatic'}
            r=self.client.get('/api/listings',params=params)
            self.assertEqual(200,r.status_code,r.text); self.assertEqual(1,r.json()['total'])
            car=r.json()['items'][0]
            self.assertEqual('automatic',car['transmission']); self.assertEqual('olx_attribute',car['transmission_source'])
            self.assertTrue(car['transmission_checked_at'].endswith('+00:00'))
            r=self.client.get('/api/model-analysis',params=params)
            self.assertEqual(200,r.status_code,r.text); self.assertEqual(1,r.json()['attributes']['structured_transmission'])
            comparison=self.client.post('/api/compare',json={**self.body,'filters':params})
            self.assertEqual(1,comparison.json()['segments'][0]['sample_size'])
            with self.engine.begin() as c:
                self.assertEqual('manual',c.execute(text('SELECT transmission FROM car_listings WHERE listing_id=:id'),{'id':self.ids[0]}).scalar())
                store_transmission(c,self.ids[0],{},checked+timedelta(hours=1),'unavailable')
                self.assertEqual(checked,c.execute(text('SELECT checked_at FROM dealer_listing_details WHERE listing_id=:id'),{'id':self.ids[0]}).scalar())
        finally:
            with self.engine.begin() as c:c.execute(text('DELETE FROM dealer_listing_details WHERE listing_id=:id'),{'id':self.ids[0]})

    def test_transmission_job_stops_on_source_limits_and_keeps_remaining_ads_unchanged(self):
        from scrapers.olx_listing_details import DetailsUnavailable
        job_id='__detail_test'
        self.module.detail_jobs[job_id]={'id':job_id,'status':'queued','total':2,'checked':0,'enriched':0,'not_specified':0,'unavailable':0,'errors':0,'message':''}
        try:
            with patch.object(self.module,'fetch_transmission',side_effect=DetailsUnavailable(429)) as fetch:
                self.module.run_detail_job(job_id,self.ids[:2],delay=0)
            fetch.assert_called_once()
            job=self.client.get(f'/api/transmission-jobs/{job_id}').json()
            self.assertEqual('partial',job['status']); self.assertEqual(1,job['checked']); self.assertEqual(1,job['unavailable'])
            with self.engine.connect() as c:
                self.assertEqual(0,c.execute(text("SELECT count(*) FROM dealer_listing_details WHERE listing_id=ANY(:ids)"),{'ids':self.ids}).scalar())
                from scrapers.olx_listing_details import details_resume_at
                self.assertIsNotNone(details_resume_at(c))
        finally:
            del self.module.detail_jobs[job_id]
            with self.engine.begin() as c:
                c.execute(text("DELETE FROM dealer_source_pauses WHERE source='olx_listing_details'"))
        self.assertEqual(422,self.client.post('/api/model-transmission',json={}).status_code)
        self.assertEqual(403,self.client.post('/api/model-transmission',json=self.body['filters'],headers={'origin':'https://example.com'}).status_code)

    def test_discovery_budget_selects_models_without_truncating_the_price_band(self):
        # Label fixtures explicitly; production labels are inferred and stored separately.
        with self.engine.begin() as c:
            for lid in self.ids:
                c.execute(text("""INSERT INTO dealer_vehicle_labels(listing_id,make,model,category,category_basis,source_make,source_model,version)
                  VALUES (:id,'__DealerTest','Test','suv','model_family','__DealerTest','Test',1)
                  ON CONFLICT(listing_id) DO UPDATE SET make=excluded.make,model=excluded.model,category=excluded.category"""),{'id':lid})
        r=self.client.get('/api/discovery',params={'make':'__DealerTest','model':'Test','year_min':2024,'year_max':2024,'price_max':11000,'category':'suv'})
        self.assertEqual(200,r.status_code,r.text)
        item=r.json()['items'][0]
        self.assertEqual(1,item['within_budget'])
        self.assertEqual(2,item['priced_sample'])
        self.assertEqual(15000,item['median'])
        self.assertEqual(1,item['price_cut_listings'])
        self.assertIsNone(r.json()['confirmed_sales'])
        self.assertEqual([],self.client.get('/api/discovery',params={'make':'__DealerTest','price_max':9999}).json()['items'])
        self.assertEqual([],self.client.get('/api/discovery',params={'make':'__DealerTest','category':'sedan'}).json()['items'])
        self.assertEqual(422,self.client.get('/api/discovery?sort=demand').status_code)

    def test_saved_searches_preserve_filters_baseline_and_deduplicate_without_touching_ads(self):
        filters={'make':'__DealerTest','model':'Test','year_min':2024,'year_max':2024,'days':30,'price_max':11000,'mileage_min':90000,'mileage_max':210000}
        saved=self.client.post('/api/model-searches',json=filters)
        self.assertEqual(200,saved.status_code,saved.text)
        sid=saved.json()['id']
        try:
            self.assertEqual(sid,self.client.post('/api/model-searches',json={**filters,'price_max':50000}).json()['id'])
            found=next(s for s in self.client.get('/api/model-searches').json() if s['id']==sid)
            self.assertEqual(15000,found['baseline']['median'])
            self.assertEqual(15000,found['current']['median'])
            self.assertIsNone(found['filters']['price_max'])
            self.assertEqual(90000,found['filters']['mileage_min'])
            self.assertFalse(found['new_data'])
            with patch.object(self.module,'as_of',return_value=self.anchor+timedelta(days=1)):
                changed=next(s for s in self.client.get('/api/model-searches').json() if s['id']==sid)
                self.assertTrue(changed['new_data'])
                self.assertEqual(found['baseline_as_of'],changed['baseline_as_of'])
                self.assertEqual(found['baseline'],changed['baseline'])
            with patch.object(self.module,'as_of',return_value=self.anchor-timedelta(days=1)):
                older=next(s for s in self.client.get('/api/model-searches').json() if s['id']==sid)
                self.assertFalse(older['new_data'])
            with self.engine.connect() as c:
                self.assertEqual(4,c.execute(text('SELECT count(*) FROM car_listings WHERE listing_id=ANY(:ids)'),{'ids':self.ids}).scalar())
            self.assertEqual(422,self.client.post('/api/model-searches',json={}).status_code)
            self.assertEqual(403,self.client.post('/api/model-searches',json=filters,headers={'origin':'https://example.com'}).status_code)
        finally:
            self.client.delete(f'/api/model-searches/{sid}')
        self.assertEqual(404,self.client.delete(f'/api/model-searches/{sid}').status_code)
