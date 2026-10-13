"""Private dealer workspace. Existing collector evidence is read-only here."""
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
import logging
import threading
import time
import uuid
import json
import hashlib
import os
import requests

from fastapi import FastAPI, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from database.models import create_engine_and_session
from scrapers.olx_listing_details import DETAILS_DDL, DetailsUnavailable, fetch_transmission, store_transmission, details_resume_at, pause_details
from market_labels import LABEL_DDL, refresh_labels
from database.website_tracking import create_website_tables

HOSTED = os.getenv('VERCEL') == '1'
if HOSTED:
    from hosted_runtime import hosted_engine, hosted_hosts, same_origin
    engine = hosted_engine()
else:
    from hosted_runtime import same_origin
    engine, _ = create_engine_and_session()
logger = logging.getLogger(__name__)
LISTING_SOURCE = """(SELECT c0.*,
 CASE WHEN w0.details_checked_at IS NOT NULL THEN w0.attributes::jsonb->>'Proizvođač' ELSE COALESCE(n.make,c0.make) END AS market_make,
 CASE WHEN w0.details_checked_at IS NOT NULL THEN w0.attributes::jsonb->>'Model' ELSE COALESCE(n.model,c0.model) END AS market_model,
 (w0.details_checked_at IS NOT NULL OR (n.make IS NOT NULL AND n.model IS NOT NULL)) AS recognized,
 CASE WHEN w0.details_checked_at IS NOT NULL AND (e0.checked_at IS NULL OR w0.details_checked_at>=e0.checked_at)
      THEN w0.transmission ELSE COALESCE(e0.transmission,c0.transmission) END AS effective_transmission,
 CASE WHEN w0.details_checked_at IS NOT NULL AND (e0.checked_at IS NULL OR w0.details_checked_at>=e0.checked_at)
      THEN 'olx_website_attribute' WHEN e0.transmission IS NOT NULL THEN 'olx_attribute'
      WHEN c0.transmission IS NOT NULL THEN 'ad_title' END AS effective_transmission_source,
 CASE WHEN w0.details_checked_at IS NOT NULL AND (e0.checked_at IS NULL OR w0.details_checked_at>=e0.checked_at)
      THEN w0.details_checked_at ELSE e0.checked_at END AS effective_transmission_checked_at,
 n.category,n.category_basis FROM car_listings c0 LEFT JOIN dealer_vehicle_labels n USING(listing_id)
 LEFT JOIN website_listing_states w0 USING(listing_id) LEFT JOIN dealer_listing_details e0 USING(listing_id)) c
 LEFT JOIN dealer_listing_details e USING(listing_id) LEFT JOIN website_listing_states w USING(listing_id)"""
detail_jobs = {}
detail_job_lock = threading.Lock()

CANDIDATE_DDL = """
CREATE TABLE IF NOT EXISTS dealer_candidates (
 listing_id VARCHAR(50) PRIMARY KEY REFERENCES car_listings(listing_id) ON DELETE RESTRICT,
 status VARCHAR(20) NOT NULL DEFAULT 'shortlisted' CHECK (status IN ('shortlisted','contacted','inspect','passed')),
 note TEXT NOT NULL DEFAULT '',
 saved_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC'),
 updated_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')
)
"""
SEARCH_DDL = """CREATE TABLE IF NOT EXISTS dealer_model_searches (
 id VARCHAR(32) PRIMARY KEY, fingerprint VARCHAR(64) NOT NULL UNIQUE,
 filters JSONB NOT NULL, baseline JSONB NOT NULL, baseline_as_of TIMESTAMP NOT NULL,
 saved_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC'))"""

@asynccontextmanager
async def lifespan(app):
    if HOSTED:
        # Imported schema and labels are managed outside request-serving workers.
        yield
        return
    create_website_tables(engine)
    with engine.begin() as connection:
        connection.execute(text(CANDIDATE_DDL))
        connection.execute(text(DETAILS_DDL))
        connection.execute(text(LABEL_DDL))
        connection.execute(text(SEARCH_DDL))
        refresh_labels(connection)
    yield

app = FastAPI(title='Car Analyzer · Dealer workspace', lifespan=lifespan,
              docs_url=None if HOSTED else '/docs', redoc_url=None if HOSTED else '/redoc',
              openapi_url=None if HOSTED else '/openapi.json')
app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosted_hosts() if HOSTED else ['localhost', '127.0.0.1', 'testserver'])

