"""Delivery rollback, idempotence and hosted-state preservation on isolated schemas."""
import gzip
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from scripts.sync_supabase import DeliveryError, deliver, digest
from database.website_tracking import create_website_tables


@unittest.skipUnless(os.getenv('DEALER_TEST_DATABASE_URL'), 'Requires the local audit database')
class DeliveryTests(unittest.TestCase):
    def setUp(self):
        url = os.environ['DEALER_TEST_DATABASE_URL']
        if make_url(url).host not in {'127.0.0.1','localhost','::1'}:
            self.skipTest('Requires loopback audit PostgreSQL')
        self.admin = create_engine(url)
        self.schemas = ['delivery_test_' + uuid.uuid4().hex[:10] for _ in range(2)]
        self.engines = []
        self.addCleanup(self.cleanup_schemas)
        self.date = datetime(2026,10,1,7,30)
        for schema in self.schemas:
            with self.admin.begin() as c:
                c.execute(text(f'CREATE SCHEMA {schema}'))
                c.execute(text(f'SET search_path TO {schema}'))
                c.execute(text('CREATE TABLE car_listings(listing_id text PRIMARY KEY, price integer, last_verified_at timestamp)'))
                c.execute(text('CREATE TABLE listing_snapshots(id serial PRIMARY KEY, listing_id text REFERENCES car_listings(listing_id), observed_at timestamp, asking_price integer)'))
                c.execute(text('CREATE TABLE scraping_logs(id serial PRIMARY KEY, session_id text, end_time timestamp, status text, errors_count integer)'))
                c.execute(text('CREATE TABLE dealer_model_searches(id text PRIMARY KEY, baseline jsonb, filters jsonb, baseline_as_of timestamp)'))
                c.execute(text('CREATE TABLE dealer_listing_details(listing_id text PRIMARY KEY, transmission text)'))
                c.execute(text("INSERT INTO car_listings VALUES('existing',10000,:seen)"),{'seen':self.date})
                c.execute(text("INSERT INTO listing_snapshots VALUES(1,'existing',:seen,10000)"),{'seen':self.date})
                c.execute(text("INSERT INTO scraping_logs VALUES(1,'run-one',:seen,'completed',0)"),{'seen':self.date})
            self.engines.append(create_engine(url,connect_args={'options':f'-csearch_path={schema}'}))
        self.source,self.target = self.engines
        with self.target.begin() as c:
            c.execute(text("INSERT INTO dealer_model_searches VALUES('saved',CAST(:baseline AS jsonb),CAST(:filters AS jsonb),:seen)"),
                      {'baseline':json.dumps({'median':10000}),'filters':json.dumps({'make':'Volkswagen','days':30}),'seen':self.date})
            c.execute(text("INSERT INTO dealer_listing_details VALUES('existing','automatic')"))
            c.execute(text("INSERT INTO car_listings VALUES('retained',9000,:seen)"),{'seen':self.date})
            c.execute(text("INSERT INTO listing_snapshots VALUES(2,'retained',:seen,9000)"),{'seen':self.date})
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        from pathlib import Path
        self.state = Path(self.directory.name)
        for engine in self.engines:
            create_website_tables(engine)
            with engine.begin() as c:
                c.execute(text('CREATE TABLE dealer_candidates(listing_id text PRIMARY KEY, note text)'))
                c.execute(text('CREATE TABLE dealer_source_pauses(source text PRIMARY KEY, resume_at timestamp)'))
                c.execute(text('CREATE TABLE dealer_vehicle_labels(listing_id text PRIMARY KEY)'))
        self.refresh = patch('scripts.sync_supabase.refresh_labels')
        self.refresh.start()
        self.addCleanup(self.refresh.stop)

    def cleanup_schemas(self):
        for engine in self.engines:
            engine.dispose()
        with self.admin.begin() as c:
            for schema in self.schemas:
                c.execute(text(f'DROP SCHEMA IF EXISTS {schema} CASCADE'))
        self.admin.dispose()

    def add_collection(self):
        with self.source.begin() as c:
            c.execute(text("UPDATE car_listings SET price=8000,last_verified_at=:seen WHERE listing_id='existing'"),{'seen':self.date+timedelta(days=1)})
            c.execute(text("INSERT INTO car_listings VALUES('new',18000,:seen)"),{'seen':self.date+timedelta(days=1)})
            c.execute(text("INSERT INTO listing_snapshots VALUES(3,'new',:seen,18000)"),{'seen':self.date+timedelta(days=1)})
            c.execute(text("INSERT INTO scraping_logs VALUES(2,'run-two',:seen,'completed',0)"),{'seen':self.date+timedelta(days=1)})

    def test_atomic_delivery_preserves_hosted_state_extra_history_and_repeats(self):
        self.add_collection()
        first = deliver(self.source, self.target, self.state)
        self.assertEqual(1, first['changed']['listing_snapshots'])
        second = deliver(self.source, self.target, self.state)
        self.assertTrue(all(n == 0 for n in second['changed'].values()))
        self.assertEqual(first['protected_checksums'], second['protected_checksums'])
        with self.target.connect() as c:
            self.assertEqual(3, c.execute(text('SELECT count(*) FROM listing_snapshots')).scalar())
            self.assertEqual({'median':10000}, c.execute(text('SELECT baseline FROM dealer_model_searches')).scalar())
            self.assertEqual({'make':'Volkswagen','days':30}, c.execute(text('SELECT filters FROM dealer_model_searches')).scalar())
            self.assertEqual(self.date, c.execute(text('SELECT baseline_as_of FROM dealer_model_searches')).scalar())
            self.assertEqual('automatic', c.execute(text('SELECT transmission FROM dealer_listing_details')).scalar())
        backup = json.loads(gzip.decompress(next(self.state.glob('*.json.gz')).read_bytes()))
        for name, value in backup['tables'].items():
            self.assertEqual(backup['checksums'][name], digest(value))

    def test_immutable_conflict_rolls_back_all_changes_and_success_marker(self):
        self.add_collection()
        with self.source.begin() as c:
            c.execute(text('UPDATE listing_snapshots SET asking_price=1 WHERE id=1'))
        with self.assertRaisesRegex(DeliveryError, 'Immutable'):
            deliver(self.source, self.target, self.state)
        with self.target.connect() as c:
            self.assertEqual(10000, c.execute(text("SELECT price FROM car_listings WHERE listing_id='existing'")).scalar())
            self.assertEqual(0, c.execute(text("SELECT count(*) FROM car_listings WHERE listing_id='new'")).scalar())
        self.assertFalse((self.state / 'latest-success.json').exists())

    def test_overlap_is_rejected_without_modifying_data(self):
        with self.target.begin() as lock:
            lock.execute(text('SELECT pg_advisory_xact_lock(731400220)'))
            with self.assertRaisesRegex(DeliveryError, 'already running'):
                deliver(self.source, self.target, self.state)
