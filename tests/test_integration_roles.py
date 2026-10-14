"""Positive priority reads and protected writes on a disposable role-aware schema."""
import os
import unittest

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

URL = os.getenv('INTEGRATION_ROLE_DATABASE_URL')


@unittest.skipUnless(URL, 'Requires the disposable integration bootstrap')
class IntegrationRoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(URL)
        if url.host not in ('127.0.0.1', 'localhost') or not url.database.startswith('car_analyzer_integration'):
            raise ValueError('Refuse a non-disposable database')
        cls.engine = create_engine(url, hide_parameters=True)
        with cls.engine.begin() as c:
            c.execute(text("""INSERT INTO car_listings(listing_id,make,model,source,collection_method)
                VALUES ('__role_fixture','Test','Fixture','olx.ba','olx_website')"""))
            c.execute(text("INSERT INTO dealer_candidates(listing_id) VALUES ('__role_fixture')"))
            c.execute(text("""INSERT INTO dealer_listing_details(listing_id,source_url,checked_at,outcome)
                VALUES ('__role_fixture','https://example.invalid/fixture',now(),'not_specified')"""))
            c.execute(text("""INSERT INTO dealer_model_searches(id,fingerprint,filters,baseline,baseline_as_of)
                VALUES ('__role_fixture','__role_fixture','{}','{}',now())"""))

    @classmethod
    def tearDownClass(cls):
        with cls.engine.begin() as c:
            c.execute(text("DELETE FROM dealer_model_searches WHERE id='__role_fixture'"))
            for table in ('dealer_candidates', 'dealer_listing_details', 'car_listings'):
                c.execute(text(f"DELETE FROM {table} WHERE listing_id='__role_fixture'"))
        cls.engine.dispose()

    def test_collector_reads_nonempty_priority_inputs_and_backup_reads_sequences(self):
        from database.collection_priority import load_priority_inputs
        with self.engine.connect() as c, c.begin():
            c.execute(text('SET LOCAL ROLE car_analyzer_collector'))
            self.assertEqual('car_analyzer_collector', c.execute(text('SELECT current_user')).scalar())
            for table in ('dealer_candidates', 'dealer_listing_details', 'dealer_model_searches'):
                self.assertGreaterEqual(c.execute(text(f'SELECT count(*) FROM {table}')).scalar(), 1)
            rows, searches = load_priority_inputs(c)
            self.assertTrue(any(row['listing_id'] == '__role_fixture' for row in rows))
            self.assertIn({}, searches)
        with self.engine.connect() as c, c.begin():
            c.execute(text('SET LOCAL ROLE car_analyzer_backup'))
            self.assertIsNotNone(c.execute(text('SELECT last_value FROM listing_snapshots_id_seq')).scalar())

    def test_final_roles_deny_private_and_immutable_mutation(self):
        operations = {
            'car_analyzer_collector': [
                'UPDATE dealer_listing_details SET outcome=outcome WHERE false',
                'DELETE FROM dealer_candidates WHERE false',
                'UPDATE dealer_model_searches SET filters=filters WHERE false',
                'UPDATE listing_snapshots SET asking_price=asking_price WHERE false',
                'DELETE FROM website_listing_checks WHERE false',
            ],
            'car_analyzer_app': [
                'UPDATE car_listings SET price=price WHERE false',
                'UPDATE dealer_model_searches SET baseline=baseline WHERE false',
                'UPDATE dealer_model_searches SET filters=filters WHERE false',
            ],
            'car_analyzer_backup': ['DELETE FROM car_listings WHERE false'],
            'car_analyzer_sync': ['UPDATE car_listings SET price=price WHERE false'],
            'anon': ['SELECT * FROM car_listings'],
            'authenticated': ['SELECT * FROM dealer_model_searches'],
        }
        for role, sqls in operations.items():
            with self.subTest(role=role), self.engine.connect() as c, c.begin():
                c.execute(text(f'SET LOCAL ROLE {role}'))
                for sql in sqls:
                    with self.subTest(sql=sql):
                        savepoint = c.begin_nested()
                        try:
                            with self.assertRaises(DBAPIError) as error:
                                c.execute(text(sql))
                            self.assertEqual('42501', error.exception.orig.pgcode)
                        finally:
                            savepoint.rollback()
        with self.engine.connect() as c:
            self.assertFalse(c.execute(text("SELECT rolcanlogin FROM pg_roles WHERE rolname='car_analyzer_sync'")).scalar())
