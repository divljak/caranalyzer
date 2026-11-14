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


PIK_SEARCH = '''<!doctype html><html><head><meta charset="utf-8"><title>Automobili · PIK</title></head><body>
<section class="search-page__results"><div class="search-page__toolbar"><div>
<h1 id="search-results-title">Automobili</h1><p role="status">47.810 oglasa</p></div></div>
<div class="listing-grid listing-grid--search">
<article class="listing-card"><div class="listing-card__content"><h2 class="listing-card__title">
<a href="/artikal/peugeot-308-2013-76348132" class="listing-card__title-link">Peugeot 308 2013</a></h2>
<div class="listing-card__labels"><div class="listing-card__attributes"><span title="Gorivo">dizel</span>
<span title="Kilometraža">210.000 km</span><span title="Godište">2013</span></div></div>
<div class="listing-card__footer"><div class="listing-card__price"><strong>9.400 KM</strong></div></div></div></article>
<article class="listing-card"><div class="listing-card__content"><h2 class="listing-card__title">
<a href="/artikal/audi-a4-2016-81000001" class="listing-card__title-link">Audi A4 2016</a></h2>
<div class="listing-card__attributes"><span>benzin</span><span>150.000 km</span><span>2016</span></div>
<div class="listing-card__price--discounted listing-card__price"><del>27.300 KM</del>
<strong><span class="sr-only" style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)">Akcijska cijena: </span>26.300 KM</strong></div></div></article>
</div>
<nav class="ui-pagination search-page__pagination" aria-label="Stranice rezultata"><ol class="ui-pagination__list">
<li><button aria-current="page" class="ui-pagination__page is-current"><span>1</span></button></li>
<li><a href="?page=2" class="ui-pagination__page"><span>2</span></a></li>
<li class="is-desktop-only"><a href="?page=7" class="ui-pagination__page"><span>7</span></a></li>
<li><span class="ui-pagination__gap">…</span></li>
<li><a href="?page=50" class="ui-pagination__page"><span>50</span></a></li></ol>
<a aria-current="page" href="?page=2" class="ui-pagination__arrow" rel="next">›</a></nav></section>
<script type="application/json" id="__NUXT_DATA__" data-ssr="true">[{"data":1},[2,9],
{"id":3,"title":4,"shop":5,"featured":6,"type":7},76348132,"Peugeot 308 2013",true,2,"single",0,
{"id":10,"title":11,"shop":12,"featured":6,"type":7},81000001,"Audi A4 2016",false]</script></body></html>'''

PIK_DETAIL = '''<!doctype html><html><head><meta charset="utf-8"><title>Audi A3 · PIK</title></head><body>
<h1 id="article-title">Audi A3 1.6 TDI</h1><div class="article-page__metadata"><span>ID 80634552</span></div>
<h2 id="article-details-title">Karakteristike</h2>
<dl class="article-details__highlights">
<div><dt><img alt=""><span>Proizvođač</span></dt><dd><a href="/pretraga?brand=8">Audi <span class="iconify"></span></a></dd></div>
<div><dt><span>Model</span></dt><dd>A3</dd></div><div><dt><span>Godište</span></dt><dd>2012</dd></div>
<div><dt><span>Kilometraža</span></dt><dd>233.000 km</dd></div><div><dt><span>Transmisija</span></dt><dd>Manuelni</dd></div>
<div><dt><span>Gorivo</span></dt><dd>Dizel</dd></div></dl>
<dl class="article-details__rows--metadata"><div><dt>Datum objave</dt><dd>25.10.2026.</dd></div>
<div><dt>Obnovljen</dt><dd>14.11.2026. u 22:09</dd></div><div><dt>ID oglasa</dt><dd>80634552</dd></div></dl>
<section class="article-price"><h2 class="sr-only">Cijena</h2><div class="article-price__main">
<strong class="article-price__amount">10.650 KM</strong></div></section></body></html>'''


@unittest.skipUnless(os.getenv('DETAIL_BROWSER_LOOPBACK') == '1', 'Requires packaged Chromium; loopback only')
class PikMarkupLoopbackTests(unittest.TestCase):
    """The extraction script against PIK markup (OLX relaunch, 10 Nov 2026) in real Chromium."""

    def setUp(self):
        pages = {'/pretraga': PIK_SEARCH, '/artikal/80634552': PIK_DETAIL}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = pages.get(self.path.split('?')[0], '').encode()
                self.send_response(200 if body else 404)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_search_cards_count_and_pagination_are_extracted(self):
        with WebsiteBrowser() as browser:
            page = browser.read(self.base + '/pretraga?category_id=18&page=1', 'search')
        self.assertEqual(('47.810 oglasa', 1, 50), (page['result_heading'], page['current_page'], page['last_page']))
        self.assertEqual(['/artikal/peugeot-308-2013-76348132', '/artikal/audi-a4-2016-81000001'],
                         [c['url'] for c in page['cards']])
        self.assertEqual(['9.400 KM', '26.300 KM'], [c['price'] for c in page['cards']])
        self.assertEqual(['dizel', '210.000 km', '2013'], page['cards'][0]['tags'])
        # seller type comes from the page's own Nuxt payload (shop: true / false per ad id)
        self.assertEqual([True, False], [c['dealer'] for c in page['cards']])
        self.assertIsNotNone(page['browser']['navigation_ms'])

    def test_detail_attributes_id_and_price_are_extracted(self):
        with WebsiteBrowser() as browser:
            browser.driver.get(self.base + '/artikal/80634552')
            page = browser.driver.execute_async_script(EXTRACT_ASYNC_SCRIPT, 'detail')
        self.assertEqual('Audi A3 1.6 TDI', page['main_title'])
        self.assertEqual(('80634552', 'Audi', '25.10.2026.'),
                         (page['attributes']['ID'], page['attributes']['Proizvođač'], page['attributes']['Datum objave']))
        self.assertEqual(['10.650 KM'], page['prices'])
        page['url'] = 'https://olx.ba/artikal/80634552'  # loopback origin is never accepted as evidence
        self.assertEqual('listed', parse_detail(page, '80634552')['outcome'])