@app.middleware('http')
async def private_requests(request: Request, call_next):
    # Hobby Standard Protection covers preview URLs. Production must stay closed.
    if HOSTED and os.getenv('VERCEL_ENV') != 'preview':
        return JSONResponse({'detail': 'This workspace requires a protected preview deployment'}, status_code=503)
    if HOSTED and (request.url.path.startswith('/api/candidates') or
                   request.url.path.startswith('/api/transmission-jobs') or
                   request.url.path == '/api/model-transmission'):
        return JSONResponse({'detail': 'Source refresh is available only in the local collector workspace'}, status_code=409)
    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        origin = request.headers.get('origin')
        if not same_origin(origin, request.headers.get('host'), str(request.base_url), HOSTED):
            return JSONResponse({'detail': 'Cross-origin writes are disabled'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': 'Cross-site writes are disabled'}, status_code=403)
    try:
        response = await call_next(request)
    except Exception as error:
        logger.error('Private workspace request failed: %s', type(error).__name__)
        return JSONResponse({'detail': 'Database unavailable. Please retry later.'}, status_code=503,
                            headers={'Cache-Control': 'no-store'})
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response

class Filters(BaseModel):
    make: str = Field(default='', max_length=80)
    model: str = Field(default='', max_length=100)
    year_min: int = Field(default=1990, ge=1900, le=2100)
    year_max: int = Field(default_factory=lambda: date.today().year, ge=1900, le=2100)
    price_max: int | None = Field(default=None, ge=1)
    mileage_min: int | None = Field(default=None, ge=0)
    mileage_max: int | None = Field(default=None, ge=0)
    fuel: Literal['', 'diesel', 'petrol', 'hybrid', 'electric'] = ''
    transmission: Literal['', 'manual', 'automatic'] = ''
    category: Literal['', 'suv', 'sedan', 'hatchback', 'estate', 'unknown'] = ''
    days: int = Field(default=30, ge=1, le=365)

    @model_validator(mode='after')
    def ordered_years(self):
        if self.year_min > self.year_max:
            raise ValueError('Minimum year must be at most maximum year')
        if self.mileage_min is not None and self.mileage_max is not None and self.mileage_min > self.mileage_max:
            raise ValueError('Minimum mileage must be at most maximum mileage')
        return self

class Segment(BaseModel):
    make: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=100)

class Comparison(BaseModel):
    segments: list[Segment] = Field(min_length=1, max_length=3)
    filters: Filters = Field(default_factory=Filters)

class Explanation(Comparison):
    question: str = Field(default='Which is easier to resell?', min_length=1, max_length=1000)

class Candidate(BaseModel):
    status: Literal['shortlisted', 'contacted', 'inspect', 'passed'] = 'shortlisted'
    note: str = Field(default='', max_length=3000)

def rows(connection, sql, params=None):
    return [dict(row) for row in connection.execute(text(sql), params or {}).mappings()]

def utc(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value is not None else None

def as_of(connection):
    return connection.execute(text("SELECT max(end_time) FROM scraping_logs WHERE status='completed' AND errors_count=0")).scalar()

def cohort(filters, anchor):
    params = filters.model_dump()
    params['cutoff'] = anchor - timedelta(days=filters.days)
    params['anchor'] = anchor
    conditions = ["c.source='olx.ba'", "c.collection_method IN ('olx_api','olx_website')", 'c.last_verified_at >= :cutoff', 'c.last_verified_at <= :anchor', 'c.year BETWEEN :year_min AND :year_max']
    for key in ['make', 'model']:
        if params[key]:
            conditions.append(f'lower(trim(c.market_{key}))=lower(trim(:{key}))')
    if filters.category:
        conditions.append('c.category IS NULL' if filters.category == 'unknown' else 'c.category=:category')
    if filters.price_max is not None:
        conditions.extend(["c.price_status='priced'", 'c.price > 0', 'c.price <= :price_max'])
    if filters.mileage_max is not None:
        conditions.append('c.mileage BETWEEN 0 AND :mileage_max')
    if filters.mileage_min is not None:
        conditions.append('c.mileage >= :mileage_min')
    for key, field in [('fuel', 'fuel_type'), ('transmission', 'transmission')]:
        if params[key]:
            expression = 'c.effective_transmission' if key == 'transmission' else f'c.{field}'
            conditions.append(f'{expression}=:{key}')
    return ' AND '.join(conditions), params

@app.get('/api/overview')
def overview():
    with engine.begin() as connection:
        if not HOSTED:
            refresh_labels(connection)
        data = rows(connection, """SELECT (SELECT count(*) FROM car_listings) AS listings,
          (SELECT count(*) FROM car_listings WHERE collection_method='olx_api' AND source='olx.ba') AS api_listings,
          (SELECT count(*) FROM listing_snapshots) AS snapshots,
          (SELECT count(*) FROM scraping_logs) AS runs""")[0]
        data['website_listings'] = connection.execute(text("SELECT count(*) FROM car_listings WHERE source='olx.ba' AND collection_method='olx_website'")).scalar()
        health = rows(connection, 'SELECT status,started_at,finished_at,search_attempted,detail_attempted,details_completed,errors,report FROM website_collection_runs ORDER BY started_at DESC,id DESC LIMIT 1')
        data['website_collection'] = health[0] if health else None
        if health:
            for field in ('started_at','finished_at'):
                data['website_collection'][field] = utc(data['website_collection'][field])
            data['website_collection']['report'] = json.loads(data['website_collection']['report'] or '{}')
        data['as_of'] = utc(as_of(connection))
        data['coverage'] = 'Bounded newest-results sample; model coverage is unknown'
        data['availability'] = 'Last observed on OLX; current availability is unverified'
        data['confirmed_sales'] = None
        data['assistant_mode'] = 'deterministic_evidence'
        data['models'] = rows(connection, f"""SELECT lower(trim(market_make)) AS key_make, lower(trim(market_model)) AS key_model,
          min(market_make) AS make, min(market_model) AS model, count(*) AS sample
          FROM {LISTING_SOURCE} WHERE source='olx.ba' AND collection_method IN ('olx_api','olx_website')
          AND recognized GROUP BY 1,2 ORDER BY sample DESC""")
        data['labels'] = rows(connection,f"""SELECT count(*) FILTER (WHERE recognized) AS recognized,
          count(*) FILTER (WHERE NOT recognized) AS needs_review,
          count(*) FILTER (WHERE category IS NOT NULL) AS categorized
          FROM {LISTING_SOURCE} WHERE source='olx.ba' AND collection_method IN ('olx_api','olx_website')""")[0]
        return data

def query_filters(
    make: str = Query(default='', max_length=80),
    model: str = Query(default='', max_length=100),
    year_min: int = Query(default=1990, ge=1900, le=2100),
    year_max: int = Query(default=date.today().year, ge=1900, le=2100),
    price_max: int | None = Query(default=None, ge=1),
    mileage_min: int | None = Query(default=None, ge=0),
    mileage_max: int | None = Query(default=None, ge=0),
    fuel: Literal['', 'diesel', 'petrol', 'hybrid', 'electric'] = '',
    transmission: Literal['', 'manual', 'automatic'] = '',
    category: Literal['', 'suv', 'sedan', 'hatchback', 'estate', 'unknown'] = '',
    days: int = Query(default=30, ge=1, le=365),
):
    if year_min > year_max:
        raise HTTPException(422, 'Minimum year must be at most maximum year')
    if mileage_min is not None and mileage_max is not None and mileage_min > mileage_max:
        raise HTTPException(422, 'Minimum mileage must be at most maximum mileage')
    return Filters(make=make, model=model, year_min=year_min, year_max=year_max,
                   price_max=price_max, mileage_min=mileage_min, mileage_max=mileage_max, fuel=fuel,
                   transmission=transmission, category=category, days=days)

@app.get('/api/listings')
def listings(filters: Filters = Depends(query_filters), page: int = Query(default=1, ge=1), size: int = Query(default=24, ge=1, le=100),
             sort: Literal['observed', 'price_asc', 'price_desc', 'mileage', 'year'] = 'observed'):
    with engine.connect() as connection:
        anchor = as_of(connection)
        if not anchor:
            return {'items': [], 'total': 0, 'page': page, 'as_of': None}
        where, params = cohort(filters, anchor)
        total = connection.execute(text(f'SELECT count(*) FROM {LISTING_SOURCE} WHERE {where}'), params).scalar()
        params.update(offset=(page-1)*size, size=size)
        price_order = "CASE WHEN c.price_status='priced' AND c.price>0 THEN c.price END"
        order = {'observed': 'c.last_verified_at DESC', 'price_asc': f'{price_order} ASC NULLS LAST',
                 'price_desc': f'{price_order} DESC NULLS LAST', 'mileage': 'c.mileage ASC NULLS LAST', 'year': 'c.year DESC'}[sort]
        items = rows(connection, f"""SELECT c.listing_id,c.market_make AS make,c.market_model AS model,c.category,c.category_basis,c.year,c.price,c.price_status,c.mileage,
          c.fuel_type,c.effective_transmission AS transmission,
          c.effective_transmission_source AS transmission_source,
          c.effective_transmission_checked_at AS transmission_checked_at,e.outcome AS transmission_detail_outcome,c.first_seen_at,c.last_verified_at,c.consecutive_missing_runs,
          c.description, d.status AS candidate_status, COALESCE(w.status,'unverified') AS availability,
          w.last_checked_at AS availability_checked_at,w.last_outcome AS availability_last_outcome,c.collection_method
          FROM {LISTING_SOURCE} LEFT JOIN dealer_candidates d USING(listing_id)
          WHERE {where} ORDER BY {order},c.listing_id LIMIT :size OFFSET :offset""", params)
        for item in items:
            item['listing_url'] = f"https://olx.ba/artikal/{item['listing_id']}"
            item['last_verified_at'] = utc(item['last_verified_at'])
            item['first_seen_at'] = utc(item['first_seen_at'])
            item['availability_checked_at'] = utc(item['availability_checked_at'])
            item['transmission_checked_at'] = utc(item['transmission_checked_at'])
            if item['price_status'] != 'priced' or not item['price'] or item['price'] <= 0:
                item['price'] = None
        return {'items': items, 'total': total, 'page': page, 'as_of': utc(anchor)}

# Aggregate each listing once; use snapshots only for actual observed price transitions.
def calculate_comparison(connection, request):
    anchor = as_of(connection)
    if not anchor:
        raise HTTPException(409, 'No successful collection is available')
    result = []
    for segment in request.segments:
        f = request.filters.model_copy(update={'make': segment.make, 'model': segment.model})
        where, params = cohort(f, anchor)
        values = rows(connection, f"""
          WITH sample AS (SELECT c.* FROM {LISTING_SOURCE} WHERE {where}),
          unique_observations AS (
            SELECT DISTINCT ON (s.listing_id,s.observed_at) s.listing_id,s.observed_at,s.asking_price FROM listing_snapshots s
            JOIN sample c USING(listing_id) WHERE s.price_status='priced' AND s.asking_price>0 AND s.observed_at<=:anchor
            ORDER BY s.listing_id,s.observed_at,s.id DESC
          ), transitions AS (
            SELECT *, lag(asking_price) OVER (PARTITION BY listing_id ORDER BY observed_at) AS previous_price
            FROM unique_observations
          ), cuts AS (
            SELECT count(DISTINCT listing_id) AS price_cut_listings FROM transitions
            WHERE observed_at>=:cutoff AND asking_price<previous_price
          )
          SELECT count(*) AS sample_size,
            count(*) FILTER (WHERE price_status='priced' AND price>0) AS priced_sample,
            percentile_cont(0.25) WITHIN GROUP (ORDER BY price) FILTER (WHERE price_status='priced' AND price>0) AS p25,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY price) FILTER (WHERE price_status='priced' AND price>0) AS median,
            percentile_cont(0.75) WITHIN GROUP (ORDER BY price) FILTER (WHERE price_status='priced' AND price>0) AS p75,
            count(*) FILTER (WHERE first_seen_at>=:anchor - interval '14 days') AS first_seen_14d,
            count(*) FILTER (WHERE consecutive_missing_runs>0) AS outside_window,
            min(year) AS year_min, max(year) AS year_max,
            min(last_verified_at) AS oldest_observation, max(last_verified_at) AS newest_observation,
            (SELECT price_cut_listings FROM cuts) AS price_cut_listings FROM sample
        """, params)[0]
        for field in ['oldest_observation', 'newest_observation']:
            values[field] = utc(values[field])
        values.update(make=segment.make, model=segment.model, coverage='unknown', sample_warning='Small sample' if values['priced_sample'] < 10 else 'Bounded sample')
        result.append(values)
    return {'as_of': utc(anchor), 'filters': request.filters.model_dump(), 'segments': result, 'resale_verdict': 'insufficient_evidence', 'confirmed_sales': None, 'days_to_sell': None}

@app.post('/api/compare')
def compare(request: Comparison):
    with engine.connect() as connection:
        return calculate_comparison(connection, request)

@app.get('/api/discovery')
def discovery(filters: Filters = Depends(query_filters),
              sort: Literal['observed','price','recent','cuts'] = 'observed'):
    """Budget determines eligible models, never truncates their displayed price bands."""
    with engine.begin() as connection:
        if not HOSTED:
            refresh_labels(connection)
        anchor = as_of(connection)
        if not anchor:
            return {'items':[], 'as_of':None, 'coverage':{'observed':0,'recognized':0,'categorized':0}, 'confirmed_sales':None}
        where, params = cohort(filters.model_copy(update={'price_max':None}), anchor)
        params['budget'] = filters.price_max
        coverage = rows(connection,f"""SELECT count(*) AS observed,
          count(*) FILTER (WHERE recognized) AS recognized,
          count(*) FILTER (WHERE category IS NOT NULL) AS categorized
          FROM {LISTING_SOURCE} WHERE {where}""",params)[0]
        ordering = {'observed':'sample_size DESC', 'price':'median ASC NULLS LAST',
                    'recent':'newest_observation DESC', 'cuts':'price_cut_listings DESC'}[sort]
        items = rows(connection,f"""
          WITH sample AS (SELECT c.* FROM {LISTING_SOURCE} WHERE {where} AND c.recognized),
          observations AS (
            SELECT DISTINCT ON (s.listing_id,s.observed_at) s.listing_id,s.observed_at,s.asking_price
            FROM listing_snapshots s JOIN sample c USING(listing_id)
            WHERE s.price_status='priced' AND s.asking_price>0 AND s.observed_at<=:anchor
            ORDER BY s.listing_id,s.observed_at,s.id DESC
          ), transitions AS (
            SELECT *,lag(asking_price) OVER (PARTITION BY listing_id ORDER BY observed_at) AS previous_price
            FROM observations
          ), cuts AS (
            SELECT DISTINCT listing_id FROM transitions WHERE observed_at>=:cutoff AND asking_price<previous_price
          ), grouped AS (
            SELECT market_make AS make,market_model AS model,count(*) AS sample_size,
              count(*) FILTER (WHERE price_status='priced' AND price>0) AS priced_sample,
              count(*) FILTER (WHERE price_status='priced' AND price>0 AND (CAST(:budget AS integer) IS NULL OR price<=CAST(:budget AS integer))) AS within_budget,
              percentile_cont(.25) WITHIN GROUP (ORDER BY price) FILTER (WHERE price_status='priced' AND price>0) AS p25,
              percentile_cont(.5) WITHIN GROUP (ORDER BY price) FILTER (WHERE price_status='priced' AND price>0) AS median,
              percentile_cont(.75) WITHIN GROUP (ORDER BY price) FILTER (WHERE price_status='priced' AND price>0) AS p75,
              count(*) FILTER (WHERE first_seen_at>=:anchor-interval '14 days') AS first_seen_14d,
              count(*) FILTER (WHERE listing_id IN (SELECT listing_id FROM cuts)) AS price_cut_listings,
              min(year) AS year_min,max(year) AS year_max,max(last_verified_at) AS newest_observation,
              count(*) FILTER (WHERE category IS NOT NULL) AS categorized,
              array_agg(DISTINCT category) FILTER (WHERE category IS NOT NULL) AS categories
            FROM sample GROUP BY market_make,market_model
          ) SELECT * FROM grouped WHERE CAST(:budget AS integer) IS NULL OR within_budget>0
          ORDER BY {ordering},make,model
        """,params)
        for item in items:
            item['newest_observation'] = utc(item['newest_observation'])
    return {'items':items,'as_of':utc(anchor),'coverage':coverage,'confirmed_sales':None,
            'price_basis':'Full filtered model sample before the budget ceiling; distinct ads, not sales.'}

@app.post('/api/model-searches')
def save_model_search(filters: Filters):
    if not filters.make.strip() or not filters.model.strip():
        raise HTTPException(422,'Choose a specific model to follow')
    f = filters.model_copy(update={'price_max':None})
    serialized = json.dumps(f.model_dump(),sort_keys=True)
    fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
    with engine.begin() as connection:
        evidence = calculate_comparison(connection,Comparison(segments=[Segment(make=f.make,model=f.model)],filters=f))
        result = rows(connection,"""INSERT INTO dealer_model_searches(id,fingerprint,filters,baseline,baseline_as_of)
          VALUES (:id,:fingerprint,CAST(:filters AS jsonb),CAST(:baseline AS jsonb),:anchor)
          ON CONFLICT(fingerprint) DO UPDATE SET fingerprint=excluded.fingerprint RETURNING id""",
          {'id':uuid.uuid4().hex,'fingerprint':fingerprint,'filters':serialized,
           'baseline':json.dumps(evidence['segments'][0]),'anchor':as_of(connection)})[0]
    return result

@app.get('/api/model-searches')
def saved_model_searches():
    with engine.connect() as connection:
        searches = rows(connection,'SELECT * FROM dealer_model_searches ORDER BY saved_at DESC')
        for search in searches:
            f = Filters(**search['filters'])
            evidence = calculate_comparison(connection,Comparison(segments=[Segment(make=f.make,model=f.model)],filters=f))
            search['current'] = evidence['segments'][0]
            search['as_of'] = evidence['as_of']
            search['baseline_as_of'] = utc(search['baseline_as_of'])
            search['saved_at'] = utc(search['saved_at'])
            search['new_data'] = bool(search['as_of'] and search['baseline_as_of'] and
                                      search['as_of'] > search['baseline_as_of'])
    return searches

@app.delete('/api/model-searches/{search_id}')
def unfollow_model_search(search_id: str):
    with engine.begin() as connection:
        removed = connection.execute(text('DELETE FROM dealer_model_searches WHERE id=:id RETURNING id'),{'id':search_id}).scalar()
        if not removed:
            raise HTTPException(404,'Saved model search not found')
    return {'removed':search_id}

@app.get('/api/model-analysis')
def model_analysis(filters: Filters = Depends(query_filters)):
    if not filters.make.strip() or not filters.model.strip():
        raise HTTPException(422, 'Choose a make and a specific model')
    with engine.connect() as connection:
        anchor = as_of(connection)
        if not anchor:
            raise HTTPException(409, 'No successful collection is available')
        where, params = cohort(filters, anchor)
        evidence = calculate_comparison(connection, Comparison(
            segments=[Segment(make=filters.make, model=filters.model)], filters=filters))
        years = rows(connection, f"""
          WITH sample AS (SELECT c.* FROM {LISTING_SOURCE} WHERE {where}), stats AS (
            SELECT year, count(*) AS sample_size,
              count(*) FILTER (WHERE price_status='priced' AND price>0) AS priced_sample,
              min(price) FILTER (WHERE price_status='priced' AND price>0) AS minimum,
              max(price) FILTER (WHERE price_status='priced' AND price>0) AS maximum,
              percentile_cont(0.25) WITHIN GROUP (ORDER BY price) FILTER (WHERE price_status='priced' AND price>0) AS p25,
              percentile_cont(0.5) WITHIN GROUP (ORDER BY price) FILTER (WHERE price_status='priced' AND price>0) AS median,
              percentile_cont(0.75) WITHIN GROUP (ORDER BY price) FILTER (WHERE price_status='priced' AND price>0) AS p75,
              percentile_cont(0.5) WITHIN GROUP (ORDER BY mileage) FILTER (WHERE mileage>=0) AS median_mileage
            FROM sample GROUP BY year
          ) SELECT y.year,coalesce(s.sample_size,0) AS sample_size,coalesce(s.priced_sample,0) AS priced_sample,
            s.minimum,s.maximum,s.p25,s.median,s.p75,s.median_mileage
          FROM generate_series(:year_min,:year_max) AS y(year) LEFT JOIN stats s USING(year) ORDER BY y.year
        """, params)
        attributes = rows(connection, f"""SELECT
          count(*) FILTER (WHERE c.effective_transmission IN ('automatic','manual')) AS known_transmission,
          count(*) FILTER (WHERE c.effective_transmission_source IN ('olx_attribute','olx_website_attribute')) AS structured_transmission,
          count(*) FILTER (WHERE fuel_type IN ('diesel','petrol','hybrid','electric')) AS known_fuel,
          count(*) FILTER (WHERE mileage>=0) AS known_mileage
          FROM {LISTING_SOURCE} WHERE {where}""", params)[0]
        # The current filtered cohort sets membership; history covers 12 UTC months,
        # independently of the cohort's observation window. Each ad adds one price per month.
        month_index = anchor.year * 12 + anchor.month - 1 - 11
        params['history_start'] = datetime(month_index // 12, month_index % 12 + 1, 1)
        monthly = rows(connection, f"""
          WITH sample AS (SELECT c.listing_id FROM {LISTING_SOURCE} WHERE {where}), monthly_prices AS (
            SELECT DISTINCT ON (s.listing_id,date_trunc('month',s.observed_at))
              s.listing_id,date_trunc('month',s.observed_at) AS month,s.asking_price
            FROM listing_snapshots s JOIN sample c USING(listing_id)
            WHERE s.observed_at BETWEEN :history_start AND :anchor AND s.price_status='priced' AND s.asking_price>0
            ORDER BY s.listing_id,date_trunc('month',s.observed_at),s.observed_at DESC,s.id DESC
          ) SELECT month,count(*) AS priced_sample,
            percentile_cont(0.25) WITHIN GROUP (ORDER BY asking_price) AS p25,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY asking_price) AS median,
            percentile_cont(0.75) WITHIN GROUP (ORDER BY asking_price) AS p75
          FROM monthly_prices GROUP BY month ORDER BY month
        """, params)
        for month in monthly:
            month['month'] = utc(month['month'])
        resume_at = details_resume_at(connection)
    return {'as_of': utc(anchor), 'filters': filters.model_dump(), 'summary': evidence['segments'][0],
            'years': years, 'monthly': monthly, 'attributes': attributes,
            'coverage': 'incomplete_newest_results_sample', 'confirmed_sales': None, 'days_to_sell': None,
            'transmission_resume_at': utc(resume_at),
            'history_basis': 'Current filtered cohort; final usable price per listing per UTC month. Changes in the observed mix can move the median. This is not a market price index.'}

@app.get('/api/listings/{listing_id}/history')
def listing_history(listing_id: str):
    with engine.connect() as connection:
        anchor = as_of(connection)
        exists = connection.execute(text("SELECT 1 FROM car_listings WHERE listing_id=:id AND source='olx.ba' AND collection_method IN ('olx_api','olx_website')"), {'id': listing_id}).scalar()
        if not exists:
            raise HTTPException(404, 'Observed OLX listing not found')
        if not anchor:
            raise HTTPException(409, 'No successful collection is available')
        events = rows(connection, """
          WITH observations AS (
            SELECT DISTINCT ON (observed_at) observed_at,
              CASE WHEN price_status='priced' AND asking_price>0 THEN asking_price END AS price
            FROM listing_snapshots WHERE listing_id=:id AND observed_at<=:anchor ORDER BY observed_at,id DESC
          ), ordered AS (
            SELECT *,lag(price) OVER (ORDER BY observed_at) AS previous_price,
              row_number() OVER (ORDER BY observed_at) AS position FROM observations
          ) SELECT observed_at,price,previous_price,price-previous_price AS change
          FROM ordered WHERE position=1 OR price IS DISTINCT FROM previous_price ORDER BY observed_at
        """, {'id': listing_id, 'anchor': anchor})
        availability = rows(connection, 'SELECT status,last_checked_at,last_outcome FROM website_listing_states WHERE listing_id=:id', {'id': listing_id})
        state = availability[0] if availability else {}
        total = len(events)
        for event in events[-200:]:
            event['observed_at'] = utc(event['observed_at'])
    return {'listing_id': listing_id, 'as_of': utc(anchor), 'events': events[-200:], 'total_events': total,
            'truncated': total>200, 'availability': state.get('status','unverified'),
            'availability_checked_at': utc(state.get('last_checked_at')),
            'availability_last_outcome': state.get('last_outcome'), 'confirmed_sale': None}

def run_detail_job(job_id, listing_ids, delay=0.5):
    """Enrich only the local prototype attribute cache; never mutate collector history."""
    with detail_job_lock:
        detail_jobs[job_id]['status'] = 'running'
    with requests.Session() as session:
        for index, listing_id in enumerate(listing_ids):
            blocked = False
            try:
                details = fetch_transmission(session, listing_id)
                outcome = 'collected' if details['transmission'] else 'not_specified'
                with engine.begin() as connection:
                    store_transmission(connection, listing_id, details, datetime.now(timezone.utc).replace(tzinfo=None), outcome)
                with detail_job_lock:
                    detail_jobs[job_id]['enriched' if details['transmission'] else 'not_specified'] += 1
            except DetailsUnavailable as error:
                blocked = error.status_code in {403, 429}
                if blocked:
                    with engine.begin() as connection:
                        pause_details(connection)
                if not blocked:
                    with engine.begin() as connection:
                        store_transmission(connection, listing_id, {}, datetime.now(timezone.utc).replace(tzinfo=None), 'unavailable')
                with detail_job_lock:
                    detail_jobs[job_id]['unavailable'] += 1
                    if blocked:
                        detail_jobs[job_id]['message'] = 'OLX temporarily refused details. Remaining listings were left unchanged; try later.'
            except (requests.RequestException, ValueError):
                with detail_job_lock:
                    detail_jobs[job_id]['errors'] += 1
            except Exception:
                logger.exception('Could not store a transmission lookup')
                with detail_job_lock:
                    detail_jobs[job_id]['errors'] += 1
                    detail_jobs[job_id]['message'] = 'A local database error stopped the lookup. Completed attributes were preserved.'
                blocked = True
            finally:
                with detail_job_lock:
                    detail_jobs[job_id]['checked'] += 1
            if blocked:
                break
            if index+1 < len(listing_ids):
                time.sleep(delay)
    with detail_job_lock:
        job = detail_jobs[job_id]
        job['status'] = 'partial' if job['checked']<job['total'] or job['errors'] else 'completed'
        job['finished_at'] = utc(datetime.now(timezone.utc))

@app.post('/api/model-transmission', status_code=202)
def refresh_model_transmission(filters: Filters):
    if not filters.make.strip() or not filters.model.strip():
        raise HTTPException(422, 'Choose a make and a specific model')
    # Search all transmissions for this model, including currently unknown values.
    f = filters.model_copy(update={'transmission': '', 'price_max': None})
    with engine.connect() as connection:
        anchor = as_of(connection)
        if not anchor:
            raise HTTPException(409, 'No successful collection is available')
        where, params = cohort(f, anchor)
        pending = rows(connection, f"""SELECT c.listing_id FROM {LISTING_SOURCE} WHERE {where}
          AND c.listing_id ~ '^[0-9]{{1,20}}$'
          AND (e.checked_at IS NULL OR e.checked_at < (now() AT TIME ZONE 'UTC') -
            CASE WHEN e.outcome='unavailable' THEN interval '1 day' ELSE interval '7 days' END)
          ORDER BY (c.effective_transmission IS NULL) DESC,c.last_verified_at DESC,c.listing_id
        """, params)
        resume_at = details_resume_at(connection)
        if pending and resume_at:
            raise HTTPException(503, f'OLX details lookup is paused after a refused request. Try after {resume_at:%H:%M} UTC. Saved attributes remain available.')
    with detail_job_lock:
        if any(job['status'] in {'queued','running'} for job in detail_jobs.values()):
            raise HTTPException(409, 'Another model lookup is running. Wait for it to finish.')
        for old_id in list(detail_jobs)[:-15]:
            del detail_jobs[old_id]
        job_id = uuid.uuid4().hex
        listing_ids = [row['listing_id'] for row in pending[:300]]
        job = {'id': job_id, 'make': filters.make, 'model': filters.model, 'status': 'queued' if listing_ids else 'completed',
               'total': len(listing_ids), 'remaining': max(0,len(pending)-300), 'checked': 0, 'enriched': 0,
               'not_specified': 0, 'unavailable': 0, 'errors': 0, 'message': '', 'finished_at': None}
        detail_jobs[job_id] = job
        result = dict(job)
    if listing_ids:
        threading.Thread(target=run_detail_job, args=(job_id, listing_ids), daemon=True).start()
    return result

@app.get('/api/transmission-jobs/{job_id}')
def transmission_job(job_id: str):
    with detail_job_lock:
        job = detail_jobs.get(job_id)
        if job is None:
            raise HTTPException(404, 'Lookup not found. The local server may have restarted; saved attributes remain available.')
        return dict(job)

@app.post('/api/explain')
def explain(request: Explanation):
    with engine.connect() as connection:
        evidence = calculate_comparison(connection, request)
    statements = []
    for index, segment in enumerate(evidence['segments'], 1):
        ref = f'S{index}'
        n = segment['sample_size']
        median = f"{segment['median']:,.0f} KM" if segment['median'] is not None else 'unavailable'
        statements.append({'text': f"{segment['make']} {segment['model']}: {n} distinct listings in your filtered observation sample; {segment['priced_sample']} have usable asking prices. Median asking price is {median}. {segment['price_cut_listings']} distinct listings have a recorded asking-price cut in this period.", 'evidence_ref': ref})
    return {'mode': 'deterministic_evidence', 'answer': 'I cannot reliably choose which is easier to resell from this dataset. These are bounded newest-results observations, with unknown model coverage and no confirmed sale outcomes. The evidence below can help compare purchase budgets and asking prices.',
      'question': request.question, 'statements': statements, 'evidence': evidence,
      'next_step': 'Run complete model searches with identical year, mileage, fuel and transmission filters, then track listing status over time. Record confirmed outcomes before estimating sales speed.',
      'limitations': ['Asking prices are not transaction prices.', 'Window absence is not evidence of a sale.', 'Make and model are inferred from ad titles; parsing errors and split model labels can omit or mix comparable cars.', 'First seen is the start of our observation, not the date the ad was created.', 'Historical observations do not verify current availability.', 'The explanation uses templates and SQL results; no external AI provider is connected.']}

@app.get('/api/candidates')
def candidates():
    with engine.connect() as connection:
        return rows(connection, """SELECT d.*,c.make,c.model,c.year,
          CASE WHEN c.price_status='priced' AND c.price>0 THEN c.price ELSE NULL END AS price,c.last_verified_at
          FROM dealer_candidates d JOIN car_listings c USING(listing_id) ORDER BY d.saved_at DESC""")

@app.put('/api/candidates/{listing_id}')
def save_candidate(listing_id: str, candidate: Candidate):
    with engine.begin() as connection:
        exists = connection.execute(text("SELECT 1 FROM car_listings WHERE listing_id=:id AND source='olx.ba' AND collection_method IN ('olx_api','olx_website')"), {'id': listing_id}).scalar()
        if not exists:
            raise HTTPException(404, 'Observed OLX listing not found')
        connection.execute(text("""INSERT INTO dealer_candidates(listing_id,status,note) VALUES (:id,:status,:note)
          ON CONFLICT(listing_id) DO UPDATE SET status=excluded.status,note=excluded.note,updated_at=(now() AT TIME ZONE 'UTC')"""), {'id': listing_id, **candidate.model_dump()})
    return {'listing_id': listing_id, **candidate.model_dump()}

@app.delete('/api/candidates/{listing_id}')
def remove_candidate(listing_id: str):
    with engine.begin() as connection:
        connection.execute(text('DELETE FROM dealer_candidates WHERE listing_id=:id'), {'id': listing_id})
    return {'removed': listing_id}

frontend = Path(__file__).parent / 'frontend' / 'dist'
if (frontend / 'assets').exists():
    app.mount('/assets', StaticFiles(directory=frontend / 'assets'), name='assets')

@app.get('/')
@app.get('/market')
@app.get('/compare')
@app.get('/following')
@app.get('/models/{make}/{model}')
def ui():
    if not (frontend / 'index.html').exists():
        raise HTTPException(503, 'Build the frontend first: cd frontend && npm run build')
    return FileResponse(frontend / 'index.html')
