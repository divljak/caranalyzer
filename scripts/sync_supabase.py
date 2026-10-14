"""One-way evidence delivery. Credentials are read from a private mounted file."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from sqlalchemy import MetaData, Table, create_engine, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_labels import refresh_labels

TABLES = ('car_listings', 'listing_snapshots', 'scraping_logs', 'website_collection_budget',
          'website_collection_runs', 'website_listing_states', 'website_listing_checks')
PROTECTED = ('dealer_model_searches', 'dealer_candidates', 'dealer_listing_details', 'dealer_source_pauses')
IMMUTABLE = {'listing_snapshots', 'website_listing_checks'}
IDENTITY = {'listing_snapshots': ('listing_id', 'observed_at'), 'scraping_logs': ('session_id',),
            'website_collection_runs': ('session_id',), 'website_listing_checks': ('listing_id', 'run_id', 'checked_at')}


class DeliveryError(RuntimeError):
    pass


def stamp():
    return datetime.now(timezone.utc).isoformat()


def rows(connection, table):
    key = 'listing_id' if table in ('car_listings', 'website_listing_states', 'dealer_candidates', 'dealer_listing_details', 'dealer_vehicle_labels') else 'source' if table == 'dealer_source_pauses' else 'id'
    return connection.execute(text(f'SELECT row_to_json(t) FROM (SELECT * FROM {table} ORDER BY {key}::text COLLATE "C") t')).scalars().all()


def digest(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def private_json(path, value, compressed=False):
    temporary = path.with_suffix(path.suffix + '.partial')
    content = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    with temporary.open('wb') as out:
        os.chmod(temporary, 0o600)
        out.write(gzip.compress(content) if compressed else content)
        out.flush()
        os.fsync(out.fileno())
    decoded = gzip.decompress(temporary.read_bytes()) if compressed else temporary.read_bytes()
    if json.loads(decoded) != value:
        raise DeliveryError('Backup verification failed')
    temporary.replace(path)


def deliver(source_engine, target_engine, directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    with source_engine.connect().execution_options(isolation_level='REPEATABLE READ') as source, target_engine.begin() as target:
        source.execute(text('SET TRANSACTION READ ONLY'))
        if not target.execute(text('SELECT pg_try_advisory_xact_lock(731400220)')).scalar():
            raise DeliveryError('Delivery is already running')
        # Synchronizers serialize; web reads and Following writes remain available.
        target.execute(text('LOCK TABLE ' + ','.join(n for n in TABLES + ('dealer_vehicle_labels',) if n not in IMMUTABLE) + ' IN SHARE ROW EXCLUSIVE MODE'))
        clean_sql = "SELECT max(end_time) FROM scraping_logs WHERE status='completed' AND errors_count=0"
        clean, current = source.execute(text(clean_sql)).scalar(), target.execute(text(clean_sql)).scalar()
        if not clean or (current and clean < current):
            raise DeliveryError('Missing or regressing clean collection anchor')
        if source.execute(text('SELECT count(*) FROM car_listings')).scalar() == 0:
            raise DeliveryError('Empty source')
        backup_tables = TABLES + ('dealer_vehicle_labels',) + PROTECTED
        backup = {name: rows(target, name) for name in backup_tables}
        protected = {name: digest(backup[name]) for name in PROTECTED}
        backup_path = directory / ('supabase-before-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.json.gz')
        private_json(backup_path, {'format': 1, 'created_at': stamp(), 'tables': backup,
                                 'checksums': {name: digest(value) for name,value in backup.items()}}, compressed=True)
        changed, source_manifest = {}, {}
        for name in TABLES:
            src, dst = Table(name, MetaData(), autoload_with=source), Table(name, MetaData(), autoload_with=target)
            columns = list(src.c.keys())
            if set(columns) - set(dst.c.keys()):
                raise DeliveryError(f'Schema mismatch in {name}')
            key = 'listing_id' if name in ('car_listings', 'website_listing_states') else 'id'
            changed[name] = 0
            for batch in source.execute(select(src).order_by(src.c[key])).mappings().partitions(300):
                payload = [dict(row) for row in batch]
                existing = {row[key]: dict(row) for row in target.execute(select(dst).where(dst.c[key].in_([r[key] for r in payload]))).mappings()}
                updates = []
                for row in payload:
                    old = existing.get(row[key])
                    if old and any(row[field] != old[field] for field in IDENTITY.get(name, ())):
                        raise DeliveryError(f'Identity collision in {name}')
                    if old and name in IMMUTABLE and row != {c:old[c] for c in columns}:
                        raise DeliveryError(f'Immutable evidence conflict in {name}')
                    if old is None or any(row[c] != old[c] for c in columns):
                        updates.append(row)
                if updates:
                    statement = insert(dst).values(updates)
                    if name not in IMMUTABLE:
                        statement = statement.on_conflict_do_update(index_elements=[dst.c[key]], set_={c:statement.excluded[c] for c in columns if c != key})
                    target.execute(statement)
                    changed[name] += len(updates)
            # Verify every source row's content, while retaining destination-only history.
            original = rows(source, name)
            expected_keys = {r[key] for r in original}
            actual = [r for r in rows(target, name) if r[key] in expected_keys]
            if digest(original) != digest(actual):
                raise DeliveryError(f'Content verification failed for {name}')
            source_manifest[name] = {'rows':len(original), 'sha256':digest(original)}
        refresh_labels(target)
        for name in PROTECTED:
            if digest(rows(target, name)) != protected[name]:
                raise DeliveryError(f'Protected dealer state changed in {name}')
        for name in ('listing_snapshots','scraping_logs','website_collection_runs','website_listing_checks'):
            target.execute(text(f"SELECT setval(pg_get_serial_sequence('{name}','id'), GREATEST(COALESCE((SELECT max(id) FROM {name}),1),(SELECT last_value FROM {name}_id_seq)),true)"))
        for query in ('SELECT count(*) FROM listing_snapshots s LEFT JOIN car_listings c USING(listing_id) WHERE c.listing_id IS NULL',
                      'SELECT count(*) FROM website_listing_checks c LEFT JOIN website_collection_runs r ON r.id=c.run_id WHERE r.id IS NULL'):
            if target.execute(text(query)).scalar():
                raise DeliveryError('Orphaned evidence')
        result = {'synced_at':stamp(), 'clean_collection_at':clean.isoformat()+'Z', 'latest_observation_at':source.execute(text('SELECT max(observed_at) FROM listing_snapshots')).scalar().isoformat()+'Z',
                  'source':source_manifest, 'destination_counts':{n:target.execute(text(f'SELECT count(*) FROM {n}')).scalar() for n in backup_tables},
                  'changed':changed, 'protected_checksums':protected, 'backup':str(backup_path)}
    # Written only after the target transaction commits. Failures never advance this marker.
    private_json(directory / 'latest-success.json', result)
    # Keep at least 30 complete target recovery snapshots; never rotate an unverified one.
    for old in sorted(directory.glob('supabase-before-*.json.gz'), reverse=True)[30:]:
        old.unlink()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    args = parser.parse_args()
    if (args.state_dir / 'direct-active').exists():
        raise DeliveryError('Direct Supabase collector is active; legacy synchronization disabled')
    if args.credentials.stat().st_mode & 0o077:
        raise DeliveryError('Credential file must be readable only by its owner')
    credential = json.loads(args.credentials.read_text())
    if credential['host'] != 'aws-0-eu-north-1.pooler.supabase.com' or credential['user'] != 'car_analyzer_sync.ojmokmsjppkqwppmrzpv' or credential['sslmode'] != 'require':
        raise DeliveryError('Unexpected delivery target')
    target = create_engine(URL.create('postgresql+psycopg2', username=credential['user'], password=credential['password'], host=credential['host'], port=credential['port'], database=credential['dbname']),
                           connect_args={'sslmode':credential['sslmode'], 'connect_timeout':20}, poolclass=NullPool, isolation_level='REPEATABLE READ')
    from database.db_manager import db_manager
    for attempt in range(3):
        try:
            print(json.dumps(deliver(db_manager.engine, target, args.state_dir), sort_keys=True))
            return 0
        except OperationalError:
            if attempt == 2:
                print('Delivery connection failed after three bounded attempts.', file=sys.stderr)
                return 1
            time.sleep(15 * (attempt + 1))
        except Exception as exc:
            print(f'Delivery aborted ({type(exc).__name__}); target transaction rolled back. Inspect private diagnostics.', file=sys.stderr)
            if isinstance(exc, DeliveryError):
                print(str(exc), file=sys.stderr)
            elif hasattr(exc, 'orig'):
                print('Database SQLSTATE: ' + str(getattr(exc.orig, 'pgcode', None)), file=sys.stderr)
            # SQLAlchemy exceptions can contain bound values; do not emit raw errors.
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
