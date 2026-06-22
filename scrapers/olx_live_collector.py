"""Bounded collector for verifiable public OLX.ba automobile listings.

The collector only accepts cards that expose a numeric OLX listing URL, title and
asking price in the rendered page. It deliberately records asking prices, not
claimed sale prices, and stores every collection as a separate snapshot.
"""
from __future__ import annotations

from datetime import datetime
import logging
import re
import time
from typing import Any, Dict, List
from urllib.parse import urljoin

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from config.settings import SCRAPING, USER_AGENTS

logger = logging.getLogger(__name__)

OLX_ORIGIN = 'https://olx.ba'
LISTING_URL_RE = re.compile(r'^https://olx\.ba/artikal/(\d+)/?$')
YEAR_RE = re.compile(r'\b(?:19|20)\d{2}\b')
PRICE_RE = re.compile(r'^(\d[\d.\s]*)\s*KM$', re.IGNORECASE)
MILEAGE_RE = re.compile(r'^(\d[\d.\s]*)\s*km$', re.IGNORECASE)

MAKE_ALIASES = (
    ('Mercedes-Benz', ('mercedes-benz', 'mercedes', 'mb ')),
    ('Volkswagen', ('volkswagen', 'vw ')),
    ('Škoda', ('škoda', 'skoda')),
    ('Citroën', ('citroën', 'citroen')),
    ('Alfa Romeo', ('alfa romeo',)),
    ('Land Rover', ('land rover',)),
    ('BMW', ('bmw',)),
    ('Audi', ('audi',)),
    ('Opel', ('opel',)),
    ('Ford', ('ford',)),
    ('Renault', ('renault',)),
    ('Peugeot', ('peugeot',)),
    ('Toyota', ('toyota',)),
    ('Honda', ('honda',)),
    ('Nissan', ('nissan',)),
    ('Hyundai', ('hyundai',)),
    ('Kia', ('kia',)),
    ('Fiat', ('fiat',)),
    ('Mazda', ('mazda',)),
    ('Volvo', ('volvo',)),
    ('Porsche', ('porsche',)),
    ('Tesla', ('tesla',)),
)


class OLXLiveCollector:
    def __init__(self) -> None:
        self.driver: webdriver.Chrome | None = None

    def __enter__(self) -> 'OLXLiveCollector':
        options = Options()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1440,1200')
        options.add_argument(f'--user-agent={USER_AGENTS[0]}')
        self.driver = webdriver.Chrome(options=options)
        self.driver.set_page_load_timeout(30)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.driver:
            self.driver.quit()
            self.driver = None

    def collect(self, pages: int = 1) -> List[Dict[str, Any]]:
        if not self.driver:
            raise RuntimeError('Collector must be used as a context manager')

        listings: Dict[str, Dict[str, Any]] = {}
        for page in range(1, pages + 1):
            url = f"{SCRAPING['base_url']}&page={page}"
            logger.info('Collecting OLX search page %s: %s', page, url)
            try:
                self.driver.get(url)
                WebDriverWait(self.driver, 20).until(
                    lambda driver: len(driver.find_elements(By.CSS_SELECTOR, 'a[href*="/artikal/"]')) > 0
                )
            except WebDriverException as exc:
                logger.warning('Could not collect OLX page %s: %s', page, exc)
                continue

            for card in self.driver.find_elements(By.CSS_SELECTOR, 'a[href*="/artikal/"]'):
                listing = self._parse_card(card)
                if listing:
                    listings[listing['listing_id']] = listing

            if page < pages:
                time.sleep(2)

        return list(listings.values())

    def _parse_card(self, card) -> Dict[str, Any] | None:
        href = card.get_attribute('href')
        listing_url = urljoin(OLX_ORIGIN, href)
        match = LISTING_URL_RE.match(listing_url)
        if not match:
            return None

        title_elements = card.find_elements(By.CSS_SELECTOR, 'h1.main-heading.normal-heading')
        if not title_elements:
            return None
        title = ' '.join(title_elements[0].text.split())
        if not title:
            return None

        tags = [' '.join(tag.text.split()) for tag in card.find_elements(By.CSS_SELECTOR, '.standard-tag')]
        price = self._extract_price(card)
        if price is None:
            return None

        make, model = self._parse_make_model(title)
        return {
            'listing_id': match.group(1),
            'listing_url': listing_url,
            'title': title,
            'make': make,
            'model': model,
            'year': self._extract_year(tags, title),
            'price': price,
            'mileage': self._extract_mileage(tags),
            'fuel_type': self._extract_fuel(tags, title),
            'transmission': self._extract_transmission(title),
            'description': title,
            'seller_type': 'dealer' if 'PIK SHOP' in card.text else 'individual',
        }

    @staticmethod
    def _extract_price(card) -> int | None:
        for element in card.find_elements(By.CSS_SELECTOR, '.price-wrap .smaller, .smaller'):
            match = PRICE_RE.match(' '.join(element.text.split()))
            if match:
                return int(re.sub(r'\D', '', match.group(1)))
        return None

    @staticmethod
    def _extract_year(tags: List[str], title: str) -> int | None:
        for value in [*tags, title]:
            match = YEAR_RE.search(value)
            if match:
                return int(match.group())
        return None

    @staticmethod
    def _extract_mileage(tags: List[str]) -> int | None:
        for value in tags:
            match = MILEAGE_RE.match(value)
            if match:
                return int(re.sub(r'\D', '', match.group(1)))
        return None

    @staticmethod
    def _extract_fuel(tags: List[str], title: str) -> str | None:
        value = ' '.join([*tags, title]).lower()
        if any(term in value for term in ('dizel', 'diesel', 'tdi', 'hdi', 'dci')):
            return 'diesel'
        if any(term in value for term in ('benzin', 'petrol', 'tfsi', 'tsi')):
            return 'petrol'
        if 'hibrid' in value or 'hybrid' in value:
            return 'hybrid'
        if 'elektr' in value or 'electric' in value:
            return 'electric'
        return None

    @staticmethod
    def _extract_transmission(title: str) -> str | None:
        value = title.lower()
        if any(term in value for term in ('automatik', 'automatic', 'dsg', 'tiptronic')):
            return 'automatic'
        if any(term in value for term in ('manual', 'manuel')):
            return 'manual'
        return None

    @staticmethod
    def _parse_make_model(title: str) -> tuple[str, str]:
        normalized = title.lower()
        for make, aliases in MAKE_ALIASES:
            for alias in aliases:
                if normalized.startswith(alias):
                    consumed = len(alias.rstrip())
                    return make, OLXLiveCollector._normalize_model(title[consumed:].strip(' -,.'))
        parts = title.split(maxsplit=1)
        return parts[0].title(), OLXLiveCollector._normalize_model(parts[1] if len(parts) > 1 else '')

    @staticmethod
    def _normalize_model(value: str) -> str:
        """Keep a stable model family while preserving the full verified title separately."""
        tokens = value.split()
        if not tokens:
            return 'Unspecified'
        if len(tokens) >= 2 and tokens[0].lower() in {'a', 'c', 'e', 's', 'g'} and tokens[1][0].isdigit():
            return f'{tokens[0].upper()} {tokens[1]}'
        if len(tokens) >= 2 and tokens[1].upper() in {'CC', 'COUPE', 'SPORTBACK'}:
            return f'{tokens[0]} {tokens[1].title()}'
        return tokens[0][:100]
