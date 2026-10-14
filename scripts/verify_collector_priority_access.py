#!/usr/bin/env python3
"""Exercise the installed priority query as its actual login, without source visits.

Separate private read-only snapshots prove preservation. Denial probes have no
matching rows and must fail with permission denied; no evidence rows are inserted.
Only safe aggregates/hashes are written, never filters, baselines, notes or passwords.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys

TABLES = ('car_listings','listing_snapshots','scraping_logs','website_collection_budget',
          'website_collection_runs','website_listing_states','website_listing_checks',
          'dealer_vehicle_labels','dealer_listing_details','dealer_candidates',
          'dealer_model_searches','dealer_source_pauses')
KEYS = {'car_listings':'listing_id','website_listing_states':'listing_id',
        'dealer_vehicle_labels':'listing_id','dealer_listing_details':'listing_id',
        'dealer_candidates':'listing_id','dealer_source_pauses':'source'}
PROTECTED = ('dealer_listing_details','dealer_candidates','dealer_model_searches','dealer_source_pauses')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def snapshot(engine):
    from sqlalchemy import text
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as c, c.begin():
        c.execute(text('SET TRANSACTION READ ONLY'))
        c.execute(text("SET LOCAL statement_timeout='30s'"))
        result = {}
        for name in TABLES:
            key = KEYS.get(name,'id')
            data = c.execute(text(f'SELECT row_to_json(t) FROM (SELECT * FROM public.{name} ORDER BY {key}::text COLLATE "C") t')).scalars().all()
            result[name] = {'rows':len(data),'sha256':digest(data)}
        budget = dict(c.execute(text('SELECT day,search_visits,detail_visits,last_visit_at,pause_until FROM website_collection_budget WHERE id=1')).mappings().one())
        result['budget'] = {k:v.isoformat() if hasattr(v,'isoformat') else v for k,v in budget.items()}
        result['sequences'] = {}
        for name in ('listing_snapshots','scraping_logs','website_collection_runs','website_listing_checks'):
            result['sequences'][name] = list(c.execute(text(f'SELECT last_value,is_called FROM public.{name}_id_seq')).one())
        result['latest_observation'] = c.execute(text('SELECT max(observed_at)::text FROM listing_snapshots')).scalar()
        result['clean_anchor'] = c.execute(text("SELECT max(end_time)::text FROM scraping_logs WHERE status='completed' AND errors_count=0")).scalar()
        return result


def boundaries(engine):
    from sqlalchemy import text
    failures = []
    with engine.connect() as c, c.begin():
        c.execute(text('SET TRANSACTION READ ONLY'))
        for table in TABLES:
            for privilege in ('INSERT','UPDATE','DELETE'):
                for role in ('car_analyzer_app','anon','authenticated','car_analyzer_backup','car_analyzer_collector'):
                    denied = ((role in ('anon','authenticated','car_analyzer_backup')) or
                              (role=='car_analyzer_app' and table != 'dealer_model_searches') or
                              (role=='car_analyzer_collector' and (privilege=='DELETE' or table in PROTECTED or
                                (table in ('listing_snapshots','website_listing_checks') and privilege=='UPDATE'))))
                    if denied and c.execute(text('SELECT has_table_privilege(:role,:table,:privilege)'),
                                            {'role':role,'table':'public.'+table,'privilege':privilege}).scalar():
                        failures.append(f'{role}:{table}:{privilege}')
                    if denied and privilege in ('INSERT','UPDATE') and c.execute(
                            text('SELECT has_any_column_privilege(:role,:table,:privilege)'),
                            {'role':role,'table':'public.'+table,'privilege':privilege}).scalar():
                        failures.append(f'{role}:{table}:column_{privilege}')
        for column in ('filters','baseline','baseline_as_of'):
            if c.execute(text("SELECT has_column_privilege('car_analyzer_app','public.dealer_model_searches',:column,'UPDATE')"),{'column':column}).scalar():
                failures.append('app_search_update:'+column)
        for role in ('car_analyzer_app','car_analyzer_backup','car_analyzer_collector'):
            row=c.execute(text('SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=:role'),{'role':role}).one()
            if any(row): failures.append('privileged_role:'+role)
            if c.execute(text("SELECT has_schema_privilege(:role,'public','CREATE')"),{'role':role}).scalar():
                failures.append('schema_create:'+role)
        for role in ('car_analyzer_app','car_analyzer_backup','anon','authenticated'):
            if c.execute(text("SELECT pg_has_role(:role,'car_analyzer_collector','MEMBER')"),{'role':role}).scalar():
                failures.append('collector_membership:'+role)
        if c.execute(text("SELECT rolcanlogin FROM pg_roles WHERE rolname='car_analyzer_sync'")).scalar():
            failures.append('legacy_sync_login')
        if c.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename=ANY(:tables) AND NOT rowsecurity"),{'tables':list(TABLES)}).scalar():
            failures.append('rls_disabled')
    return failures


def denied_operations(engine):
    """Actual collector login must reject all private/history mutations with 42501."""
    from sqlalchemy import text
    result = {}
    with engine.connect() as c, c.begin():
        for name in PROTECTED + ('listing_snapshots','website_listing_checks'):
            column = 'source' if name=='dealer_source_pauses' else 'id' if name in ('listing_snapshots','website_listing_checks','dealer_model_searches') else 'listing_id'
            for op in ('UPDATE','DELETE'):
                tx = c.begin_nested()
                try:
                    sql = f'UPDATE public.{name} SET {column}={column} WHERE false' if op=='UPDATE' else f'DELETE FROM public.{name} WHERE false'
                    c.execute(text(sql))
                    result[f'{name}:{op}']='unexpectedly_allowed'
                except Exception as exc:
                    result[f'{name}:{op}']=getattr(getattr(exc,'orig',None),'pgcode',type(exc).__name__)
                finally:
                    tx.rollback()
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir',type=Path,required=True)
    parser.add_argument('--runtime-manifest',type=Path,required=True)
    parser.add_argument('--collector-credentials',type=Path,required=True)
    parser.add_argument('--backup-credentials',type=Path,required=True)
    parser.add_argument('--before',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.output.exists(): raise ValueError('Refuse to overwrite an earlier audit')
    sys.path.insert(0,str(args.runtime_dir.resolve()))
    sys.path.insert(1,str(Path.cwd()))  # Image-owned config/dependencies under /app.
    from database.collector_connection import private_engine
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker
    result={'captured_at':datetime.now(timezone.utc).isoformat(),'source_visits':0,'evidence_inserts':0,'budget_resets':0}
    backup=private_engine(args.backup_credentials,'car_analyzer_backup')
    collector=private_engine(args.collector_credentials,'car_analyzer_collector')
    try:
        manifest=json.loads(args.runtime_manifest.read_text())
        module=importlib.import_module('database.collection_priority')
        module_path=Path(module.__file__).resolve()
        expected=(args.runtime_dir/'database/collection_priority.py').resolve()
        actual_hash=hashlib.sha256(module_path.read_bytes()).hexdigest()
        if module_path!=expected or actual_hash!=manifest['file_sha256']['database/collection_priority.py']:
            raise ValueError('Installed priority module does not match manifest')
        result['priority_sha256']=actual_hash
        result['before_probe']=snapshot(backup)
        result['boundary_failures']=boundaries(backup)
        result['denied_operations']=denied_operations(collector)
        try:
            with collector.connect().execution_options(isolation_level='REPEATABLE READ') as c, c.begin():
                c.execute(text('SET TRANSACTION READ ONLY'))
                result['actual_role']=c.execute(text('SELECT current_user')).scalar()
                if result['actual_role']!='car_analyzer_collector': raise ValueError('Wrong collector role')
                private_counts={table:c.execute(text(f'SELECT count(*) FROM public.{table}')).scalar() for table in PROTECTED[:3]}
                if any(private_counts[t]!=result['before_probe'][t]['rows'] for t in private_counts):
                    raise ValueError('Collector RLS hides priority inputs')
                result['collector_visible_private_counts']=private_counts
                rows,searches=module.load_priority_inputs(c)
                plan=module.database_plan(c,datetime.now(timezone.utc),20)
                # Exercise the same adapter/session binding used by installed collection.
                adapter=module.PriorityWebsiteStore(collector)
                adapter.sessions=sessionmaker(bind=c,expire_on_commit=False)
                selected=adapter.candidates(datetime.now(timezone.utc),20)
                if selected!=plan['selected_ids']: raise ValueError('Adapter/plan discrepancy')
                result['queue']={'readable':True,'tracked_rows':len(rows),'saved_scopes':len(searches),
                                 'selected_count':len(selected),'due_eligible':plan['due_eligible'],
                                 'remaining_allowance':plan['remaining_allowance'],'paused':plan['paused'],
                                 'unique_selection':len(set(selected))==len(selected),'selected_ids_sha256':digest(selected)}
        except Exception as exc:
            result['queue']={'readable':False,'error_class':type(exc).__name__,
                             'sqlstate':getattr(getattr(exc,'orig',None),'pgcode',None)}
        result['after_probe']=snapshot(backup)
        result['probe_data_unchanged']=result['before_probe']==result['after_probe']
        if args.before:
            old=json.loads(args.before.read_text())
            result['repair_data_unchanged']=old['after_probe']==result['after_probe']
        result['pass']=(result['queue']['readable'] and not result['boundary_failures'] and
                        all(x=='42501' for x in result['denied_operations'].values()) and
                        result['probe_data_unchanged'] and result.get('repair_data_unchanged',True))
    except Exception as exc:
        result.update({'pass':False,'failure_class':type(exc).__name__})
    finally:
        collector.dispose();backup.dispose()
        args.output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        with args.output.open('x') as out:
            os.chmod(args.output,0o600);json.dump(result,out,sort_keys=True,indent=2)
        print(json.dumps({k:v for k,v in result.items() if k not in ('before_probe','after_probe')},sort_keys=True))
    return 0 if result.get('pass') else 1


if __name__=='__main__':
    raise SystemExit(main())
