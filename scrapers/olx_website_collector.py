"""Read rendered website pages only. No direct OLX API calls or automatic retries."""
from datetime import datetime
import json
import logging
import os
import re
import shutil
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from zoneinfo import ZoneInfo

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

from scrapers.olx_live_collector import OLXLiveCollector

PARSER_VERSION = 'website-v1'

# Only selected DOM fields are returned. No seller contact or full page source is retained.
EXTRACT_SCRIPT = r"""
const clean = e => e ? e.innerText.replace(/\s+/g, ' ').trim() : '';
const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
const body = document.body ? document.body.innerText : '';
const blocked = /verify you are human|checking your browser|access denied|too many requests|potvrdite da niste robot|captcha/i.test(body);
const headings = Array.from(document.querySelectorAll('h1,h2,h3')).filter(visible).map(clean);
const attributes = {};
for (const h of document.querySelectorAll('h4')) {
  if (!visible(h)) continue;
  const next = h.nextElementSibling;
  if (next && next.tagName === 'H4') attributes[clean(h)] = clean(next);
}
const cards = arguments[0] === 'search' ? Array.from(document.querySelectorAll('a[href*="/artikal/"]'))
  .filter(a => visible(a) && a.querySelector('h1.main-heading'))
  .map(a => ({url:a.getAttribute('href'), title:clean(a.querySelector('h1.main-heading')),
    price:clean(a.querySelector('.price-wrap .smaller:not(.discount)')),
    tags:Array.from(a.querySelectorAll('.standard-tag')).map(clean),
    dealer:/PIK SHOP/.test(a.innerText)})) : [];
const pageItems = Array.from(document.querySelectorAll('li')).filter(e => visible(e) && /^\d+$/.test(clean(e)));
const activePage = pageItems.find(e => e.classList.contains('active'));
const resultHeading = document.querySelector('h1.search-title');
const mainTitle = document.querySelector('h1.main-title-listing');
const prices = Array.from(document.querySelectorAll('.price-heading')).filter(visible).map(clean);
const onRequest = Array.from(document.querySelectorAll('.pricing')).filter(visible).some(e => /Na upit|Po dogovoru/i.test(e.innerText));
return {url:location.href, title:document.title, blocked, headings, attributes, cards,
  result_heading:clean(resultHeading), current_page:activePage ? Number(clean(activePage)) : null,
  last_page:pageItems.length ? Math.max(...pageItems.map(e => Number(clean(e)))) : null,
  main_title:clean(mainTitle), prices, on_request:onRequest};
"""

# Async WebDriver execution makes the configured script timeout apply to DOM
# extraction. The callback returns selected fields only; it makes no requests.
EXTRACT_ASYNC_SCRIPT = "const done = arguments[arguments.length - 1]; done((function () {" + EXTRACT_SCRIPT + "}).call(null, arguments[0]));"


class WebsiteReadError(RuntimeError):
    pass


class WebsiteBlocked(WebsiteReadError):
    pass


class WebsiteDOMTimeout(WebsiteReadError):
    """The reserved visit never produced a verified rendered page."""
    pass


def search_url(page=1, brand=None, model=None):
    params = {'category_id': 18, 'sort_by': 'date', 'order': 'desc', 'page': page}
    if brand:
        params.update(brand=brand, brands=brand)
    if model:
        params['models'] = model
    return 'https://olx.ba/pretraga?' + urlencode(params)


def price_value(value):
    value = ' '.join((value or '').split())
    if value.casefold() in {'na upit', 'po dogovoru', 'cijena na upit'}:
        return 0, 'missing'
    match = re.fullmatch(r'(\d[\d.\s]*(?:,\d{1,2})?)\s*KM', value, re.I)
    if not match:
        raise WebsiteReadError('Price format is unknown; refusing to guess')
    amount = match.group(1).replace('.', '').replace(' ', '').replace(',', '.')
    return int(float(amount)), 'priced'


def numeric(value, suffix=''):
    if value is None:
        return None
    value = re.sub(re.escape(suffix) + r'\s*$', '', str(value).strip(), flags=re.I) if suffix else str(value).strip()
    if not re.fullmatch(r'\d[\d.\s]*', value):
        return None
    return int(re.sub(r'\D', '', value))


