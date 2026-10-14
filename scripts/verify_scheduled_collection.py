"""Read-only direct-collection audit. Never imports collector startup or visits OLX.

Inputs containing private state must stay in .private/ or .daily-delivery/.
Exit 0 = consistent evidence, 1 = discrepancy, 2 = pending/incomplete verification.
Collection outcome (clean/partial/failed/skipped) is separate from audit validity.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT = 'ojmokmsjppkqwppmrzpv'
TABLES = ('car_listings', 'listing_snapshots', 'scraping_logs',
          'website_collection_budget', 'website_collection_runs',
          'website_listing_states', 'website_listing_checks', 'dealer_vehicle_labels',
          'dealer_model_searches', 'dealer_candidates', 'dealer_listing_details',
          'dealer_source_pauses')
PROTECTED = ('dealer_model_searches', 'dealer_candidates', 'dealer_listing_details', 'dealer_source_pauses')
IMMUTABLE = ('listing_snapshots', 'website_listing_checks')
HOST = 'car-analyzer-git-dealer-eviden-1dda87-ogilud-gmailcoms-projects.vercel.app'


def instant(value):
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc)
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False).encode()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def private_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + '.partial')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
    temporary.replace(path)


def systemd_timestamp(value):
    if not value or value == 'n/a':
        return None
    return datetime.strptime(value, '%a %Y-%m-%d %H:%M:%S UTC').replace(
        tzinfo=timezone.utc).isoformat()


def capture_scheduler(root, runtime_manifest=None):
    def properties(unit, names):
        output = subprocess.check_output(
            ['systemctl', 'show', unit, *['--property=' + n for n in names]], text=True)
        return dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    service = properties('car-analyzer-collector.service', [
        'ActiveState', 'Result', 'ExecMainStatus', 'ExecMainStartTimestamp',
        'ExecMainExitTimestamp', 'ExecStart', 'ExecStartPost', 'ExecStartPre', 'ExecStopPost'])
    # Store classifications only: never dump arbitrary command lines/environment.
    service['direct_exec'] = 'path=/opt/car-analyzer/scripts/daily-direct-collection.sh ;' in service.pop('ExecStart')
    service['no_post_sync'] = not service.pop('ExecStartPost', '')
    pre, post = service.pop('ExecStartPre', ''), service.pop('ExecStopPost', '')
    hook = '/opt/car-analyzer/.daily-delivery/backup-tools/run-canonical-backup.sh'
    service['canonical_backup_pre'] = hook + ' before' in pre
    service['canonical_backup_post'] = hook + ' after' in post
    service['canonical_backup_hook_exits_ok'] = all('status=0' in item
        for item in (pre, post.split('path=' + hook)[-1])) if service['canonical_backup_pre'] and service['canonical_backup_post'] else False
    for field in ('ExecMainStartTimestamp', 'ExecMainExitTimestamp'):
        service[field] = systemd_timestamp(service[field])
    timer = properties('car-analyzer-collector.timer', [
        'ActiveState', 'UnitFileState', 'LastTriggerUSec', 'NextElapseUSecRealtime', 'TimersCalendar'])
    for field in ('LastTriggerUSec', 'NextElapseUSecRealtime'):
        timer[field] = systemd_timestamp(timer[field])
    entry = (root / 'scripts/daily-direct-collection.sh').read_text()
    runner = (root / 'scripts/run-direct-container.sh').read_text()
    release = (root / '.daily-delivery/direct-current').resolve()
    manifest = runtime_manifest or root / '.daily-delivery/direct-installation.json'
    if runtime_manifest is None:
        for name in ('detail-reliability-installation.json', 'coverage-priority-installation.json'):
            candidate = root / '.daily-delivery' / name
            if candidate.exists() and load(candidate).get('release') == str(release):
                manifest = candidate
    installation = load(manifest)
    runtime_hashes = {name:hashlib.sha256((release / name).read_bytes()).hexdigest()
                      for name in installation['file_sha256']}
    runtime_mismatches = [name for name,value in runtime_hashes.items()
                          if value != installation['file_sha256'][name]]
    if installation.get('release') != str(release):
        runtime_mismatches.append('release:unmatched_manifest')
    for name in ('scripts/daily-direct-collection.sh','scripts/run-direct-container.sh'):
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != runtime_hashes[name]:
            runtime_mismatches.append('live:' + name)
    messages = []
    if service['ExecMainStartTimestamp']:
        journal = subprocess.check_output(['journalctl','-u','car-analyzer-collector.service',
            '--since',service['ExecMainStartTimestamp'],'--output=json','--all','--no-pager'],text=True)
        messages = [json.loads(line).get('MESSAGE') or '' for line in journal.splitlines()]
    reports = []
    for message in messages:
        if message.startswith('{'):
            value = json.loads(message)
            if value.get('architecture') == 'direct_supabase' and not value.get('verify_only'):
                reports.append({k:value.get(k) for k in ('started_at','finished_at','exit_code','collection')})
    full_backup = None
    backup_dir = root / '.daily-delivery/canonical-backups'
    if (backup_dir / 'installation.json').exists():
        backup_installation = load(backup_dir / 'installation.json')
        installed_hashes = {name:hashlib.sha256(Path(name).read_bytes()).hexdigest()
                            for name in backup_installation['installed_files_sha256']}
        latest = load(backup_dir / 'latest.json')
        bundle = Path(latest['path'])
        if bundle.parent != backup_dir or not bundle.name.startswith('canonical-'):
            raise ValueError('Unexpected canonical backup path')
        artifacts = {name:hashlib.sha256((bundle / name).read_bytes()).hexdigest() for name in latest['files']}
        snapshot = json.loads(gzip.decompress((bundle / 'snapshot.json.gz').read_bytes()))
        full_backup = {'source_commit':backup_installation['source_commit'],
            'installed_sha256':installed_hashes,
            'installation_matches':installed_hashes == backup_installation['installed_files_sha256'],
            'archive_files_match':artifacts == latest['files'],
            'snapshot_checksums_match':all(digest(snapshot['tables'][t]) == latest['checksums'][t] for t in TABLES),
            'manifest':latest, 'export_attempt':load(backup_dir / 'latest-attempt.json'),
            'restore':load(backup_dir / 'latest-restore.json'),
            'restore_attempt':load(backup_dir / 'latest-restore-attempt.json')}
    return {'captured_at': datetime.now(timezone.utc).isoformat(), 'service': service,
            'full_backup':full_backup,
            'timer': timer, 'journal': {'last_real_report':reports[-1] if reports else None,
                 'timeout_seen':any('start operation timed out' in m or 'Failed with result \'timeout\'' in m for m in messages)},
            'configuration': {
                'release': str(release),
                'source_commit':installation['source_commit'],
                'runtime_manifest':str(manifest),
                'image':(root / '.daily-delivery/direct-image').read_text().strip(),
                'runtime_sha256':runtime_hashes,
                'runtime_mismatches':runtime_mismatches,
                'runtime_manifest_matches':not runtime_mismatches,
                'priority_installed':'database/collection_priority.py' in runtime_hashes,
                'release_is_managed': str(release).startswith(str(root / '.daily-delivery/releases') + '/'),
                'entry_sha256': digest(entry), 'runner_sha256': digest(runner),
                'uses_standalone_docker': 'docker run --rm' in runner and 'docker compose' not in runner,
                'collector_secret': '--credentials /delivery/collector-secret.json' in runner,
                'no_local_or_sync': all(token not in entry + runner for token in
                                        ('sync_supabase.py', 'DB_HOST=', 'DATABASE_URL=', 'compose run'))}}


def read_database(credentials):
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import URL
    from sqlalchemy.pool import NullPool
    path = Path(credentials)
    if path.stat().st_mode & 0o077:
        raise ValueError('Credential file must be private')
    data = load(path)
    if (data['host'], int(data['port']), data['dbname'], data['user'], data['sslmode']) != (
            'aws-0-eu-north-1.pooler.supabase.com', 5432, 'postgres',
            'car_analyzer_backup.' + PROJECT, 'require'):
        raise ValueError('Expected canonical Supabase backup credential')
    engine = create_engine(URL.create('postgresql+psycopg2', username=data['user'],
                           password=data['password'], host=data['host'], port=data['port'],
                           database=data['dbname']), poolclass=NullPool, hide_parameters=True,
                           connect_args={'sslmode': 'require', 'connect_timeout': 20,
                                         'application_name': 'car_analyzer_verifier'})
    try:
        with engine.connect().execution_options(isolation_level='REPEATABLE READ') as c:
            c.execute(text('SET TRANSACTION READ ONLY'))
            c.execute(text("SET LOCAL statement_timeout = '30s'"))
            role = c.execute(text('SELECT current_user')).scalar()
            if role != 'car_analyzer_backup':
                raise ValueError('Unexpected read role')
            tables = {}
            for table in TABLES:
                key = ('listing_id' if table in ('car_listings', 'website_listing_states',
                       'dealer_vehicle_labels', 'dealer_candidates', 'dealer_listing_details')
                       else 'source' if table == 'dealer_source_pauses' else 'id')
                tables[table] = c.execute(text(
                    f'SELECT row_to_json(t) FROM (SELECT * FROM public.{table} ORDER BY {key}::text COLLATE "C") t')).scalars().all()
            grants = c.execute(text("""SELECT r.rolname, r.rolsuper, r.rolbypassrls,
                has_schema_privilege(r.rolname,'public','CREATE') AS schema_create,
                pg_has_role(r.rolname,'car_analyzer_collector','MEMBER') AS collector_member,
                t.tablename, t.rowsecurity,
                has_table_privilege(r.rolname,'public.'||t.tablename,'SELECT') AS can_select,
                has_table_privilege(r.rolname,'public.'||t.tablename,'INSERT') AS can_insert,
                has_table_privilege(r.rolname,'public.'||t.tablename,'DELETE') AS can_delete,
                EXISTS(SELECT 1 FROM pg_attribute a
                       WHERE a.attrelid=('public.'||t.tablename)::regclass AND a.attnum>0 AND NOT a.attisdropped
                       AND has_column_privilege(r.rolname,a.attrelid,a.attnum,'UPDATE')) AS any_update,
                CASE WHEN t.tablename='dealer_model_searches' THEN
                  has_column_privilege(r.rolname,'public.dealer_model_searches','filters','UPDATE') OR
                  has_column_privilege(r.rolname,'public.dealer_model_searches','baseline','UPDATE') OR
                  has_column_privilege(r.rolname,'public.dealer_model_searches','baseline_as_of','UPDATE')
                  ELSE false END AS baseline_update
                FROM pg_roles r CROSS JOIN pg_tables t
                WHERE r.rolname IN ('car_analyzer_app','car_analyzer_collector','car_analyzer_backup','anon','authenticated')
                AND t.schemaname='public' AND t.tablename=ANY(:tables)
                ORDER BY r.rolname,t.tablename"""), {'tables': list(TABLES)}).mappings().all()
            c.rollback()
            return tables, [dict(r) for r in grants]
    finally:
        engine.dispose()


def assess(tables, grants, attempt, scheduler, expected_start=None, scheduled=False,
           baseline=None, overview=None, browser=None, access=None, pre_run_tables=None, evaluated_at=None):
    checks = []
    def check(name, ok, detail=None):
        checks.append({'name': name, 'result': 'pending' if ok is None else 'pass' if ok else 'fail',
                       **({'detail': detail} if detail is not None else {})})
    expected = instant(expected_start)
    budget = tables['website_collection_budget'][0] if tables['website_collection_budget'] else None
    runs = sorted(tables['website_collection_runs'], key=lambda r: (r['started_at'], r['id']), reverse=True)
    direct = [r for r in runs if r['session_id'].startswith('direct-') and
              (not expected or instant(r['started_at']) >= expected)]
    run = direct[0] if direct else None
    now = instant(evaluated_at) if evaluated_at else datetime.now(timezone.utc)
    overdue = expected and (now - expected).total_seconds() > 25 * 60
    check('real_direct_run', True if run else False if scheduled and overdue else None)
    checksums = {name: digest(rows) for name, rows in tables.items()}
    history = {name: {str(row['id']): digest(row) for row in tables[name]} for name in IMMUTABLE}
    clean_times = [r['end_time'] for r in tables['scraping_logs']
                   if r['status'] == 'completed' and r['errors_count'] == 0 and r['end_time']]
    clean = max(clean_times, default=None)
    latest_observation = max((r['observed_at'] for r in tables['listing_snapshots']), default=None)
    stale_labels = 0
    labels = {r['listing_id']: r for r in tables['dealer_vehicle_labels']}
    for listing in tables['car_listings']:
        if listing['source'] == 'olx.ba' and listing['collection_method'] in ('olx_api', 'olx_website'):
            label = labels.get(listing['listing_id'])
            if not label or label['version'] < 1 or any(label[a] != listing[b] for a, b in
                    [('source_title','description'), ('source_make','make'), ('source_model','model')]):
                stale_labels += 1
    check('derived_labels_current', stale_labels == 0, stale_labels)
    expected_grants = {(role, table) for role in ('car_analyzer_app','car_analyzer_collector',
                       'car_analyzer_backup','anon','authenticated') for table in TABLES}
    check('grant_inventory_complete', {(g['rolname'],g['tablename']) for g in grants} == expected_grants)
    denied = []
    for g in grants:
        role, table = g['rolname'], g['tablename']
        write = g['can_insert'] or g['can_delete'] or g['any_update']
        violation = g['rolsuper'] or g['rolbypassrls'] or g['schema_create'] or not g['rowsecurity']
        if role == 'car_analyzer_app':
            violation |= g['collector_member'] or g['baseline_update'] or (write and table != 'dealer_model_searches')
        elif role in ('anon','authenticated'):
            violation |= write or g['can_select'] or g['collector_member']
        elif role == 'car_analyzer_backup':
            violation |= write or g['collector_member']
        elif role == 'car_analyzer_collector':
            violation |= g['can_delete'] or (write and table in PROTECTED + ('dealer_source_pauses',))
            violation |= g['any_update'] and table in IMMUTABLE
        if violation:
            denied.append(role + ':' + table)
    check('role_boundaries', not denied, denied)
    if scheduler:
        service, timer = scheduler['service'], scheduler['timer']
        check('installed_direct_configuration', service['direct_exec'] and service['no_post_sync'] and
              all(scheduler['configuration'][k] for k in ('release_is_managed','uses_standalone_docker','collector_secret','no_local_or_sync','runtime_manifest_matches')))
        check('timer_active_and_0717_utc', timer['ActiveState'] == 'active' and timer['UnitFileState'] == 'enabled'
              and '07:17:00 UTC' in timer['TimersCalendar'])
        if scheduler['configuration'].get('priority_installed'):
            required = ('dealer_listing_details','dealer_candidates','dealer_model_searches')
            missing = [t for t in required if not any(g['rolname']=='car_analyzer_collector' and
                g['tablename']==t and g['can_select'] for g in grants)]
            check('priority_collector_read_access', not missing, missing)
    else:
        check('installed_scheduler', None)
    if run:
        check('run_finished', run['finished_at'] is not None and run['status'] != 'running')
        observations = [r for r in tables['listing_snapshots'] if run['finished_at'] and
                        instant(run['started_at']) <= instant(r['observed_at']) <= instant(run['finished_at'])]
        run_checks = [r for r in tables['website_listing_checks'] if r['run_id'] == run['id']]
        check('run_observations_match', len(observations) == run['observations'], len(observations))
        check('run_checks_match', len(run_checks) == run['detail_attempted'], len(run_checks))
        logs = [r for r in tables['scraping_logs'] if r['session_id'] == run['session_id']]
        check('scraping_log_matches', len(logs) == 1 and logs[0]['total_listings_found'] == run['observations']
              and logs[0]['errors_count'] == run['errors'] and logs[0]['status'] ==
              ('partial' if run['status'] == 'completed' and not run['observations'] else run['status']))
        check('clean_semantics', run['status'] != 'completed' or
              (run['errors'] == 0 and bool(run['discovery_complete'])))
        if run['status'] != 'completed':
            check('incomplete_does_not_advance_clean_anchor', not clean or instant(clean) < instant(run['started_at']))
        match = bool(attempt and not attempt.get('verify_only') and attempt.get('architecture') == 'direct_supabase'
                     and instant(attempt['started_at']) <= instant(run['started_at']) and run['finished_at']
                     and instant(attempt['finished_at']) >= instant(run['finished_at']))
        check('job_report_matches_run', match)
        if match:
            check('job_collection_matches', all(attempt.get('collection', {}).get(k) == run[k] for k in
                  ('status','observations','new_listings','errors','search_attempted','detail_attempted','details_completed')))
            check('job_exit_is_honest', attempt['exit_code'] == (0 if run['status'] == 'completed' else 1))
            check('canonical_backup_verified', attempt.get('backup_verified') is True)
            check('protected_state_unchanged_during_run', all(
                attempt['before']['checksums'][t] == attempt['after']['checksums'][t] for t in PROTECTED))
            check('post_run_database_matches_backup', all(attempt['after']['checksums'][t] == checksums[t] for t in TABLES))
            check('pre_run_backup_independently_verified', all(
                  digest(pre_run_tables[t]) == attempt['before']['checksums'][t] for t in TABLES) if pre_run_tables is not None else None)
            if pre_run_tables is not None:
                check('immutable_history_preserved_during_run', all(
                      history[t].get(str(row['id'])) == digest(row) for t in IMMUTABLE for row in pre_run_tables[t]))
        if scheduler:
            service = scheduler['service']
            start = instant(service['ExecMainStartTimestamp'])
            check('service_matches_run', bool(start and 0 <= (instant(run['started_at']) - start).total_seconds() <= 120))
            check('service_result_is_honest', service['ActiveState'] != 'activating' and
                  int(service['ExecMainStatus']) == (0 if run['status'] == 'completed' else 1) and
                  service['Result'] == ('success' if run['status'] == 'completed' else 'exit-code'))
            journal = scheduler.get('journal', {}).get('last_real_report')
            check('journal_matches_real_job', bool(journal and match and all(
                  journal[k] == attempt[k] for k in ('started_at','finished_at','exit_code','collection'))))
            if scheduled:
                trigger = instant(scheduler['timer']['LastTriggerUSec'])
                check('normal_timer_trigger', bool(expected and trigger and start and
                      0 <= (trigger - expected).total_seconds() <= 120 and
                      0 <= (start - trigger).total_seconds() <= 120))
        if scheduled and not scheduler:
            check('normal_timer_trigger', None)
        check('daily_budget_ceiling', bool(budget and budget['search_visits'] <= 3 and budget['detail_visits'] <= 20))
        check('budget_matches_run_day', bool(budget and budget['day'] == instant(run['started_at']).date().isoformat()))
        full_backup = scheduler.get('full_backup') if scheduler else None
        if full_backup:
            manifest, export = full_backup['manifest'], full_backup['export_attempt']
            restore, restore_attempt = full_backup['restore'], full_backup['restore_attempt']
            check('full_backup_installation_and_hooks', full_backup['installation_matches'] and
                all(scheduler['service'][k] for k in ('canonical_backup_pre','canonical_backup_post','canonical_backup_hook_exits_ok')))
            check('full_backup_current_archive_verified', full_backup['archive_files_match'] and full_backup['snapshot_checksums_match']
                and manifest['complete'] is True and manifest['stage']=='after' and export['status']=='success'
                and export['path']==manifest['path'] and instant(manifest['created_at'])>=instant(run['finished_at'])
                and all(manifest['checksums'][t]==checksums[t] for t in TABLES))
            check('full_backup_current_restore_verified', restore['bundle']==manifest['path'] and
                restore_attempt['bundle']==manifest['path'] and restore_attempt['status']=='success' and
                instant(restore['verified_at'])>=instant(manifest['created_at']) and
                restore['scratch_network']=='none' and all(restore[k] is True for k in
                    ('content_checksums_match','schema_access_match','sequence_state_match','scratch_removed')))
        if pre_run_tables and budget and pre_run_tables['website_collection_budget']:
            previous = pre_run_tables['website_collection_budget'][0]
            check('same_day_budget_not_reset', previous['day'] != budget['day'] or all(
                  budget[k] >= previous[k] for k in ('search_visits','detail_visits')))
    if baseline:
        check('immutable_history_preserved', all(
              history[t].get(key) == value for t in IMMUTABLE for key,value in baseline['history'][t].items()))
        check('protected_state_matches_baseline', all(
              checksums[t] == baseline['checksums'][t] for t in PROTECTED),
              'A legitimate user edit requires attribution; do not overwrite it to make a check pass.')
    if overview:
        body = overview['body']
        latest = runs[0] if runs else None
        check('hosted_overview_matches_canonical', overview['status'] == 200 and
              overview['url'] == 'https://' + HOST + '/api/overview' and
              body['listings'] == len(tables['car_listings']) and body['snapshots'] == len(tables['listing_snapshots']) and
              instant(body['as_of']) == instant(clean) and bool(latest) and all(
              body['website_collection'][k] == latest[k] for k in ('status','observations','errors','detail_attempted')) and
              instant(body['website_collection']['started_at']) == instant(latest['started_at']))
        check('hosted_response_uncached', 'no-store' in overview.get('cache_control', ''))
        observed = next((r for r in runs if r['observations'] > 0), None)
        api_observed = body.get('website_observation_collection')
        check('hosted_retains_latest_observation_run', bool(api_observed and all(
              api_observed[k] == observed[k] for k in ('status','observations')) and
              instant(api_observed['started_at']) == instant(observed['started_at']))
              if observed else api_observed is None)
        check('hosted_capture_after_run', bool(run['finished_at'] and
              instant(overview['captured_at']) >= instant(run['finished_at'])) if run else None)
    else:
        check('hosted_overview', None)
    if browser:
        check('hosted_browser_capture', bool(run['finished_at'] and
              instant(browser['captured_at']) >= instant(run['finished_at']) and
              browser['url'] == 'https://' + HOST + '/market' and
              browser.get('freshness_text_matches') is True and browser.get('screenshot')) if run else None)
    else:
        check('hosted_browser', None)
    if access:
        check('unauthenticated_access_protected', all(access.get(k) in (302,307,308,401,403) for k in
              ('html_status','api_status')) and access.get('redirect_is_vercel_login') is True)
    else:
        check('unauthenticated_access', None)
    result = 'fail' if any(c['result'] == 'fail' for c in checks) else 'pending' if any(
        c['result'] == 'pending' for c in checks) else 'pass'
    summary_run = {k: run[k] for k in ('id','session_id','started_at','finished_at','status','observations',
                   'new_listings','search_attempted','detail_attempted','details_completed','errors','discovery_complete')} if run else None
    return {'format': 1, 'verified_at': datetime.now(timezone.utc).isoformat(), 'result': result,
            'scheduled': scheduled, 'expected_start': expected_start, 'run': summary_run,
            'counts': {t:len(v) for t,v in tables.items()}, 'clean_collection_at': clean,
            'latest_observation_at': latest_observation,
            'budget': budget,
            'runtime':scheduler.get('configuration') if scheduler else None,
            'full_backup':scheduler.get('full_backup') if scheduler else None,
            'check_outcomes': dict(Counter(r['outcome'] for r in tables['website_listing_checks'] if run and r['run_id'] == run['id'])),
            'checks': checks, 'checksums': checksums, 'history': history,
            'limitations': ['Snapshot time-window attribution relies on the exclusive collector lock.',
                            'Empty dealer tables prove empty-state preservation only.',
                            'Partial observations do not refresh every listing or prove sales.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture-scheduler', action='store_true')
    parser.add_argument('--root', type=Path, default=Path('/opt/car-analyzer'))
    parser.add_argument('--runtime-manifest', type=Path, help='Explicit combined-runtime installation manifest for scheduler capture')
    parser.add_argument('--credentials', type=Path)
    parser.add_argument('--state-dir', type=Path, default=Path('/delivery'))
    parser.add_argument('--expected-start')
    parser.add_argument('--scheduled', action='store_true')
    for name in ('scheduler', 'baseline', 'overview', 'browser', 'access'):
        parser.add_argument('--' + name + '-json', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.capture_scheduler:
        try:
            private_write(args.output, capture_scheduler(args.root, args.runtime_manifest))
            return 0
        except Exception as exc:
            print(json.dumps({'result':'fail','failure_class':type(exc).__name__}), file=sys.stderr)
            return 1
    if not args.credentials or (args.scheduled and not args.expected_start):
        parser.error('Credentials and (for a scheduled audit) expected UTC start are required')
    try:
        tables, grants = read_database(args.credentials)
        attempt_path = args.state_dir / 'latest-direct-attempt.json'
        attempt = load(attempt_path) if attempt_path.exists() else None
        before = None
        if attempt and attempt.get('before'):
            before_path = args.state_dir / Path(attempt['before']['path']).name
            before = json.loads(gzip.decompress(before_path.read_bytes()))['tables']
        result = assess(tables, grants, attempt,
                        load(args.scheduler_json) if args.scheduler_json else None,
                        args.expected_start, args.scheduled, pre_run_tables=before,
                        **{name:load(getattr(args,name+'_json')) if getattr(args,name+'_json') else None
                           for name in ('baseline','overview','browser','access')})
        private_write(args.output, result)
        print(json.dumps({k:v for k,v in result.items() if k not in ('history','checksums','runtime','full_backup')}, sort_keys=True))
        return {'pass':0,'fail':1,'pending':2}[result['result']]
    except Exception as exc:
        # SQLAlchemy/driver messages can contain URLs/SQL values; expose class only.
        print(json.dumps({'result':'fail','failure_class':type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
