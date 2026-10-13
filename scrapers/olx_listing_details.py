"""Read seller-entered vehicle attributes without retaining user/contact payloads."""
import re
import requests

from config.settings import USER_AGENTS


class DetailsUnavailable(RuntimeError):
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__('OLX listing details are unavailable')


def normalize_transmission(value):
    if not isinstance(value, str):
        return None
    value = ' '.join(value.casefold().split())
    if value in {'automatik', 'automatic', 'automatski', 'automatski mjenjač', 'automatski mjenjac'}:
        return 'automatic'
    if value in {'manuelni', 'manual', 'manualni', 'manuel', 'ručni', 'rucni', 'ručni mjenjač', 'rucni mjenjac'}:
        return 'manual'
    return None


def parse_transmission(payload, listing_id):
    """Accept only a matching car ad and its explicit transmission attribute."""
    if not isinstance(payload, dict) or str(payload.get('id')) != str(listing_id) or payload.get('category_id') != 18:
        raise ValueError('OLX returned a different listing or category')
    attributes = payload.get('attributes')
    if not isinstance(attributes, list):
        return {'transmission': None, 'raw_value': None}
    values = [a.get('value') for a in attributes if isinstance(a, dict) and
              (a.get('attr_code') == 'transmisija' or str(a.get('name', '')).casefold() == 'transmisija')]
    raw = next((v for v in values if isinstance(v, str) and v.strip()), None)
    # Conflicting attributes are not resolved by guessing.
    normalized = {normalize_transmission(v) for v in values if isinstance(v, str) and v.strip()}
    transmission = next(iter(normalized)) if len(normalized) == 1 else None
    return {'transmission': transmission, 'raw_value': raw[:80] if raw else None}


def fetch_transmission(session, listing_id):
    if not re.fullmatch(r'\d{1,20}', str(listing_id)):
        raise ValueError('Invalid OLX listing ID')
    url = f'https://api.olx.ba/listings/{listing_id}'
    response = session.get(url, headers={'Accept': 'application/json', 'User-Agent': USER_AGENTS[0]}, timeout=(5, 15))
    if response.status_code != 200:
        raise DetailsUnavailable(response.status_code)
    result = parse_transmission(response.json(), listing_id)
    return {**result, 'source_url': url}


DETAILS_DDL = """
CREATE TABLE IF NOT EXISTS dealer_listing_details (
 listing_id VARCHAR(50) PRIMARY KEY REFERENCES car_listings(listing_id) ON DELETE RESTRICT,
 transmission VARCHAR(20) CHECK (transmission IN ('manual','automatic')),
 raw_value VARCHAR(80),
 source_url VARCHAR(200) NOT NULL,
 checked_at TIMESTAMP NOT NULL,
 outcome VARCHAR(30) NOT NULL CHECK (outcome IN ('collected','not_specified','unavailable'))
)
;
CREATE TABLE IF NOT EXISTS dealer_source_pauses (
 source VARCHAR(40) PRIMARY KEY,
 resume_at TIMESTAMP NOT NULL
)
"""

def details_resume_at(connection):
    from sqlalchemy import text
    return connection.execute(text("SELECT resume_at FROM dealer_source_pauses WHERE source='olx_listing_details' AND resume_at>(now() AT TIME ZONE 'UTC')")).scalar()


def pause_details(connection):
    from sqlalchemy import text
    return connection.execute(text("""INSERT INTO dealer_source_pauses(source,resume_at)
      VALUES ('olx_listing_details',(now() AT TIME ZONE 'UTC')+interval '30 minutes')
      ON CONFLICT(source) DO UPDATE SET resume_at=greatest(dealer_source_pauses.resume_at,excluded.resume_at)
      RETURNING resume_at""")).scalar()

def store_transmission(connection, listing_id, details, checked_at, outcome):
    from sqlalchemy import text
    connection.execute(text("""INSERT INTO dealer_listing_details(listing_id,transmission,raw_value,source_url,checked_at,outcome)
      VALUES (:id,:transmission,:raw_value,:source_url,:checked_at,:outcome)
      ON CONFLICT(listing_id) DO UPDATE SET transmission=excluded.transmission,raw_value=excluded.raw_value,
      source_url=excluded.source_url,checked_at=excluded.checked_at,outcome=excluded.outcome
      WHERE dealer_listing_details.transmission IS NULL OR excluded.outcome <> 'unavailable'"""),
      {'id':listing_id, 'transmission':details.get('transmission'), 'raw_value':details.get('raw_value'),
       'source_url':details.get('source_url',f'https://api.olx.ba/listings/{listing_id}'),
       'checked_at':checked_at, 'outcome':outcome})