def fuel(value):
    value = (value or '').casefold()
    if 'hibrid' in value or 'hybrid' in value:
        return 'hybrid'
    if value in {'dizel', 'diesel'}:
        return 'diesel'
    if value in {'benzin', 'petrol'}:
        return 'petrol'
    if value in {'elektro', 'električni', 'electric'}:
        return 'electric'
    return None


def transmission(value):
    if (value or '').casefold() in {'automatik', 'automatski', 'automatic'}:
        return 'automatic'
    if (value or '').casefold() in {'manuelni', 'manualni', 'manual', 'ručni'}:
        return 'manual'
    return None


def listing_id(url):
    parsed = urlsplit(url)
    if parsed.netloc and parsed.netloc != 'olx.ba':
        raise WebsiteReadError('Unexpected listing host')
    match = re.fullmatch(r'/artikal/(\d{1,20})/?', parsed.path)
    if not match:
        raise WebsiteReadError('Unexpected listing URL')
    return match.group(1)


def parse_search(page, requested_page, query):
    if page.get('blocked'):
        raise WebsiteBlocked('Website access was refused')
    parsed_url = urlsplit(page.get('url', ''))
    if parsed_url.netloc != 'olx.ba' or parsed_url.path.rstrip('/') != '/pretraga':
        raise WebsiteReadError('Search redirected away from its scope')
    actual_query, expected_query = parse_qs(parsed_url.query), parse_qs(urlsplit(query).query)
    if any(actual_query.get(k) != v for k, v in expected_query.items() if k != 'page'):
        raise WebsiteReadError('Search filters changed during navigation')
    result_match = re.fullmatch(r'([\d.\s]+)\s+rezultata', page.get('result_heading', ''), re.I)
    if not result_match:
        raise WebsiteReadError('Search result count is missing')
    total = numeric(result_match.group(1))
    if total and page.get('current_page') != requested_page and not (
            requested_page == 1 and page.get('last_page') is None and page.get('current_page') is None):
        raise WebsiteReadError('Website returned a different result page')
    listings, seen, rejected, duplicates = [], set(), 0, 0
    for card in page.get('cards', []):
        try:
            lid = listing_id(card['url'])
            if lid in seen:
                duplicates += 1
                continue
            if not card.get('title'):
                raise WebsiteReadError('Missing title')
            price, price_status = price_value(card.get('price'))
            tags = card.get('tags', [])
            make, model = OLXLiveCollector._parse_make_model(card['title'])
            listings.append({'listing_id': lid, 'listing_url': f'https://olx.ba/artikal/{lid}',
                'title': card['title'], 'description': card['title'], 'make': make, 'model': model,
                'price': price, 'price_status': price_status,
                # Search attributes are explicit card labels; make/model stay title inference until a direct check.
                'year': next((numeric(t) for t in tags if re.fullmatch(r'(19|20)\d{2}', t)), None),
                'mileage': next((numeric(t, 'km') for t in tags if t.casefold().endswith('km')), None),
                'fuel_type': next((fuel(t) for t in tags if fuel(t)), None),
                'seller_type': 'dealer' if card.get('dealer') else 'individual',
                'collection_method': 'olx_website', 'source_query': query,
                'source_page': requested_page, 'source_per_page': None})
            seen.add(lid)
        except (KeyError, ValueError, WebsiteReadError):
            rejected += 1
    if total and not listings:
        raise WebsiteReadError('Search has results but no cards could be parsed')
    return {'listings': listings, 'source_total': total, 'last_page': page.get('last_page'),
            'raw_cards': len(page.get('cards', [])), 'rejected': rejected, 'duplicates': duplicates}


# Conservative exact messages. Unknown templates are check failures, never removals.
UNAVAILABLE_MESSAGES = {'oglas nije dostupan', 'ovaj oglas nije dostupan', 'oglas ne postoji', 'ovaj oglas ne postoji'}
ENDED_MESSAGES = {'oglas je završen', 'ovaj oglas je završen'}
SOLD_MESSAGES = {'oglas je prodan', 'ovaj oglas je prodan'}


