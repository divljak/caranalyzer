"""Read-only, deterministic allocation of the existing direct-check allowance.

This module never reserves visits, updates state, visits OLX or infers sales.
Attempt timestamps (including failures) provide rotation; successful checks alone
provide freshness. The caller must retain the existing lock/reserve/pause rules.
"""
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math

from sqlalchemy import text

POLICY = 'coverage_priority_v2'
TRACKED_METHODS = {'olx_api', 'olx_website'}
CANDIDATE_STATUSES = {'shortlisted', 'contacted', 'inspect'}
SUCCESS_OUTCOMES = {'listed', 'unavailable', 'source_reported_ended', 'source_reported_sold'}


def utc(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def label(value):
    return ' '.join(str(value or '').split()).casefold()


def stamp(value):
    return utc(value).isoformat() + 'Z' if value is not None else None


def followed_match(row, filters):
    """Exact saved scope, possible match with missing fields, or explicit mismatch.

    The saved `days` window is deliberately unused: stale matching cars need a
    refresh. A missing attribute never counts as a verified comparable.
    """
    if not label(filters.get('make')) or not label(filters.get('model')):
        return None
    for name in ('make', 'model'):
        if label(row.get(name)) != label(filters[name]):
            return None
    unknown = not row.get('recognized_model', False)
    for filter_name, field in [('fuel', 'fuel_type'), ('transmission', 'transmission'), ('category', 'category')]:
        wanted = filters.get(filter_name)
        if not wanted:
            continue
        actual = row.get(field)
        if wanted == 'unknown' and field == 'category':
            if actual is not None:
                return None
        elif not actual:
            unknown = True
        elif label(actual) != label(wanted):
            return None
    for key, field, lower in [('year_min', 'year', True), ('year_max', 'year', False),
                              ('mileage_min', 'mileage', True), ('mileage_max', 'mileage', False),
                              ('price_max', 'price', False)]:
        wanted = filters.get(key)
        if wanted is None:
            continue
        actual = row.get(field)
        if actual is None or (field == 'price' and (actual <= 0 or row.get('price_status') != 'priced')):
            unknown = True
        elif (actual < wanted if lower else actual > wanted):
            return None
    return 'followed_unknown_attributes' if unknown else 'followed_scope'


def eligible_rows(rows, now, excluded=()):
    """Deduplicate before capacity allocation; refuse malformed source IDs."""
    now = utc(now)
    excluded = {str(lid) for lid in excluded}
    unique = {}
    for original in rows:
        row = dict(original)
        lid = str(row.get('listing_id') or '')
        if (not lid or not lid.isascii() or not lid.isdigit() or lid in excluded or
                row.get('source') != 'olx.ba' or row.get('collection_method') not in TRACKED_METHODS):
            continue
        row['listing_id'] = lid
        # Choose the most recent version if a caller supplies overlapping extracts.
        key = (utc(row.get('last_checked_at')) or datetime.min,
               utc(row.get('last_verified_at')) or datetime.min,
               json.dumps(row, default=str, sort_keys=True))
        if lid not in unique or key > unique[lid][0]:
            unique[lid] = (key, row)
    return [r for _, r in unique.values()
            if utc(r.get('next_check_at')) is None or utc(r['next_check_at']) <= now]


def attempt_order(row):
    # Discovery sightings must not continually move unattempted old records back.
    return (utc(row.get('last_checked_at')) or utc(row.get('first_seen_at')) or datetime.min,
            row['listing_id'])


def reason(row, searches, now=None):
    if row.get('candidate_status') in CANDIDATE_STATUSES:
        return 'candidate'
    if row.get('status') == 'unavailable_pending':
        return 'availability_recheck'
    matches = [followed_match(row, f) for f in searches]
    if ('followed_unknown_attributes' in matches and now is not None
            and utc(row.get('first_seen_at')) is not None
            and now - timedelta(days=7) <= utc(row['first_seen_at']) <= now):
        return 'new_followed_details'
    if 'followed_scope' in matches:
        return 'followed_scope'
    if 'followed_unknown_attributes' in matches:
        return 'followed_unknown_attributes'
    return 'tracked_rotation'


def refresh_decision(row, now, basis):
    """Spend details on useful missing fields; keep sightings and detail ages separate.

    These intervals govern eligibility, not promised service deadlines. Never
    change stored attempt/success clocks or postponed removal confirmations.
    """
    details_at = utc(row.get('details_checked_at'))
    listed_at = utc(row.get('last_listed_at'))
    interval = 1 if basis == 'candidate' else 7 if basis in {
        'new_followed_details', 'followed_scope', 'followed_unknown_attributes'} else 30
    if basis == 'availability_recheck':
        return {'need': 'confirm_availability', 'interval_days': None, 'deferred_by': None}
    if row.get('status') in {'removed_unconfirmed', 'source_reported_ended', 'source_reported_sold'}:
        success = utc(row.get('last_success_at'))
        return {'need': 'revisit_unavailable', 'interval_days': 30,
                'deferred_by': 'unavailable_backoff' if success and now - timedelta(days=30) < success <= now else None}
    if details_at and now - timedelta(days=interval) < details_at <= now:
        return {'need': 'refresh_details', 'interval_days': interval, 'deferred_by': 'recent_detail_check'}
    missing = (not row.get('recognized_model') or not row.get('make') or not row.get('model')
               or row.get('year') is None or row.get('mileage') is None
               or not row.get('fuel_type') or not row.get('transmission'))
    if missing:
        return {'need': 'missing_attributes', 'interval_days': interval, 'deferred_by': None}
    # Focused cars still get periodic detail refreshes even if search sees them.
    # For ordinary complete rows, recent positive sightings make availability-only
    # visits redundant. This does not advance details_checked_at/last_success_at.
    if basis == 'tracked_rotation' and listed_at and now - timedelta(days=7) < listed_at <= now:
        return {'need': 'availability', 'interval_days': 7, 'deferred_by': 'recent_positive_sighting'}
    return {'need': 'refresh_details' if basis != 'tracked_rotation' else 'availability',
            'interval_days': interval, 'deferred_by': None}


def fair_lane(rows, now):
    """One car per model per round; rotate the first model each UTC day."""
    groups = defaultdict(list)
    for row in rows:
        # Unrecognized title fragments are one review pool, not hundreds of
        # artificial models competing equally with recognized model families.
        key = (label(row.get('make')), label(row.get('model'))) if row.get('recognized_model') else ('', '')
        groups[key].append(row)
    # Stable spread avoids consecutive model slots all belonging to one make
    # merely because its labels happen to be adjacent alphabetically.
    keys = sorted(groups, key=lambda k: hashlib.sha256(json.dumps(k).encode()).digest())
    if not keys:
        return []
    shift = utc(now).date().toordinal() % len(keys)
    keys = keys[shift:] + keys[:shift]
    queues = {k: deque(sorted(groups[k], key=attempt_order)) for k in keys}
    result = []
    while keys:
        for k in keys:
            result.append(queues[k].popleft())
        keys = [k for k in keys if queues[k]]
    return result


def select_priority(rows, now, limit=20, *, searches=(), excluded=(), used=0, pause_until=None, max_selected=None):
    """Return an explanatory plan bounded by remaining allowance; no side effects.

    One in four nominal slots goes to oldest attempts globally, with at least one
    such slot in a nonempty short plan. Unused lane capacity spills over. The
    guarantee requires actual visits: a pause or repeated run failure can prevent
    any scheduler from reaching later slots. Persistent attempt times allow the
    next run to move past failed IDs without treating them as fresh evidence.
    """
    if not isinstance(limit, int) or not 0 <= limit <= 20 or not isinstance(used, int) or used < 0:
        raise ValueError('Use the installed 0–20 daily detail limit and a nonnegative used count')
    if max_selected is not None and (not isinstance(max_selected, int) or not 0 <= max_selected <= 20):
        raise ValueError('Requested queue length must be between 0 and 20')
    now = utc(now)
    available = eligible_rows(rows, now, excluded)
    rows, deferred = [], Counter()
    for row in available:
        row['_reason'] = reason(row, searches, now)
        row['_refresh'] = refresh_decision(row, now, row['_reason'])
        if row['_refresh']['deferred_by']:
            deferred[row['_refresh']['deferred_by']] += 1
        else:
            rows.append(row)
    capacity = max(0, limit - used)
    paused = utc(pause_until) is not None and utc(pause_until) > now
    if paused:
        capacity = 0
    lanes = defaultdict(list)
    for row in rows:
        lanes[row['_reason']].append(row)
    ranked = []
    for name in ('candidate', 'availability_recheck', 'new_followed_details', 'followed_scope',
                 'followed_unknown_attributes', 'tracked_rotation'):
        ranked.extend(fair_lane(lanes[name], now))
    ranked = deque(ranked)
    oldest = deque(sorted(rows, key=attempt_order))
    selected, selected_ids = [], set()
    # For a short plan, the final slot is also the protected aging slot.
    count = min(capacity, len(rows), max_selected if max_selected is not None else 20)
    for slot in range(count):
        aging = slot % 4 == 3 or (count < 4 and slot == count - 1)
        pool = oldest if aging else ranked
        while pool and pool[0]['listing_id'] in selected_ids:
            pool.popleft()
        row = pool.popleft()
        selected_ids.add(row['listing_id'])
        selected.append({'listing_id': row['listing_id'], 'reason': 'oldest_attempt' if aging else row['_reason'],
                         'priority_basis': row['_reason'], 'make': row.get('make'), 'model': row.get('model'),
                         'last_attempt_at': stamp(row.get('last_checked_at')), 'last_success_at': stamp(row.get('last_success_at')),
                         'last_listed_at': stamp(row.get('last_listed_at')),
                         'details_checked_at': stamp(row.get('details_checked_at')),
                         'check_need': row['_refresh']['need'], 'refresh_interval_days': row['_refresh']['interval_days'],
                         'match_confidence': ('attributes_missing' if row['_reason'] in {'followed_unknown_attributes', 'new_followed_details'}
                                              else 'recorded_attributes_only')})
    return {'policy': POLICY, 'as_of_utc': now.isoformat() + 'Z', 'daily_detail_limit': limit,
            'detail_used': used, 'remaining_allowance': capacity, 'paused': paused,
            'due_eligible': len(rows), 'priority_counts': dict(sorted(Counter(r['_reason'] for r in rows).items())),
            'available_before_refresh_policy': len(available), 'refresh_deferred': dict(sorted(deferred.items())),
            'selected': selected, 'selected_ids': [r['listing_id'] for r in selected],
            'oldest_slots': sum(r['reason'] == 'oldest_attempt' for r in selected),
            'default_basis': ('saved_scopes_and_candidates' if searches or any(
                r.get('candidate_status') in CANDIDATE_STATUSES for r in available) else 'all_existing_tracked_models'),
            'source_coverage': 'unknown', 'sample_label': 'bounded_known_listing_check_queue',
            'confirmed_sales': None, 'minimum_days_for_one_pass_at_full_success': math.ceil(len(rows) / limit) if limit else None}


INPUT_SQL = """
SELECT c.listing_id,c.source,c.collection_method,c.first_seen_at,c.last_verified_at,
 c.year,c.mileage,c.price,c.price_status,c.fuel_type,
 CASE WHEN w.details_checked_at IS NOT NULL THEN w.attributes::jsonb->>'Proizvođač'
      ELSE coalesce(v.make,c.make) END AS make,
 CASE WHEN w.details_checked_at IS NOT NULL THEN w.attributes::jsonb->>'Model'
      ELSE coalesce(v.model,c.model) END AS model,
 CASE WHEN w.details_checked_at IS NOT NULL AND (e.checked_at IS NULL OR w.details_checked_at>=e.checked_at)
      THEN w.transmission ELSE coalesce(e.transmission,c.transmission) END AS transmission,
 (w.details_checked_at IS NOT NULL OR (v.make IS NOT NULL AND v.model IS NOT NULL)) AS recognized_model,
 v.category,d.status AS candidate_status,w.status,w.next_check_at,w.last_checked_at,w.last_success_at,
 w.last_listed_at,w.details_checked_at,w.last_outcome
FROM car_listings c LEFT JOIN website_listing_states w USING(listing_id)
 LEFT JOIN dealer_vehicle_labels v USING(listing_id) LEFT JOIN dealer_listing_details e USING(listing_id)
 LEFT JOIN dealer_candidates d USING(listing_id)
WHERE c.source='olx.ba' AND c.collection_method IN ('olx_api','olx_website')
"""


def load_priority_inputs(connection):
    """Use the caller's locked connection during collection, a read-only snapshot for reports."""
    rows = [dict(r) for r in connection.execute(text(INPUT_SQL)).mappings()]
    searches = [r[0] for r in connection.execute(text('SELECT filters FROM dealer_model_searches ORDER BY id'))]
    return rows, searches


def database_plan(connection, now, limit=20, *, excluded=(), max_selected=None):
    rows, searches = load_priority_inputs(connection)
    budget = connection.execute(text('SELECT day,detail_visits,pause_until FROM website_collection_budget WHERE id=1')).mappings().first()
    used = budget['detail_visits'] if budget and budget['day'] == utc(now).date() else 0
    return select_priority(rows, now, limit, searches=searches, excluded=excluded, used=used,
                           pause_until=budget['pause_until'] if budget else None, max_selected=max_selected)


from database.website_tracking import WebsiteStore


class PriorityWebsiteStore(WebsiteStore):
    """Opt-in direct-runtime adapter; all budget/status/write methods stay inherited.

    The completed direct collector pins `sessions` to its held advisory-lock
    connection. Use that same session here, never another pooled connection.
    No legacy store or shared collector file needs to change until release.
    """
    last_priority_plan = None

    def start_run(self, *args, **kwargs):
        self.last_priority_plan = None
        return super().start_run(*args, **kwargs)

    def candidates(self, now, limit, excluded=()):
        with self.sessions() as session:
            self.last_priority_plan = database_plan(session.connection(), now, 20,
                                                    excluded=excluded, max_selected=limit)
        return self.last_priority_plan['selected_ids']

    def finish_run(self, run_id, now, report):
        if self.last_priority_plan is not None:
            # This is planned allocation. Actual attempts/successes remain the
            # original run counters/check evidence, including early failure stops.
            report['priority_selection'] = self.last_priority_plan
        return super().finish_run(run_id, now, report)
