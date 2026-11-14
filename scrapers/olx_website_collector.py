"""Read rendered website pages only. No direct OLX API calls; the only retry is one bounded page-1 retry in the caller."""
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
// Screen-reader prefixes ("Akcijska cijena:") and struck-through old prices are not the current price.
const priceText = e => {
  if (!e) return '';
  const copy = e.cloneNode(true);
  copy.querySelectorAll('.sr-only, del').forEach(x => x.remove());
  return copy.textContent.replace(/\s+/g, ' ').trim();
};
const headings = Array.from(document.querySelectorAll('h1,h2,h3')).filter(visible).map(clean);
const attributes = {};
// Legacy layout (until Nov 2026): label/value <h4> pairs.
for (const h of document.querySelectorAll('h4')) {
  if (!visible(h)) continue;
  const next = h.nextElementSibling;
  if (next && next.tagName === 'H4') attributes[clean(h)] = clean(next);
}
// PIK layout (from 10 Nov 2026): <dt>label</dt><dd>value</dd>; "ID oglasa" is the listing ID.
for (const dt of document.querySelectorAll('dl dt')) {
  const dd = dt.nextElementSibling;
  if (!dd || dd.tagName !== 'DD') continue;
  const key = clean(dt) === 'ID oglasa' ? 'ID' : clean(dt);
  if (key && !(key in attributes)) attributes[key] = clean(dd);
}
let cards = [];
if (arguments[0] === 'search') {
  cards = Array.from(document.querySelectorAll('a[href*="/artikal/"]'))
    .filter(a => visible(a) && a.querySelector('h1.main-heading'))
    .map(a => ({url:a.getAttribute('href'), title:clean(a.querySelector('h1.main-heading')),
      price:clean(a.querySelector('.price-wrap .smaller:not(.discount)')),
      tags:Array.from(a.querySelectorAll('.standard-tag')).map(clean),
      dealer:/PIK SHOP/.test(a.innerText)}));
  // PIK cards do not show "PIK SHOP" any more, but the page's own Nuxt payload (#__NUXT_DATA__, a
  // devalue array whose object fields are indexes into the array) carries shop:true|false per ad.
  // Only a real boolean for a matching ad id counts; anything else leaves seller type unknown (null).
  const shopById = {};
  try {
    const node = document.getElementById('__NUXT_DATA__');
    const data = node ? JSON.parse(node.textContent) : null;
    if (Array.isArray(data)) {
      const at = i => (Number.isInteger(i) && i >= 0 && i < data.length) ? data[i] : undefined;
      for (const item of data) {
        if (!item || typeof item !== 'object' || Array.isArray(item)) continue;
        if (!('shop' in item && 'title' in item && 'id' in item)) continue;
        const id = at(item.id), shop = at(item.shop);
        if ((typeof id === 'number' || typeof id === 'string') && typeof shop === 'boolean') shopById[String(id)] = shop;
      }
    }
  } catch (e) { /* no payload: seller type stays unknown */ }
  const adId = href => { const m = /\/artikal\/(?:[^/]*-)?(\d{6,20})\/?$/.exec(href || ''); return m ? m[1] : null; };
  cards = cards.concat(Array.from(document.querySelectorAll('article.listing-card')).filter(visible)
    .map(card => [card, card.querySelector('a.listing-card__title-link[href*="/artikal/"]')])
    .filter(([card, link]) => link)
    .map(([card, link]) => ({url:link.getAttribute('href'), title:clean(link),
      price:priceText(card.querySelector('.listing-card__price strong')),
      tags:Array.from(card.querySelectorAll('.listing-card__attributes > span')).map(clean),
      dealer:(adId(link.getAttribute('href')) in shopById) ? shopById[adId(link.getAttribute('href'))] : null})));
}
let currentPage = null, lastPage = null;
const pager = document.querySelector('nav.ui-pagination');
if (pager) {
  const numbers = Array.from(pager.querySelectorAll('.ui-pagination__page'))
    .map(e => clean(e)).filter(t => /^\d+$/.test(t)).map(Number);
  const current = pager.querySelector('.ui-pagination__page.is-current, .ui-pagination__list [aria-current="page"]');
  lastPage = numbers.length ? Math.max(...numbers) : null;
  currentPage = current && /^\d+$/.test(clean(current)) ? Number(clean(current)) : null;
} else {
  const pageItems = Array.from(document.querySelectorAll('li')).filter(e => visible(e) && /^\d+$/.test(clean(e)));
  const activePage = pageItems.find(e => e.classList.contains('active'));
  currentPage = activePage ? Number(clean(activePage)) : null;
  lastPage = pageItems.length ? Math.max(...pageItems.map(e => Number(clean(e)))) : null;
}
const legacyHeading = document.querySelector('h1.search-title');
const resultCount = legacyHeading ? clean(legacyHeading) :
  clean(document.querySelector('#search-results-title ~ p[role="status"]'));