def parse_detail(page, expected_id):
    base = {'evidence': {'parser': PARSER_VERSION}}
    if 'browser' in page:
        base['evidence']['browser'] = page['browser']
    if page.get('blocked'):
        return {**base, 'outcome': 'blocked'}
    try:
        parsed = urlsplit(page.get('url', ''))
        if parsed.scheme != 'https' or parsed.netloc != 'olx.ba':
            raise WebsiteReadError('Unexpected direct page origin')
        if listing_id(page.get('url', '')) != str(expected_id):
            raise WebsiteReadError('Direct page changed identity')
    except WebsiteReadError:
        return {**base, 'outcome': 'check_failed', 'evidence': {**base['evidence'], 'reason': 'identity_or_redirect'}}
    # H1 is deliberately excluded: a seller's title containing "sold" is not a site status.
    headings = {h.casefold().strip(' .!') for h in page.get('headings', []) if h != page.get('main_title')}
    if sum(bool(headings & messages) for messages in
           (UNAVAILABLE_MESSAGES, ENDED_MESSAGES, SOLD_MESSAGES)) > 1 and not page.get('main_title'):
        return {**base, 'outcome': 'check_failed', 'evidence': {**base['evidence'], 'reason': 'conflicting_status'}}
    for messages, outcome in [(UNAVAILABLE_MESSAGES, 'unavailable'), (ENDED_MESSAGES, 'source_reported_ended'),
                              (SOLD_MESSAGES, 'source_reported_sold')]:
        matching = headings & messages
        if matching and not page.get('attributes', {}).get('ID') and not page.get('main_title'):
            return {**base, 'outcome': outcome, 'evidence': {**base['evidence'], 'message': sorted(matching)[0]}}
    attributes = page.get('attributes', {})
    if attributes.get('ID') != str(expected_id) or not page.get('main_title'):
        return {**base, 'outcome': 'check_failed', 'evidence': {**base['evidence'], 'reason': 'unrecognized_template'}}
    allowed = {'ID', 'Proizvođač', 'Model', 'Gorivo', 'Godište', 'Transmisija', 'Kilometraža',
               'Kubikaža', 'Snaga motora (KW)', 'Broj vrata', 'Datum objave', 'Obnovljen'}
    attrs = {k: v[:160] for k, v in attributes.items() if k in allowed and isinstance(v, str)}
    try:
        prices = {price_value(p) for p in page.get('prices', [])}
        if len(prices) == 1:
            price, price_status = prices.pop()
        elif not prices and page.get('on_request'):
            price, price_status = 0, 'missing'
        else:
            raise WebsiteReadError('Current price is absent or conflicting')
    except WebsiteReadError:
        return {**base, 'outcome': 'check_failed', 'evidence': {**base['evidence'], 'reason': 'price_not_verified'}}
    data = {'listing_id': str(expected_id), 'listing_url': f'https://olx.ba/artikal/{expected_id}',
        'title': page['main_title'], 'description': page['main_title'],
        'make': attrs.get('Proizvođač'), 'model': attrs.get('Model'),
        'price': price, 'price_status': price_status, 'year': numeric(attrs.get('Godište')),
        'mileage': numeric(attrs.get('Kilometraža'), 'km'), 'fuel_type': fuel(attrs.get('Gorivo')),
        'transmission': transmission(attrs.get('Transmisija')), 'collection_method': 'olx_website',
        'source_query': f'https://olx.ba/artikal/{expected_id}', 'source_page': None, 'source_per_page': None}
    try:
        data['posted_date'] = datetime.strptime(attrs['Datum objave'], '%d.%m.%Y').date()
    except (KeyError, ValueError):
        pass
    renewed_at = None
    try:
        renewed_at = datetime.strptime(attrs['Obnovljen'], '%d.%m.%Y u %H:%M').replace(
            tzinfo=ZoneInfo('Europe/Sarajevo')).astimezone(ZoneInfo('UTC')).replace(tzinfo=None)
    except (KeyError, ValueError):
        pass
    if not data['make'] or not data['model']:
        return {**base, 'outcome': 'check_failed', 'evidence': {**base['evidence'], 'reason': 'vehicle_labels_missing'}}
    return {'outcome': 'listed', 'listing': data, 'renewed_at': renewed_at,
            'evidence': {**base['evidence'], 'attributes': attrs}}


