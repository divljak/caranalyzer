#!/usr/bin/env python3
"""Read-only operational evidence probe and persistent, safe health event artifact."""
import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

UTC = timezone.utc
RANK = {'ok': 0, 'warning': 1, 'critical': 2}
CANONICAL_CODES = {'collection_partial', 'collection_failed', 'run_unfinished', 'clean_stale',
                   'observations_stale', 'website_paused', 'details_paused', 'detail_failures',
                   'collection_paused', 'collection_skipped', 'canonical_unreachable'}


def timestamp(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return timestamp(datetime.fromisoformat(value.replace('Z', '+00:00')))


def stamp(value):
    return timestamp(value).isoformat().replace('+00:00', 'Z') if value else None


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w') as handle:
        os.chmod(temp, 0o600)
        json.dump(value, handle, sort_keys=True)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)


def canonical_probe(credentials, delivery):
    # Imported only in the bounded probe container; no collector/browser imports.
    from sqlalchemy import text
    from database.collector_connection import private_engine
    from scripts.verify_delivery_backup import verify
    engine = private_engine(credentials, 'car_analyzer_backup')
    result = {'probe_ok': True, 'reachable': False, 'backup': {'valid': False}}
    try:
        with engine.connect().execution_options(isolation_level='REPEATABLE READ') as c:
            with c.begin():
                c.execute(text('SET TRANSACTION READ ONLY'))
                c.execute(text("SET LOCAL statement_timeout='10s'"))
                if c.execute(text('SELECT current_user')).scalar() != 'car_analyzer_backup':
                    raise ValueError('Unexpected role')
                result.update(reachable=True,
                    latest_observation=stamp(c.execute(text("""SELECT max(s.observed_at)
                      FROM listing_snapshots s JOIN car_listings l USING(listing_id)
                      WHERE l.source='olx.ba' AND l.collection_method IN ('olx_api','olx_website')""")).scalar()),
                    last_clean=stamp(c.execute(text("SELECT max(end_time) FROM scraping_logs WHERE status='completed' AND errors_count=0")).scalar()),
                    last_detail_success=stamp(c.execute(text("SELECT max(checked_at) FROM website_listing_checks WHERE outcome NOT IN ('check_failed','blocked')")).scalar()))
                result['runs'] = [dict(r) for r in c.execute(text("""SELECT id,started_at,finished_at,status,
                  observations,new_listings,errors,search_attempted,detail_attempted,details_completed
                  FROM website_collection_runs ORDER BY started_at DESC,id DESC LIMIT 30""")).mappings()]
                result['checks'] = [dict(r) for r in c.execute(text("SELECT checked_at,outcome FROM website_listing_checks ORDER BY checked_at DESC,id DESC LIMIT 10")).mappings()]
                result['website_pause_until'] = stamp(c.execute(text('SELECT max(pause_until) FROM website_collection_budget')).scalar())
                result['details_pause_until'] = stamp(c.execute(text('SELECT max(resume_at) FROM dealer_source_pauses')).scalar())
    except Exception as exc:
        # Never serialize exception text, URLs, query parameters or private rows.
        result = {'probe_ok': True, 'reachable': False, 'failure_class': type(exc).__name__, 'backup': {'valid': False}}
    finally:
        engine.dispose()
    try:
        paths = sorted(Path(delivery).glob('supabase-canonical-*.json.gz'), reverse=True)
        if paths:
            path = paths[0]
            verify(path)  # Checks all 12 tables; never restores or prints their content.
            import gzip
            created = json.loads(gzip.decompress(path.read_bytes()))['created_at']
            result['backup'] = {'valid': True, 'created_at': stamp(created), 'file': path.name}
    except Exception as exc:
        result['backup'] = {'valid': False, 'failure_class': type(exc).__name__}
    return result


