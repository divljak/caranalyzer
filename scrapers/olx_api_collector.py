"""Stable, paginated OLX API collector for active automobile asking prices."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List

import requests

from config.settings import USER_AGENTS, VALIDATION
from scrapers.olx_live_collector import OLXLiveCollector

logger = logging.getLogger(__name__)

API_URL = 'https://api.olx.ba/search'
MAX_PAGES = 5
RESULTS_PER_PAGE = 20


def build_source_query(per_page: int) -> str:
    return f'{API_URL}?category_id=18&page=1&per_page={per_page}&sort_by=date&order=desc'


class CollectionIntegrityError(RuntimeError):
    """Raised when a source page does not behave like stable pagination."""


class OLXAPICollector:
    """Collect bounded, distinct API results with page-level provenance."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.page_stats: List[Dict[str, int]] = []
        self.source_per_page = RESULTS_PER_PAGE
        self.source_query = build_source_query(self.source_per_page)

    def collect(self, pages: int = 1) -> List[Dict[str, Any]]:
        if not 1 <= pages <= MAX_PAGES:
            raise ValueError(f'pages must be between 1 and {MAX_PAGES}')

        self.source_per_page = pages * RESULTS_PER_PAGE
        self.source_query = build_source_query(self.source_per_page)
        payload = self._fetch_page(page=1, per_page=self.source_per_page)
        raw_results = payload.get('data')
        if not isinstance(raw_results, list):
            raise CollectionIntegrityError('OLX API response did not contain a data list')

        raw_ids = [str(item.get('id')) for item in raw_results if item.get('id') is not None]
        unique_items: List[Dict[str, Any]] = []
        observed_ids: set[str] = set()
        duplicate_count = 0
        for item in raw_results:
            listing_id = item.get('id')
            if listing_id is None:
                continue
            listing_id = str(listing_id)
            if listing_id in observed_ids:
                duplicate_count += 1
                continue
            observed_ids.add(listing_id)
            unique_items.append(item)
        if duplicate_count:
            logger.warning('OLX API response contained %s duplicate IDs; duplicates were ignored', duplicate_count)

        listings = [parsed for item in unique_items if (parsed := self._parse_listing(item, page=1, per_page=self.source_per_page))]
        self.page_stats.append({
            'page': 1,
            'per_page': self.source_per_page,
            'api_results': len(raw_results),
            'parsed_results': len(listings),
            'distinct_ids': len(set(raw_ids)),
            'duplicate_ids': duplicate_count,
        })
        logger.info(
            'OLX API request page 1 (per_page=%s): %s API results, %s verified listings, %s distinct IDs',
            self.source_per_page,
            len(raw_results),
            len(listings),
            len(set(raw_ids)),
        )

        if not listings:
            raise CollectionIntegrityError('OLX API returned no valid priced automobile listings')
        return listings

    def _fetch_page(self, page: int, per_page: int) -> Dict[str, Any]:
        response = self.session.get(
            API_URL,
            params={
                'category_id': 18,
                'page': page,
                'per_page': per_page,
                'sort_by': 'date',
                'order': 'desc',
            },
            headers={'Accept': 'application/json', 'User-Agent': USER_AGENTS[0]},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _parse_listing(item: Dict[str, Any], page: int, per_page: int) -> Dict[str, Any] | None:
        listing_id = item.get('id')
        title = ' '.join(str(item.get('title') or '').split())
        price = item.get('price')
        if not listing_id or not title or not isinstance(price, (int, float)):
            return None
        price = int(price)
        if not VALIDATION['min_price'] <= price <= VALIDATION['max_price']:
            logger.warning('Skipping OLX listing %s with implausible price %s', listing_id, price)
            return None

        attributes = {
            str(attribute.get('label', '')).lower(): attribute.get('value')
            for attribute in item.get('special_labels', [])
            if isinstance(attribute, dict)
        }
        make, model = OLXLiveCollector._parse_make_model(title)
        year = OLXAPICollector._as_int(attributes.get('godište'))
        if year is None:
            year = OLXLiveCollector._extract_year([], title)
        mileage = OLXAPICollector._as_int(attributes.get('kilometraža'))
        fuel_type = OLXAPICollector._normalize_fuel(attributes.get('gorivo'))
        if fuel_type is None:
            fuel_type = OLXLiveCollector._extract_fuel([], title)

        posted_date = None
        if isinstance(item.get('date'), (int, float)):
            posted_date = datetime.fromtimestamp(item['date'], tz=timezone.utc).date()

        return {
            'listing_id': str(listing_id),
            'listing_url': f'https://olx.ba/artikal/{listing_id}',
            'title': title,
            'make': make,
            'model': model,
            'year': year,
            'price': price,
            'mileage': mileage,
            'posted_date': posted_date,
            'location': item.get('location'),
            'seller_type': 'dealer' if item.get('user_type') == 'shop' else 'individual',
            'fuel_type': fuel_type,
            'transmission': OLXLiveCollector._extract_transmission(title),
            'description': title,
            'collection_method': 'olx_api',
            'source_query': build_source_query(per_page),
            'source_page': page,
            'source_per_page': per_page,
        }

    @staticmethod
    def _as_int(value: Any) -> int | None:
        if value is None:
            return None
        digits = re.sub(r'\D', '', str(value))
        return int(digits) if digits else None

    @staticmethod
    def _normalize_fuel(value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).lower()
        if normalized in {'dizel', 'diesel'}:
            return 'diesel'
        if normalized in {'benzin', 'petrol'}:
            return 'petrol'
        if normalized in {'hibrid', 'hybrid'}:
            return 'hybrid'
        if 'elektr' in normalized or normalized == 'electric':
            return 'electric'
        return None
