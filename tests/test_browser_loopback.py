"""Optional packaged Chromium smoke tests: loopback HTTP only, no database writes."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import time
import unittest
from selenium.common.exceptions import TimeoutException
from scrapers.olx_website_collector import EXTRACT_ASYNC_SCRIPT, WebsiteBrowser, WebsiteDOMTimeout, parse_detail


@unittest.skipUnless(os.getenv('DETAIL_BROWSER_LOOPBACK') == '1', 'Requires packaged Chromium; loopback only')
class LoopbackBrowserTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                requests.append(self.path)
                body = b'''<!doctype html><title>Local browser fixture</title>
                    <h1 class="search-title">1 REZULTATA</h1>
                    <a style="display:block" href="/artikal/123"><h1 class="main-heading">Fixture vehicle</h1></a>
                    <h1 class="main-title-listing">Fixture vehicle</h1>
                    <div><h4>ID</h4><h4>123</h4></div>
                    <div><h4>Proizvo\xc4\x91a\xc4\x8d</h4><h4>Fixture</h4></div>
                    <div><h4>Model</h4><h4>Fixture</h4></div>
                    <span class="price-heading">10.000 KM</span>
                    <script defer src="/slow.js"></script>'''
                self.send_response(200)
                self.send_header('Content-Type', 'application/javascript' if self.path == '/slow.js' else 'text/html; charset=utf-8')
                self.end_headers()
                if self.path == '/slow.js':
                    time.sleep(3)
                    body = b'/* delayed loopback asset */'
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/fixture'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_renderer_timeout_recovers_existing_dom_and_cleans_up_driver(self):
        with WebsiteBrowser() as browser:
            # Allow the fixture document to arrive on a busy shared VPS while
            # its deferred script still guarantees a real navigation timeout.
            browser.driver.set_page_load_timeout(1)
            try:
                page = browser.read(self.url, 'search')
            except WebsiteDOMTimeout as exc:
                self.fail(str(getattr(exc, 'diagnostics', {})))
            self.assertEqual('1 REZULTATA', page['result_heading'])
            self.assertTrue(page['browser']['navigation_timeout'])
            self.assertEqual(1, self.requests.count('/fixture'))
            process = browser.service.process
        self.assertIsNotNone(process.poll())
        self.assertIsNone(browser.driver)
        print(json.dumps({'fixture_navigation_timeout_recovered': True, 'source_visits': 0, 'driver_stopped': True}))

    def test_async_extraction_timeout_and_local_identity_rejection(self):
        with WebsiteBrowser() as browser:
            browser.driver.get(self.url)
            page = browser.driver.execute_async_script(EXTRACT_ASYNC_SCRIPT, 'detail')
            self.assertEqual('123', page['attributes']['ID'])
            self.assertEqual([], page['cards'])
            self.assertEqual('check_failed', parse_detail(page, '123')['outcome'])
            browser.driver.set_script_timeout(0.1)
            with self.assertRaises(TimeoutException):
                browser.driver.execute_async_script('/* deliberately never call callback */')
            page = browser.driver.execute_async_script(EXTRACT_ASYNC_SCRIPT, 'search')
            self.assertEqual('1 REZULTATA', page['result_heading'])
        print(json.dumps({'async_timeout_configured': True, 'local_fixture_rejected_as_evidence': True, 'source_visits': 0}))