def evaluate(sample, now):
    now = timestamp(now)
    issues = {}
    def add(code, severity, explanation, action):
        issues[code] = {'code': code, 'severity': severity, 'explanation': explanation, 'next_step': action}
    db = sample['canonical']
    service = sample['service']
    today_due = now.replace(hour=7, minute=17, second=0, microsecond=0)
    due = today_due if now >= today_due else today_due - timedelta(days=1)
    runs = db.get('runs', [])
    latest = runs[0] if runs else None
    terminal = next((r for r in runs if r.get('status') != 'running'), None)
    attempt = sample.get('attempt') or {}
    starts = [timestamp(r['started_at']) for r in runs]
    if service.get('last_start'):
        starts.append(timestamp(service['last_start']))
    if not attempt.get('verify_only') and attempt.get('started_at'):
        starts.append(timestamp(attempt['started_at']))
    last_start = max(starts, default=None)
    active = service.get('ActiveState') in {'active', 'activating'}
    if service.get('timer_active') != 'active' or service.get('timer_enabled') != 'enabled':
        add('schedule_disabled', 'critical', 'The daily collection timer is inactive or disabled.', 'Inspect car-analyzer-collector.timer; restore the existing 07:17 UTC schedule.')
    if (db.get('reachable') or last_start) and now >= due + timedelta(minutes=15) and (last_start is None or last_start < due):
        add('schedule_missed', 'critical', 'No daily collector start was recorded after the scheduled time and 15-minute grace period.', 'Inspect the collector timer and service journal; do not reset source budgets.')
    if active and (last_start is None or now - last_start > timedelta(minutes=40)):
        add('collector_overdue', 'critical', 'The collector has not finished within the 40-minute grace period.', 'Inspect service state and journal; preserve locks and source pause rules.')
    if not active and service.get('Result') not in {'success', None}:
        # A partial collector deliberately exits nonzero; classify using actual evidence below.
        outcome = latest or attempt.get('collection')
        expected_exit = (outcome and outcome.get('status') in {'partial', 'failed', 'paused', 'budget_exhausted'}
                         and not attempt.get('failure_class') and service.get('Result') == 'exit-code'
                         and service.get('ExecMainStatus') in {None, '1', 1}
                         and (not service.get('last_start') or
                              (timestamp(outcome.get('started_at') or attempt.get('started_at')) or datetime.min.replace(tzinfo=UTC))
                              >= timestamp(service['last_start']) - timedelta(seconds=10)))
        if not expected_exit:
            add('pipeline_failed', 'critical', 'The installed collector service last exited unsuccessfully.', 'Inspect the safe attempt artifact and collector journal for transport, backup or runtime failure.')
    if attempt.get('failure_class') and not active:
        add('pipeline_failed', 'critical', 'The last pipeline stopped before completing all stages.', 'Inspect latest-direct-attempt.json and journal locally; do not replay ambiguous writes or bypass budgets.')
    if not db.get('probe_ok', True):
        add('health_probe_failed', 'critical', 'The health probe could not execute; canonical reachability and backup verification are unknown.', 'Inspect the health service and probe release dependencies; do not retry OLX or change database roles.')
    elif not db.get('reachable'):
        add('canonical_unreachable', 'critical', 'The VPS cannot complete a read-only check of canonical Supabase. Freshness is unknown during this outage.', 'Check Supabase availability, network and backup-role credentials; no local fallback or OLX retry is needed.')
    else:
        if terminal:
            useful = int(terminal.get('observations') or 0) > 0
            if terminal['status'] == 'partial':
                add('collection_partial', 'warning', 'The latest finished collection is partial' + (' and recorded useful real observations.' if useful else ' without new recorded observations.'), 'Review source coverage and failure counts; await the next bounded scheduled run.')
            elif terminal['status'] == 'failed':
                add('collection_failed', 'critical', 'The latest collection failed' + (' after recording some real observations.' if useful else ' without recorded observations.'), 'Inspect the collector journal; retain evidence and source safeguards.')
            elif terminal['status'] == 'paused':
                add('collection_paused', 'warning', 'The latest collection stopped because source requests were paused.', 'Respect the source pause and inspect refusal evidence; await the next permitted scheduled run.')
            elif terminal['status'] == 'budget_exhausted':
                add('collection_skipped', 'warning', 'The latest collection skipped source visits because the cumulative daily budget was exhausted.', 'Await the next regular UTC-day run; do not reset budgets for a retry.')
        if latest and latest['status'] == 'running' and now - timestamp(latest['started_at']) > timedelta(minutes=40):
            add('run_unfinished', 'critical', 'The latest canonical run remains unfinished beyond 40 minutes.', 'Inspect whether the collector process was interrupted; use its normal recovery contract.')
        for key, code, explanation in [('last_clean', 'clean_stale', 'No clean collection has completed in 48 hours; partial data does not refresh the clean anchor.'), ('latest_observation', 'observations_stale', 'No real listing observation has been recorded in 48 hours. This does not prove there are no new listings.')]:
            if not db.get(key) or now - timestamp(db[key]) > timedelta(hours=48):
                add(code, 'warning', explanation, 'Inspect recent run outcomes and source pauses; preserve the distinction between coverage and market activity.')
        for key, code in [('website_pause_until', 'website_paused'), ('details_pause_until', 'details_paused')]:
            if db.get(key) and timestamp(db[key]) > now:
                add(code, 'warning', 'Source requests are paused until ' + stamp(db[key]) + '.', 'Respect the pause; review the refusal evidence before the next permitted scheduled run.')
        checks = [c for c in db.get('checks', []) if now - timestamp(c['checked_at']) <= timedelta(hours=48)]
        failures = {'check_failed', 'blocked'}
        consecutive = len(checks) >= 2 and all(c['outcome'] in failures for c in checks[:2])
        ratio = len(checks) >= 4 and sum(c['outcome'] in failures for c in checks) / len(checks) >= .5
        if consecutive or ratio:
            add('detail_failures', 'warning', 'Recent direct checks show repeated failures; listing availability and sale outcomes remain unconfirmed.', 'Inspect browser/transport failures and pause evidence without increasing request rates or daily budgets.')
    backup = db.get('backup', {})
    if not db.get('probe_ok', True):
        pass  # An unexecuted probe cannot establish an invalid backup.
    elif not backup.get('valid'):
        add('backup_invalid', 'critical', 'The newest canonical backup is missing, unreadable or fails checksum verification.', 'Inspect canonical backup storage and the last pipeline; preserve older verified snapshots.')
    elif now - timestamp(backup['created_at']) > timedelta(hours=30):
        add('backup_stale', 'critical', 'The newest verified canonical backup is older than 30 hours.', 'Inspect the backup stage of the daily pipeline and storage capacity.')
    for resource, label in [('disk', 'Disk'), ('memory', 'Available memory')]:
        metric = sample[resource]
        fraction = metric['available'] / max(metric['total'], 1)
        critical = fraction < .05 or (resource == 'disk' and metric['available'] < 512 * 1024**2)
        warning = fraction < .1 or (resource == 'disk' and metric['available'] < 2 * 1024**3)
        if warning:
            add(resource + '_pressure', 'critical' if critical else 'warning', label + ' is below the operational reserve.', 'Inspect resource usage; retain canonical backups, local rollback data and active services.')
    return issues, {'expected_start': stamp(due), 'last_start': stamp(last_start),
                    'last_clean': db.get('last_clean'), 'latest_observation': db.get('latest_observation'),
                    'last_detail_success': db.get('last_detail_success'), 'last_run': latest,
                    'canonical_reachable': db.get('reachable', False), 'probe_ok': db.get('probe_ok', True), 'backup': backup,
                    'last_pipeline_finished': attempt.get('finished_at'), 'service': service,
                    'disk': sample['disk'], 'memory': sample['memory']}


