"""Privacy/runtime checks without modifying collector evidence."""
import os
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import NullPool

import dealer_api
from hosted_runtime import hosted_engine, same_origin

URL = ('postgresql+psycopg2://car_analyzer_app.ojmokmsjppkqwppmrzpv:unused'
       '@aws-0-eu-north-1.pooler.supabase.com:6543/postgres?sslmode=require')


class HostedRuntimeTests(unittest.TestCase):
    def test_pooler_tls_and_restricted_role_are_mandatory(self):
        with patch.dict(os.environ, {'DATABASE_URL': URL}):
            engine = hosted_engine()
            self.assertIsInstance(engine.pool, NullPool)
            self.assertTrue(engine.hide_parameters)
            engine.dispose()
        for bad in ['', URL.replace(':6543/', ':5432/'),
                    URL.replace('sslmode=require', 'sslmode=disable'),
                    URL.replace('car_analyzer_app.', 'postgres.')]:
            with self.subTest(url=bad), patch.dict(os.environ, {'DATABASE_URL': bad}):
                with self.assertRaises(RuntimeError):
                    hosted_engine()

    def test_hosted_origin_uses_https_host_despite_proxy_base_scheme(self):
        self.assertTrue(same_origin('https://tool.vercel.app', 'tool.vercel.app', 'http://tool.vercel.app/', True))
        for origin in [None, 'https://other.vercel.app', 'http://tool.vercel.app', 'https://tool.vercel.app.evil.example']:
            self.assertFalse(same_origin(origin, 'tool.vercel.app', 'http://tool.vercel.app/', True))

    def test_hosted_requests_fail_closed_and_do_not_run_maintenance(self):
        engine = Mock()
        engine.begin.side_effect = RuntimeError('sensitive connection information')
        with patch.object(dealer_api, 'HOSTED', True), patch.object(dealer_api, 'engine', engine), \
             patch.dict(os.environ, {'VERCEL_ENV': 'preview'}):
            with TestClient(dealer_api.app, base_url='https://testserver') as client:
                # Hosted lifespan never attempts DDL or label updates.
                engine.begin.assert_not_called()
                response = client.get('/api/overview')
                self.assertEqual(503, response.status_code)
                self.assertNotIn('sensitive', response.text)
                self.assertEqual('no-store', response.headers['cache-control'])
                for headers in [{}, {'origin': 'https://evil.example'},
                                {'origin': 'https://testserver', 'sec-fetch-site': 'cross-site'}]:
                    self.assertEqual(403, client.post('/api/compare', json={}, headers=headers).status_code)
                engine.begin.reset_mock()
                self.assertEqual(409, client.post('/api/model-transmission', json={}).status_code)
                engine.begin.assert_not_called()
                with patch.dict(os.environ, {'VERCEL_ENV': 'production'}):
                    self.assertEqual(503, client.get('/market').status_code)
                    self.assertEqual(503, client.get('/api/overview').status_code)


if __name__ == '__main__':
    unittest.main()