class WebsiteBrowser:
    DOM_TIMEOUT = 20
    SCRIPT_TIMEOUT = 5
    MAX_SCRIPT_RETRIES = 2

    def __enter__(self):
        driver_path = os.getenv('CHROMEDRIVER_PATH') or shutil.which('chromedriver')
        if not driver_path:
            raise WebsiteReadError('Install the packaged Chromium driver or use the collector Docker image')
        options = Options()
        # Ads/analytics can keep the load event pending after useful DOM is ready.
        # Readiness and refusal checks below still gate every observation.
        options.page_load_strategy = 'eager'
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')  # Same existing isolated collector container runtime.
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=1440,1000')
        options.add_experimental_option('prefs', {'profile.managed_default_content_settings.images': 2})
        options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
        if os.getenv('CHROME_BIN'):
            options.binary_location = os.environ['CHROME_BIN']
        self.service = Service(driver_path)
        self.driver = None
        try:
            self.driver = webdriver.Chrome(service=self.service, options=options)
            self.driver.set_page_load_timeout(30)
            self.driver.set_script_timeout(self.SCRIPT_TIMEOUT)
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        # Cleanup must also run when setup or quit fails. It must not hide the
        # refusal/timeout that the collector needs to record for this visit.
        driver, self.driver = getattr(self, 'driver', None), None
        try:
            if driver is not None:
                driver.quit()
        except Exception as exc:
            logging.getLogger(__name__).warning('Browser quit failed: %s', type(exc).__name__)
        finally:
            service = getattr(self, 'service', None)
            if service is not None:
                try:
                    service.stop()
                except Exception as exc:
                    logging.getLogger(__name__).warning('Browser service cleanup failed: %s', type(exc).__name__)

    def read(self, url, kind):
        if kind not in {'search', 'detail'}:
            raise ValueError('Unknown website page kind')
        expected_id = listing_id(url) if kind == 'detail' else None
        started = time.monotonic()
        navigation_timeout, script_retries = False, 0
        previous_status = None
        self.driver.get_log('performance')  # Discard stale navigation diagnostics.
        def ready(driver):
            nonlocal script_retries, previous_status
            self.check_responses()
            try:
                page = driver.execute_async_script(EXTRACT_ASYNC_SCRIPT, kind)
            except TimeoutException:
                self.check_responses()
                previous_status = None
                script_retries += 1
                if script_retries > self.MAX_SCRIPT_RETRIES:
                    raise WebsiteDOMTimeout('Renderer did not respond within the DOM retry limit') from None
                return False
            if page['blocked']:
                raise WebsiteBlocked('Website access was refused')
            if kind == 'search':
                return page if page['result_heading'] else False
            result = parse_detail(page, expected_id)
            # ID can render before title, labels and current price. Wait for the
            # parser's full contract rather than accepting an incomplete shell.
            if result['outcome'] == 'listed':
                previous_status = None
                return page
            if result['outcome'] == 'check_failed':
                previous_status = None
                return False
            # Negative templates need two consistent DOM reads, so a transient
            # heading during hydration cannot immediately change availability.
            status = (result['outcome'], result['evidence']['message'])
            if status == previous_status:
                return page
            previous_status = status
            return False
        try:
            try:
                self.driver.get(url)
            except TimeoutException:
                # A navigation timeout can leave a usable DOM. Do not visit again:
                # readiness, HTTP refusal and identity/parser checks still apply.
                self.check_responses()
                navigation_timeout = True
            self.check_responses()
            try:
                page = WebDriverWait(self.driver, self.DOM_TIMEOUT).until(ready)
            except TimeoutException:
                raise WebsiteDOMTimeout('Reserved visit did not produce a verified DOM') from None
        except WebsiteBlocked:
            raise
        except Exception:
            self.check_responses()  # A refusal may have caused the navigation/DOM timeout.
            raise
        self.check_responses()
        if page['blocked']:
            raise WebsiteBlocked('Website access was refused')
        page['browser'] = {'navigation_timeout': navigation_timeout, 'script_retries': script_retries,
                           'elapsed_ms': round((time.monotonic() - started) * 1000)}
        return page

    def check_responses(self):
        server_error = False
        for event in self.driver.get_log('performance'):
            message = json.loads(event['message'])['message']
            if message['method'] != 'Network.responseReceived':
                continue
            response = message['params']['response']
            if urlsplit(response['url']).hostname not in {'olx.ba', 'api.olx.ba'}:
                continue
            if response['status'] in {403, 429}:
                raise WebsiteBlocked('Website access was refused')
            if message['params'].get('type') == 'Document' and response['status'] >= 500:
                server_error = True
        # Inspect the entire batch before reporting an error, so a later refusal
        # still triggers the persistent source pause rather than an ordinary failure.
        if server_error:
            raise WebsiteReadError('Website returned a server error')