const mainTitle = document.querySelector('h1.main-title-listing') || document.querySelector('h1#article-title');
const prices = Array.from(document.querySelectorAll('.price-heading')).filter(visible).map(clean)
  .concat(Array.from(document.querySelectorAll('.article-price__amount')).filter(visible).map(priceText));
const onRequest = Array.from(document.querySelectorAll('.pricing')).filter(visible).some(e => /Na upit|Po dogovoru/i.test(e.innerText));
return {url:location.href, title:document.title, blocked, headings, attributes, cards,
  result_heading:resultCount, current_page:currentPage, last_page:lastPage,
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
    # PIK URLs carry a title slug before the ID: /artikal/peugeot-308-2013-76348132.
    # A slugged ID must be 6+ digits so "/artikal/peugeot-308" is never read as ad 308.
    match = (re.fullmatch(r'/artikal/(\d{1,20})/?', parsed.path)
             or re.fullmatch(r'/artikal/[^/]*-(\d{6,20})/?', parsed.path))
    if not match:
        raise WebsiteReadError('Unexpected listing URL')
    return match.group(1)


def verified_search_last_page(page, requested_page, total, card_count):
    """Missing controls prove one page only when every advertised result is present."""
    if total is None or (total > 0 and not card_count) or (total == 0 and card_count):
        raise WebsiteReadError('Search result cards are not ready')
    current, last = page.get('current_page'), page.get('last_page')
    if type(current) is int and type(last) is int and current == requested_page and last >= current:
        return last
    if requested_page == 1 and last is None and current in (None, 1) and card_count == total:
        return 1
    raise WebsiteReadError('Search pagination is not verified')


def result_count_match(text):
    """Legacy "601 REZULTATA" or PIK "47.810 oglasa"; anything else is not a verified count."""
    return re.fullmatch(r'([\d.\s]+)\s+(?:rezultata|oglasa)', text or '', re.I)


def dotless_date(value):
    """PIK writes "05.05.2026." and "14.11.2026. u 22:01"; drop the trailing year dot only."""
    return re.sub(r'^(\d{2}\.\d{2}\.\d{4})\.', r'\1', value.strip())


def search_card_ids(cards):
    ids = set()
    for card in cards:
        try:
            ids.add(listing_id(card.get('url', '')))
        except WebsiteReadError:
            continue
    return ids


def parse_search(page, requested_page, query):
    if page.get('blocked'):
        raise WebsiteBlocked('Website access was refused')
    parsed_url = urlsplit(page.get('url', ''))
    if parsed_url.netloc != 'olx.ba' or parsed_url.path.rstrip('/') != '/pretraga':
        raise WebsiteReadError('Search redirected away from its scope')
    actual_query, expected_query = parse_qs(parsed_url.query), parse_qs(urlsplit(query).query)
    if any(actual_query.get(k) != v for k, v in expected_query.items() if k != 'page'):
        raise WebsiteReadError('Search filters changed during navigation')
    result_match = result_count_match(page.get('result_heading', ''))
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
                # None = the card does not say; an unknown never overwrites a stored seller type.
                'seller_type': {True: 'dealer', False: 'individual'}.get(card.get('dealer')),
                'collection_method': 'olx_website', 'source_query': query,
                'source_page': requested_page, 'source_per_page': None})
            seen.add(lid)
        except (KeyError, ValueError, WebsiteReadError):
            rejected += 1
    last_page = verified_search_last_page(page, requested_page, total,
                                          len(search_card_ids(page.get('cards', []))))
    return {'listings': listings, 'source_total': total, 'last_page': last_page,
            'raw_cards': len(page.get('cards', [])), 'rejected': rejected, 'duplicates': duplicates}


