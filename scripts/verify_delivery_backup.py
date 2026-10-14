"""Verify a recovery snapshot; optionally restore it into an EMPTY loopback scratch DB."""
import argparse
import gzip
import json
import os
from pathlib import Path
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.sync_supabase import TABLES, PROTECTED, digest, rows


def verify(path, engine=None):
    data = json.loads(gzip.decompress(Path(path).read_bytes()))
    expected = set(TABLES + PROTECTED + ('dealer_vehicle_labels',))
    if data.get('format') != 1 or set(data['tables']) != expected:
        raise ValueError('Unexpected snapshot format or tables')
    for name, values in data['tables'].items():
        if digest(values) != data['checksums'][name]:
            raise ValueError('Snapshot checksum mismatch')
    if engine is not None:
        with engine.begin() as c:
            for name in expected:
                if c.execute(text(f'SELECT count(*) FROM {name}')).scalar():
                    raise ValueError('Restore requires empty scratch tables')
            for name in TABLES + ('dealer_vehicle_labels',) + PROTECTED:
                c.execute(text(f'INSERT INTO {name} SELECT * FROM json_populate_recordset(NULL::{name},CAST(:records AS json))'),
                          {'records':json.dumps(data['tables'][name])})
                if digest(rows(c, name)) != data['checksums'][name]:
                    raise ValueError('Restored content mismatch')
            for name in ('listing_snapshots','scraping_logs','website_collection_runs','website_listing_checks'):
                c.execute(text(f"SELECT setval(pg_get_serial_sequence('{name}','id'),GREATEST(COALESCE((SELECT max(id) FROM {name}),1),1),true)"))
    return {name:len(value) for name,value in data['tables'].items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('backup', type=Path)
    parser.add_argument('--restore-empty-scratch', action='store_true')
    args = parser.parse_args()
    engine = None
    if args.restore_empty_scratch:
        url = make_url(os.environ['RECOVERY_DATABASE_URL'])
        if url.host not in {'127.0.0.1','localhost','::1'} or not url.database.startswith('delivery_recovery_'):
            raise ValueError('Use a loopback delivery_recovery_* scratch database')
        engine = create_engine(url)
    print(json.dumps(verify(args.backup, engine), sort_keys=True))


if __name__ == '__main__':
    main()
