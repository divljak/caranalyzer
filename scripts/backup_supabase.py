"""Consistent, verified application recovery snapshot with a read-only login."""
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import text
from scripts.sync_supabase import TABLES, PROTECTED, digest, private_json, rows, stamp
from scripts.verify_delivery_backup import verify


def backup(engine, directory, stage):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    names = TABLES + ('dealer_vehicle_labels',) + PROTECTED
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as c:
        with c.begin():
            c.execute(text('SET TRANSACTION READ ONLY'))
            data = {name: rows(c, name) for name in names}
    checksums = {name: digest(value) for name, value in data.items()}
    path = directory / ('supabase-canonical-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '-' + stage + '.json.gz')
    private_json(path, {'format': 1, 'created_at': stamp(), 'tables': data, 'checksums': checksums}, compressed=True)
    counts = verify(path)
    # Keep 30 verified snapshots IN ADDITION to retained pre-cutover backups.
    for old in sorted(directory.glob('supabase-canonical-*.json.gz'), reverse=True)[30:]:
        old.unlink()
    return {'path': str(path), 'counts': counts, 'checksums': checksums}