# Conservative exact messages. Unknown templates are check failures, never removals.
UNAVAILABLE_MESSAGES = {'oglas nije dostupan', 'ovaj oglas nije dostupan', 'oglas ne postoji', 'ovaj oglas ne postoji'}
ENDED_MESSAGES = {'oglas je završen', 'ovaj oglas je završen'}
SOLD_MESSAGES = {'oglas je prodan', 'ovaj oglas je prodan'}
# OLX serves a removed ad as an HTTP 404 document whose only heading is the generic
# "page not found" text. Both signals are required; the heading alone stays unknown.
NOT_FOUND_PREFIX = 'oprostite, ne možemo pronaći ovu stranicu'
# PIK layout (from 10 Nov 2026): a missing ad is a 404 document titled "Oglas nije pronađen".
NOT_FOUND_MESSAGES = (NOT_FOUND_PREFIX, 'oglas nije pronađen')


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
    if (page.get('document_status') == 404 and not page.get('main_title')
            and not page.get('attributes', {}).get('ID')
            and any(h.startswith(m) for h in headings for m in NOT_FOUND_MESSAGES)):
        message = next(m for m in NOT_FOUND_MESSAGES if any(h.startswith(m) for h in headings))
        return {**base, 'outcome': 'unavailable',
                'evidence': {**base['evidence'], 'message': message, 'http_status': 404}}
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
        data['posted_date'] = datetime.strptime(dotless_date(attrs['Datum objave']), '%d.%m.%Y').date()
    except (KeyError, ValueError):
        pass
    renewed_at = None
    try:
        renewed_at = datetime.strptime(dotless_date(attrs['Obnovljen']), '%d.%m.%Y u %H:%M').replace(
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

    def restart(self):
        """Fresh Chromium process for the single bounded page-1 retry. Never used after a refusal."""
        self.close()
        return self.__enter__()

    def page_state(self):
        """Small, non-personal view of where the browser ended up; never the page body."""
        try:
            state = self.driver.execute_script(
                'return {final_url: location.href, title: document.title, ready_state: document.readyState};')
            return {'final_url': str(state.get('final_url', ''))[:300], 'title': str(state.get('title', ''))[:120],
                    'ready_state': str(state.get('ready_state', ''))[:20]}
        except Exception as exc:
            return {'page_state_error': type(exc).__name__}

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
        navigation_ms = dom_ms = None
        navigation_timeout, script_retries = False, 0
        previous_status = None
        last_dom, parser_reason = {}, None
        self.driver.get_log('performance')  # Discard stale navigation diagnostics.
        self.document_status = None
        def ready(driver):
            nonlocal script_retries, previous_status, last_dom, parser_reason
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
            # Diagnostic flags only: no seller text, contact data or full DOM.
            last_dom = {'cards': len(page.get('cards', [])),
                        'pagination_present': page.get('current_page') is not None and page.get('last_page') is not None,
                        'title_present': bool(page.get('main_title')),
                        'expected_id_matched': page.get('attributes', {}).get('ID') == expected_id if expected_id else None,
                        'price_values': len(page.get('prices', [])),
                        'on_request': bool(page.get('on_request'))}
            if kind == 'search':
                heading = result_count_match(page.get('result_heading', ''))
                if not heading:
                    return False
                requested = int(parse_qs(urlsplit(url).query).get('page', ['1'])[0])
                try:
                    verified_search_last_page(page, requested, numeric(heading.group(1)),
                                              len(search_card_ids(page.get('cards', []))))
                except WebsiteReadError:
                    parser_reason = 'search_cards_or_pagination_not_verified'
                    return False
                return page
            page['document_status'] = getattr(self, 'document_status', None)
            result = parse_detail(page, expected_id)
            parser_reason = result.get('evidence', {}).get('reason')
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
        def diagnostics():
            # Structured telemetry for failed visits: phase, timings and where the
            # browser ended up. No page body, seller text or contact data.
            return {'stage': kind, 'url': url, 'navigation_timeout': navigation_timeout,
                    'navigation_ms': navigation_ms, 'dom_ms': dom_ms,
                    'script_retries': script_retries,
                    'elapsed_ms': round((time.monotonic() - started) * 1000),
                    'document_status': getattr(self, 'document_status', None),
                    'last_dom': last_dom, 'parser_reason': parser_reason, **self.page_state()}
        try:
            try:
                self.driver.get(url)
            except TimeoutException:
                # A navigation timeout can leave a usable DOM. Do not visit again:
                # readiness, HTTP refusal and identity/parser checks still apply.
                self.check_responses()
                navigation_timeout = True
            navigation_ms = round((time.monotonic() - started) * 1000)
            self.check_responses()
            dom_started = time.monotonic()
            try:
                page = WebDriverWait(self.driver, self.DOM_TIMEOUT).until(ready)
            except TimeoutException:
                raise WebsiteDOMTimeout('Reserved visit did not produce a verified DOM') from None
            finally:
                dom_ms = round((time.monotonic() - dom_started) * 1000)
        except WebsiteBlocked as exc:
            exc.diagnostics = diagnostics()
            raise
        except WebsiteDOMTimeout as exc:
            exc.diagnostics = diagnostics()
            try:
                self.check_responses()
            except WebsiteBlocked as blocked:
                blocked.diagnostics = exc.diagnostics
                raise
            raise
        except Exception as exc:
            if getattr(exc, 'diagnostics', None) is None:
                try:
                    exc.diagnostics = diagnostics()
                except Exception:
                    pass
            self.check_responses()  # A refusal may have caused the navigation/DOM timeout.
            raise
        self.check_responses()
        if page['blocked']:
            raise WebsiteBlocked('Website access was refused')
        page['browser'] = {'navigation_timeout': navigation_timeout, 'script_retries': script_retries,
                           'navigation_ms': navigation_ms, 'dom_ms': dom_ms,
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
            if message['params'].get('type') == 'Document':
                self.document_status = response['status']
                if response['status'] >= 500:
                    server_error = True
        # Inspect the entire batch before reporting an error, so a later refusal
        # still triggers the persistent source pause rather than an ordinary failure.
        if server_error:
            raise WebsiteReadError('Website returned a server error')
