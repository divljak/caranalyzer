"""Direct Supabase collector. Schema is administrator-managed; writes are not replayed."""
import argparse
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import sys
import time
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database.collector_connection import LockedSession, private_engine, verify_connection
from scripts.backup_supabase import backup
from scripts.sync_supabase import private_json, stamp

LOCK = 731400220  # Same lock as retired sync and website collectors.


@contextmanager
def pinned_collection(db, store):
    with db.engine.connect() as c:
        if not c.execute(text('SELECT pg_try_advisory_lock(:key)'), {'key': LOCK}).scalar():
            raise RuntimeError('Another collection or sync holds the lock')
        c.commit()  # Keep the session lock, not an idle transaction, during browser waits.
        previous_db, previous_store = db.SessionLocal, store.sessions
        maker = sessionmaker(bind=c, class_=LockedSession, expire_on_commit=False)
        db.SessionLocal = store.sessions = maker
        try:
            yield c
        finally:
            db.SessionLocal, store.sessions = previous_db, previous_store
            if not c.closed and not c.invalidated:
                c.rollback()
                c.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': LOCK})
                c.commit()


def preflight(engine, role, sleep=time.sleep):
    # Retry only before evidence writes; ambiguous commits must never be replayed.
    for attempt in range(3):
        try:
            return verify_connection(engine, role)
        except OperationalError:
            if attempt == 2:
                raise
            sleep(15 * (attempt + 1))


def expire_interrupted(c):
    with c.begin():
        c.execute(text("""UPDATE website_collection_runs SET status='failed',finished_at=timezone('UTC',now()),
          errors=errors+1, report='{"status":"failed","stopped_reason":"previous_direct_process_interrupted"}'
          WHERE session_id LIKE 'direct-%' AND status='running' AND finished_at IS NULL"""))
        c.execute(text("""UPDATE scraping_logs SET status='failed',end_time=timezone('UTC',now()),
          errors_count=coalesce(errors_count,0)+1,error_message='previous_direct_process_interrupted'
          WHERE session_id LIKE 'direct-%' AND status='running' AND end_time IS NULL"""))


def verify_writes(c):
    """Real role operations, rolled back. No fabricated listing or price evidence."""
    tx = c.begin()
    try:
        # Update the existing budget to exactly itself; test row permissions and locking.
        c.execute(text('UPDATE website_collection_budget SET search_visits=search_visits WHERE id=1'))
        c.execute(text('UPDATE car_listings SET price=price WHERE listing_id=(SELECT listing_id FROM car_listings ORDER BY listing_id LIMIT 1)'))
        c.execute(text("""INSERT INTO website_collection_runs(session_id,started_at,status,source_query,search_limit,detail_limit,
          search_attempted,detail_attempted,discovery_complete,details_completed,observations,new_listings,errors)
          VALUES('verify-'||gen_random_uuid(),timezone('UTC',now()),'verification','no-source-visits',3,20,0,0,0,0,0,0,0)"""))
        c.execute(text("""INSERT INTO scraping_logs(session_id,start_time,status,total_listings_found,new_listings_added,
          updated_listings,errors_count,missing_from_window_count) VALUES('verify-'||gen_random_uuid(),timezone('UTC',now()),
          'verification',0,0,0,0,0)"""))
        from market_labels import refresh_labels
        refresh_labels(c)
        return {'rolled_back': True, 'source_visits': 0, 'listing_evidence_inserted': 0}
    finally:
        tx.rollback()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials', type=Path, required=True)
    parser.add_argument('--backup-credentials', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args(argv)
    os.environ['CAR_ANALYZER_COLLECTOR'] = 'supabase'
    os.environ['COLLECTOR_CREDENTIAL_FILE'] = str(args.credentials)
    # Errors are reduced to classes below; neither URLs nor SQL parameters are logged.
    logging.basicConfig(level=logging.WARNING)
    result = {'started_at': stamp(), 'architecture': 'direct_supabase', 'verify_only': args.verify_only}
    status = 1
    try:
        from database.db_manager import db_manager
        from database.website_tracking import WebsiteStore
        from run_website_sync import utcnow, sync
        from scrapers.olx_website_collector import WebsiteBrowser
        from market_labels import refresh_labels
        backup_engine = private_engine(args.backup_credentials, 'car_analyzer_backup')
        result['connection'] = preflight(db_manager.engine, 'car_analyzer_collector')
        preflight(backup_engine, 'car_analyzer_backup')
        store = WebsiteStore(db_manager.engine)
        with pinned_collection(db_manager, store) as c:
            result['before'] = backup(backup_engine, args.state_dir, 'before')
            if args.verify_only:
                result['verification'] = verify_writes(c)
                status = 0
            else:
                expire_interrupted(c)
                budget = store.budget(utcnow())
                paused = budget['pause_until'] and budget['pause_until'] > utcnow().isoformat() + 'Z'
                exhausted = budget['search_used'] >= 3 and budget['detail_used'] >= 20
                # Log honest skipped attempts without launching a browser or spending budgets.
                if paused or exhausted:
                    report = sync(db_manager, store, None, session_prefix='direct-')
                else:
                    with WebsiteBrowser() as browser:
                        report = sync(db_manager, store, browser, search_pages=3, detail_checks=20,
                                      delay=15, session_prefix='direct-')
                result['collection'] = report
                status = 0 if report['status'] == 'completed' else 1
                with c.begin():
                    refresh_labels(c)
            result['after'] = backup(backup_engine, args.state_dir, 'after')
            if args.verify_only and result['before']['checksums'] != result['after']['checksums']:
                raise RuntimeError('Verification changed application content')
            result['backup_verified'] = True
            marker = 'latest-direct-verification-backup.json' if args.verify_only else 'latest-direct-backup.json'
            private_json(args.state_dir / marker, result)
    except Exception as exc:
        status = 1
        result['failure_class'] = type(exc).__name__
        if hasattr(exc, 'orig'):
            result['sqlstate'] = getattr(exc.orig, 'pgcode', None)
        print('Direct collection stopped; no fallback or automatic evidence replay.', file=sys.stderr)
    finally:
        result.update(finished_at=stamp(), exit_code=status)
        args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        marker = 'latest-direct-verification.json' if args.verify_only else 'latest-direct-attempt.json'
        private_json(args.state_dir / marker, result)
        # Output useful counts/status, never complete private backup content or credentials.
        print(json.dumps(result, sort_keys=True))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