def transition(previous, issues, evidence, now):
    previous = previous or {}
    old = previous.get('issues', {})
    sequence = previous.get('sequence', 0)
    stream = previous.get('stream_id') or str(uuid.uuid4())
    events = list(previous.get('events', []))
    # Lost reachability makes evidence unknown, not recovered. Retain these open issues.
    if not evidence['canonical_reachable']:
        issues.update({k: v for k, v in old.items() if k in CANONICAL_CODES})
        if evidence.get('probe_ok') is False:
            issues.update({k: v for k, v in old.items() if k in {'backup_invalid', 'backup_stale'}})
            evidence['backup'] = previous.get('evidence', {}).get('backup', evidence.get('backup'))
        for key in ('last_clean', 'latest_observation', 'last_detail_success', 'last_run'):
            evidence[key] = previous.get('evidence', {}).get(key)
    for code in sorted(set(old) | set(issues)):
        before, after = old.get(code), issues.get(code)
        kind = 'opened' if before is None else 'recovered' if after is None else 'severity_changed' if before['severity'] != after['severity'] else None
        if kind:
            sequence += 1
            events.append({'id': stream + ':' + str(sequence), 'sequence': sequence,
                           'at': stamp(now), 'kind': kind, 'code': code,
                           'severity': after['severity'] if after else 'ok',
                           'explanation': after['explanation'] if after else 'Previously reported ' + code + ' condition has cleared.',
                           'next_step': after['next_step'] if after else 'No further action for this condition.'})
        if after:
            after['opened_at'] = before.get('opened_at', stamp(now)) if before else stamp(now)
    return {'schema_version': 1, 'stream_id': stream, 'sequence': sequence,
            'checked_at': stamp(now), 'severity': max((i['severity'] for i in issues.values()), key=RANK.get, default='ok'),
            'issues': issues, 'evidence': evidence, 'events': events[-512:],
            'oldest_event_sequence': events[-512:][0]['sequence'] if events else sequence + 1}


