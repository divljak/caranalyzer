#!/usr/bin/env python3
"""No-source, read-only canonical coverage audit and next-check plan.

Requires the completed direct-collector runtime's credential reader. The existing
backup role is sufficient; no collector grants, schema changes or OLX requests.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import text
from database.collection_priority import load_priority_inputs, select_priority, utc, PriorityWebsiteStore


def model_coverage(rows, now):
    """Counts of distinct tracked ads, with explicit small/unknown sample labels."""
    groups = defaultdict(list)
    for row in rows:
        groups[(row.get('make') or 'Unknown', row.get('model') or 'Unknown')].append(row)
    result = []
    for (make, model), group in sorted(groups.items()):
        priced = sum(r.get('price_status') == 'priced' and (r.get('price') or 0) > 0 for r in group)
        recent = sum(utc(r.get('last_verified_at')) is not None and
                     utc(r['last_verified_at']) >= now - timedelta(days=30) for r in group)
        checked = sum(utc(r.get('last_success_at')) is not None and
                      utc(r['last_success_at']) >= now - timedelta(days=7) for r in group)
        result.append({'make': make, 'model': model, 'tracked_ads': len(group), 'priced_tracked_ads': priced,
                       'observed_30d': recent, 'successfully_checked_7d': checked,
                       'recognized_model_ads': sum(bool(r.get('recognized_model')) for r in group),
                       'sample_label': 'unknown_model' if 'Unknown' in (make, model) else
                                       'unrecognized_model_label' if not any(r.get('recognized_model') for r in group) else
                                       'small_retained_sample' if priced < 10 else 'retained_sample',
                       'source_coverage': 'unknown', 'attribute_confidence': 'recorded_or_inferred_not_spot_verified'})
    return sorted(result, key=lambda r: (-r['tracked_ads'], r['make'], r['model']))


def report(connection, now, limit=20):
    now = utc(now)
    audit = connection.execute(text((ROOT / 'docs/coverage-audit.sql').read_text()), {'as_of': now}).scalar_one()
    # This is a snapshot audit, not a temporal reconstruction of mutable rows.
    latest = utc(audit['summary']['latest_observation'])
    if latest and now < latest:
        raise ValueError('Report time precedes current evidence; use a historical backup for historical audits')
    rows, searches = load_priority_inputs(connection)
    budget = audit['budget'] or {}
    used = budget.get('detail_visits', 0) if str(budget.get('day')) == now.date().isoformat() else 0
    audit['priority_plan'] = select_priority(rows, now, limit, searches=searches, used=used,
                                            pause_until=budget.get('pause_until'))
    audit['models'] = model_coverage(rows, now)
    # Counterfactual selection only: preserve today's budget and all evidence.
    # Compare the next scheduled full allowance using the same retained snapshot.
    compare_at = (now+timedelta(days=1)).replace(hour=7, minute=17, second=0, microsecond=0)
    from sqlalchemy.orm import sessionmaker
    from database.website_tracking import WebsiteStore
    legacy = WebsiteStore(connection.engine)
    legacy.sessions = sessionmaker(bind=connection)
    adapter = PriorityWebsiteStore(connection.engine)
    adapter.sessions = sessionmaker(bind=connection)
    if adapter.candidates(now, audit['priority_plan']['remaining_allowance']) != audit['priority_plan']['selected_ids']:
        raise RuntimeError('Pinned read-only store selection did not match the report plan')
    audit['adapter_verification'] = 'passed_on_same_read_only_connection_without_budget_mutation'
    old_ids = legacy.candidates(compare_at, limit)
    new_plan = select_priority(rows, compare_at, limit, searches=searches)
    by_id = {r['listing_id']: r for r in rows}

    def selected_metrics(ids):
        selected = [by_id[lid] for lid in ids]
        return {'selected_count': len(selected),
                'recognized_model_groups': len({(r['make'],r['model']) for r in selected if r['recognized_model']}),
                'unrecognized_model_ads': sum(not r['recognized_model'] for r in selected),
                'last_observed_over_30d': sum(utc(r.get('last_verified_at')) is not None and
                                             utc(r['last_verified_at']) < compare_at-timedelta(days=30) for r in selected),
                'never_directly_attempted': sum(r['last_checked_at'] is None for r in selected)}

    audit['selection_comparison'] = {'sample_label': 'counterfactual_queue_only_no_source_visits',
                                     'compare_at_utc': compare_at.isoformat()+'Z',
                                     'existing_policy': selected_metrics(old_ids),
                                     'proposed_policy': selected_metrics(new_plan['selected_ids']),
                                     'proposed_plan': new_plan}
    audit['policy_validation'] = 'read_only_plan_only_no_new_source_evidence'
    return audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credential-file', required=True, type=Path)
    parser.add_argument('--as-of', help='UTC report timestamp; default current UTC')
    parser.add_argument('--detail-limit', type=int, choices=range(21), default=20)
    parser.add_argument('--output', type=Path, help='Optional private report file (mode 600)')
    args = parser.parse_args(argv)
    # Reuse the installed contract rather than duplicating credential/TLS handling.
    from database.collector_connection import private_engine
    engine = private_engine(args.credential_file, 'car_analyzer_backup')
    try:
        with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
            with connection.begin():
                connection.execute(text('SET TRANSACTION READ ONLY'))
                connection.execute(text("SET LOCAL statement_timeout='30s'"))
                if connection.execute(text('SELECT current_user')).scalar_one() != 'car_analyzer_backup':
                    raise RuntimeError('Coverage audit requires the installed read-only backup role')
                now = utc(args.as_of) if args.as_of else datetime.now(timezone.utc).replace(tzinfo=None)
                result = report(connection, now, args.detail_limit)
        payload = json.dumps(result, default=str, ensure_ascii=False, indent=2) + '\n'
        if args.output:
            # New file only: never overwrite another chat's report or follow a symlink.
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as output:
                output.write(payload)
            print(json.dumps({'summary': result['summary'], 'report': str(args.output),
                              'policy': result['priority_plan']['policy'], 'selected_count': len(result['priority_plan']['selected'])}))
        else:
            print(payload)
        return 0
    except Exception as exc:
        # Do not expose credentials, SQL parameters, or private saved searches in errors.
        print(json.dumps({'status': 'coverage_audit_failed', 'error_class': type(exc).__name__,
                          'sqlstate': getattr(getattr(exc, 'orig', None), 'pgcode', None)}), file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
