"""Seven calendar days of real collection metrics; read-only and no OLX requests."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from database.collection_priority import utc
from database.discovery_coverage import VERSION

DEFAULT_SCOPE = 'https://olx.ba/pretraga?category_id=18&sort_by=date&order=desc&page=1'


def summarize(runs, now, days=7):
    now = utc(now)
    start = now.date() - timedelta(days=days - 1)
    daily = {start + timedelta(days=n): [] for n in range(days)}
    for original in runs:
        row = dict(original)
        began = utc(row['started_at'])
        if began > now or began.date() not in daily:
            continue
        raw = row.get('report')
        try:
            report = json.loads(raw) if isinstance(raw, str) else raw or {}
        except (ValueError, TypeError):
            report = {}
        row['_report'] = report
        daily[began.date()].append(row)
    result = []
    for day, rows in daily.items():
        finished = [r for r in rows if r.get('finished_at') is not None and utc(r['finished_at']) <= now]
        instrumented = [r for r in finished if r['_report'].get('discovery_window', {}).get('version') == VERSION]
        search = sum(r['search_attempted'] for r in finished)
        detail = sum(r['detail_attempted'] for r in finished)
        new = sum(r['new_listings'] for r in finished)
        successes = sum(r['details_completed'] for r in finished)
        deferred = {}
        for row in instrumented:
            for reason, count in row['_report'].get('priority_selection', {}).get('refresh_deferred', {}).items():
                deferred[reason] = deferred.get(reason, 0) + count
        result.append({'day': day.isoformat(), 'run_ids': [r['id'] for r in rows],
                       'statuses': [r['status'] for r in rows], 'finished_runs': len(finished),
                       'instrumented_runs': len(instrumented), 'new_database_ids': new,
                       'search_visits': search, 'detail_visits': detail,
                       'saved_observations': sum(r['observations'] for r in finished),
                       'completed_detail_checks': successes,
                       'new_ids_per_search_visit': round(new / search, 2) if search else None,
                       'detail_completion_fraction': round(successes / detail, 4) if detail else None,
                       'discovery_window_statuses': [r['_report']['discovery_window']['status'] for r in instrumented],
                       'refresh_deferred': deferred})
    measured = sum(bool(r['instrumented_runs']) for r in result)
    return {'as_of_utc': now.isoformat() + 'Z', 'window_days': days,
            'instrumented_calendar_days': measured,
            'measurement_window_complete': measured == days,
            'days_without_run_records': [r['day'] for r in result if not r['run_ids']],
            'days': result, 'full_market_coverage': 'unknown', 'missed_listing_count': None,
            'interpretation': 'Daily records accumulate on the existing schedule; missing days are not zero-result successes. '
                              'Deferred rows are planned eligibility decisions, not measured avoided visits. '
                              'Overlapping windows do not prove every newly published ad was captured.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credential-file', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    from database.collector_connection import private_engine
    engine = private_engine(args.credential_file, 'car_analyzer_backup')
    try:
        with engine.connect().execution_options(isolation_level='REPEATABLE READ') as c:
            with c.begin():
                c.execute(text('SET TRANSACTION READ ONLY'))
                c.execute(text("SET LOCAL statement_timeout='12s'"))
                if c.execute(text('SELECT current_user')).scalar_one() != 'car_analyzer_backup':
                    raise RuntimeError('Expected backup read-only role')
                now = c.execute(text("SELECT timezone('UTC', now())")).scalar_one()
                start = datetime.combine(now.date() - timedelta(days=6), datetime.min.time())
                runs = c.execute(text('''SELECT id,started_at,finished_at,status,search_attempted,detail_attempted,
                    details_completed,observations,new_listings,report FROM website_collection_runs
                    WHERE source_query=:scope AND started_at>=:start AND started_at<=:now
                    ORDER BY started_at,id LIMIT 1001'''),
                    {'scope': DEFAULT_SCOPE, 'start': start, 'now': now}).mappings().all()
                if len(runs) > 1000:
                    raise RuntimeError('Report row bound exceeded')
                result = summarize(runs, now)
        payload = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
        if args.output:
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(payload)
            print(json.dumps({'output': str(args.output), 'instrumented_calendar_days': result['instrumented_calendar_days'],
                              'measurement_window_complete': result['measurement_window_complete']}))
        else:
            print(payload)
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'efficiency_report_failed', 'failure_class': type(exc).__name__,
                          'sqlstate': getattr(getattr(exc, 'orig', None), 'pgcode', None)}), file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