def system_state():
    def command(args):
        r = subprocess.run(args, text=True, capture_output=True, timeout=15)
        return r.stdout.strip()
    output = command(['systemctl', 'show', 'car-analyzer-collector.service', '-p', 'ActiveState', '-p', 'Result', '-p', 'ExecMainStatus', '-p', 'ExecMainStartTimestampMonotonic'])
    state = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    monotonic = int(state.pop('ExecMainStartTimestampMonotonic', '0'))
    if monotonic:
        uptime = float(Path('/proc/uptime').read_text().split()[0])
        state['last_start'] = stamp(datetime.now(UTC) - timedelta(seconds=uptime - monotonic / 1000000))
    state['timer_active'] = command(['systemctl', 'is-active', 'car-analyzer-collector.timer'])
    state['timer_enabled'] = command(['systemctl', 'is-enabled', 'car-analyzer-collector.timer'])
    return state


def collect(root):
    delivery = root / '.daily-delivery'
    release = (delivery / 'direct-current').resolve(strict=True)
    if not release.is_relative_to(delivery / 'releases'):
        raise ValueError('Unexpected collector release')
    image = (delivery / 'direct-image').read_text().strip()
    args = ['docker', 'run', '--rm', '--name', 'car-analyzer-health-probe', '--read-only', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--memory=192m', '--cpus=0.25', '--pids-limit=64',
            '-e', 'PYTHONDONTWRITEBYTECODE=1',
            '-v', str(release / 'database') + ':/app/database:ro',
            '-v', str(release / 'scripts') + ':/app/scripts:ro',
            '-v', str(release / 'market_labels.py') + ':/app/market_labels.py:ro',
            '-v', str(delivery / 'backup-secret.json') + ':/delivery/backup-secret.json:ro',
            '-v', str(root / 'scripts/collector_health.py') + ':/app/collector_health.py:ro',
            image, 'python', '/app/collector_health.py', '--probe', '--root', '/app']
    backups = sorted(delivery.glob('supabase-canonical-*.json.gz'), reverse=True)
    if backups:
        args[args.index(image):args.index(image)] = ['-v', str(backups[0]) + ':/delivery/' + backups[0].name + ':ro']
    try:
        process = subprocess.run(args, text=True, capture_output=True, timeout=75)
        if process.returncode:
            raise RuntimeError('Probe failed')
        canonical = json.loads(process.stdout)
    except Exception as exc:
        # Remove ONLY our timed-out probe, never the collector.
        subprocess.run(['docker', 'rm', '-f', 'car-analyzer-health-probe'], capture_output=True, timeout=15)
        canonical = {'probe_ok': False, 'reachable': False, 'failure_class': type(exc).__name__, 'backup': {'valid': None}}
    disk = shutil.disk_usage(root)
    memory = {line.split(':')[0]: int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemTotal:', 'MemAvailable:'))}
    attempt = read_json(delivery / 'latest-direct-attempt.json') or {}
    # Explicit allowlist: private backups/checksums/reports never enter public status.
    collection = attempt.get('collection') or {}
    attempt = {key: attempt.get(key) for key in ('started_at', 'finished_at', 'exit_code', 'failure_class', 'verify_only')}
    attempt['collection'] = {key: collection.get(key) for key in ('status', 'observations')}
    return {'canonical': canonical, 'service': system_state(), 'attempt': attempt,
            'disk': {'available': disk.free, 'total': disk.total},
            'memory': {'available': memory['MemAvailable'], 'total': memory['MemTotal']}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/car-analyzer'))
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--read', action='store_true')
    args = parser.parse_args()
    if args.probe:
        sys.path.insert(0, str(args.root))
        print(json.dumps(canonical_probe('/delivery/backup-secret.json', '/delivery'), default=stamp, sort_keys=True))
        return 0
    status_path = args.root / '.collector-health/current.json'
    if args.read:
        value = read_json(status_path)
        if value is None:
            print(json.dumps({'schema_version': 1, 'severity': 'critical', 'failure': 'health_status_missing'}))
            return 1
        print(json.dumps(value, sort_keys=True))
        return 0
    os.umask(0o077)
    status_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (status_path.parent / 'checker.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        now = datetime.now(UTC)
        previous = read_json(status_path)
        try:
            sample = collect(args.root)
            issues, evidence = evaluate(sample, now)
        except Exception:
            issues = {'checker_failed': {'code': 'checker_failed', 'severity': 'critical',
                       'explanation': 'The health checker could not inspect all required server state.',
                       'next_step': 'Inspect car-analyzer-health.service and its installed release.'}}
            # Unknown collection state must not emit fabricated recovery transitions.
            issues.update((previous or {}).get('issues', {}))
            evidence = dict((previous or {}).get('evidence', {}), canonical_reachable=False)
        result = transition(previous, issues, evidence, now)
        atomic_json(status_path, result)
        print(json.dumps({'checked_at': result['checked_at'], 'severity': result['severity'], 'sequence': result['sequence'], 'issue_codes': sorted(result['issues'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
