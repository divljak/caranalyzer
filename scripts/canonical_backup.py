"""Full private application archive; no live restore path or privileged backup login."""
import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.sync_supabase import TABLES, PROTECTED, digest, rows

NAMES = tuple(sorted(TABLES + PROTECTED + ('dealer_vehicle_labels',)))
PREFIX = 'canonical-'
MAX_BYTES = 256 * 1024 * 1024
MIN_FREE = 512 * 1024 * 1024
MAX_SNAPSHOT = 16 * 1024 * 1024
KEEP_DAYS = 30
KEEP_MIN = 14


def row_sql(name):
    key = 'listing_id' if name in ('car_listings','website_listing_states','dealer_candidates','dealer_listing_details','dealer_vehicle_labels') else 'source' if name == 'dealer_source_pauses' else 'id'
    return 'SELECT row_to_json(t) FROM (SELECT * FROM public.' + quote_ident(name) + ' ORDER BY ' + quote_ident(key) + '::text COLLATE \"C\") t'


def private_write(path, data):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    with temporary.open('wb') as f:
        os.chmod(temporary, 0o600)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)


def json_write(path, value):
    private_write(path, json.dumps(value, sort_keys=True, default=str).encode())


def command(args, **kwargs):
    # stderr can contain connection details or private row values: never log it.
    result = subprocess.run(args, capture_output=True, timeout=240, **kwargs)
    if result.returncode:
        raise RuntimeError('Backup subprocess failed: ' + Path(args[0]).name)
    return result.stdout


def engine_for(path):
    path = Path(path)
    if path.stat().st_mode & 0o077:
        raise ValueError('Credential file must be private')
    config = json.loads(path.read_text())
    if (config['host'], config['port'], config['user'], config['dbname'], config['sslmode']) != (
        'aws-0-eu-north-1.pooler.supabase.com', 5432,
        'car_analyzer_backup.ojmokmsjppkqwppmrzpv', 'postgres', 'require'):
        raise ValueError('Unexpected backup target or role')
    url = URL.create('postgresql+psycopg2', username=config['user'], password=config['password'],
                     host=config['host'], port=config['port'], database=config['dbname'])
    return create_engine(url, hide_parameters=True, connect_args={
        'sslmode': 'require', 'connect_timeout': 20, 'application_name': 'car_analyzer_canonical_backup'}), config


def catalog(c):
    queries = {
        'default_acl': "SELECT pg_get_userbyid(defaclrole) AS owner,coalesce(n.nspname,'') AS schema_name,defaclobjtype,defaclacl::text AS acl FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace WHERE n.nspname='public' OR d.defaclnamespace=0 ORDER BY 1,2,3",
        'tables': "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class JOIN pg_namespace n ON n.oid=relnamespace WHERE n.nspname='public' AND relkind='r' ORDER BY 1",
        'columns': "SELECT table_name,column_name,ordinal_position,data_type,udt_name,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' ORDER BY 1,3",
        'constraints': "SELECT cl.relname,conname,pg_get_constraintdef(co.oid) AS definition FROM pg_constraint co JOIN pg_class cl ON cl.oid=conrelid JOIN pg_namespace n ON n.oid=cl.relnamespace WHERE n.nspname='public' ORDER BY 1,2",
        'indexes': "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY 1,2",
        'policies': "SELECT tablename,policyname,permissive,roles,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' ORDER BY 1,2",
        'column_grants': "SELECT cl.relname,att.attname,grantee.rolname,acl.privilege_type,acl.is_grantable FROM pg_attribute att JOIN pg_class cl ON cl.oid=att.attrelid JOIN pg_namespace n ON n.oid=cl.relnamespace CROSS JOIN LATERAL aclexplode(att.attacl) acl JOIN pg_roles grantee ON grantee.oid=acl.grantee WHERE n.nspname='public' AND grantee.rolname LIKE 'car_analyzer_%' ORDER BY 1,2,3,4",
        'grants': "SELECT cl.relname,grantee.rolname,acl.privilege_type,acl.is_grantable FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace CROSS JOIN LATERAL aclexplode(cl.relacl) acl JOIN pg_roles grantee ON grantee.oid=acl.grantee WHERE n.nspname='public' AND grantee.rolname LIKE 'car_analyzer_%' ORDER BY 1,2,3",
        'sequences': "SELECT sequencename,start_value,min_value,max_value,increment_by,cycle,cache_size FROM pg_sequences WHERE schemaname='public' ORDER BY 1",
    }
    return {name: [dict(r) for r in c.execute(text(sql)).mappings()] for name, sql in queries.items()}


def role_manifest(c):
    # All non-system roles referenced by public schema ACLs and policies, no passwords.
    sql = """SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,
      rolreplication,rolbypassrls,rolconnlimit,rolconfig FROM pg_roles
      WHERE rolname LIKE 'car_analyzer_%' OR rolname IN (
        SELECT unnest(roles)::text FROM pg_policies WHERE schemaname='public'
        UNION SELECT pg_get_userbyid(a.grantee) FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace
          CROSS JOIN LATERAL aclexplode(cl.relacl) a WHERE n.nspname='public'
        UNION SELECT pg_get_userbyid(a.grantee) FROM pg_namespace n
          CROSS JOIN LATERAL aclexplode(n.nspacl) a WHERE n.nspname='public'
        UNION SELECT pg_get_userbyid(defaclrole) FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace WHERE n.nspname='public' OR d.defaclnamespace=0
        UNION SELECT pg_get_userbyid(a.grantee) FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace CROSS JOIN LATERAL aclexplode(d.defaclacl) a WHERE n.nspname='public' OR d.defaclnamespace=0) ORDER BY 1"""
    roles = [dict(r) for r in c.execute(text(sql)).mappings() if r['rolname'] not in ('postgres', 'PUBLIC')]
    memberships = [dict(r) for r in c.execute(text("""SELECT member.rolname AS member,parent.rolname AS parent,m.admin_option,m.inherit_option,m.set_option
      FROM pg_auth_members m JOIN pg_roles member ON member.oid=m.member JOIN pg_roles parent ON parent.oid=m.roleid
      WHERE member.rolname LIKE 'car_analyzer_%' OR parent.rolname LIKE 'car_analyzer_%' ORDER BY 1,2""")).mappings()]
    allowed = {r['rolname'] for r in roles} | {'postgres'}
    if any(m['member'] not in allowed or m['parent'] not in allowed for m in memberships):
        raise ValueError('Role membership changed; review recovery contract before exporting')
    return {'roles': roles, 'memberships': memberships, 'passwords_included': False}


def quote_ident(value):
    return '"' + value.replace('"', '""') + '"'


def roles_sql(manifest):
    sql = []
    for role in manifest['roles']:
        if role['rolname'].startswith('pg_'):
            continue  # Built-in roles already exist in the empty PostgreSQL cluster.
        name = quote_ident(role['rolname'])
        # Restore access definitions with logins disabled; reset passwords separately.
        sql.append('CREATE ROLE ' + name + ' NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS ' +
                   ('INHERIT' if role['rolinherit'] else 'NOINHERIT') + ' CONNECTION LIMIT ' + str(role['rolconnlimit']) + ';')
        if role['rolname'].startswith('car_analyzer_'):
            for setting in role['rolconfig'] or []:
                key, value = setting.split('=', 1)
                if not re.fullmatch('[a-z_]+', key):
                    raise ValueError('Unexpected role setting')
                sql.append('ALTER ROLE ' + name + ' SET ' + key + " = '" + value.replace("'", "''") + "';")
    for member in manifest['memberships']:
        options = ', '.join(name.upper() + ' ' + ('TRUE' if member[key] else 'FALSE') for name,key in [('admin','admin_option'),('inherit','inherit_option'),('set','set_option')])
        sql.append('GRANT ' + quote_ident(member['parent']) + ' TO ' + quote_ident(member['member']) + ' WITH ' + options + ';')
    return '\n'.join(sql) + '\n'


def retained_size(root):
    return sum(p.stat().st_size for d in root.glob(PREFIX + '*') if d.is_dir() for p in d.rglob('*') if p.is_file())


def prune(root, now=None):
    """Only completed owned archives older than policy; never touch rollback copies."""
    now = now or datetime.now(timezone.utc)
    complete = []
    for path in root.glob(PREFIX + '*'):
        if path.is_symlink() or not path.is_dir() or not (path / 'manifest.json').is_file():
            continue
        try:
            manifest = json.loads((path / 'manifest.json').read_text())
            when = datetime.fromisoformat(manifest['created_at'])
            if manifest['format'] != 1 or manifest.get('complete') is not True:
                continue
        except (ValueError, KeyError):
            continue
        complete.append((when, path))
    removed = []
    for when, path in sorted(complete, reverse=True)[KEEP_MIN:]:
        if when < now - timedelta(days=KEEP_DAYS):
            shutil.rmtree(path)
            removed.append(path.name)
    return removed


def storage_guard(root, estimate=96*1024*1024):
    size = retained_size(root)
    free = shutil.disk_usage(root).free
    if free < MIN_FREE + estimate or size + estimate > MAX_BYTES:
        raise RuntimeError('Backup storage floor/cap reached; no unexpired or rollback backup was deleted')
    return {'bytes_retained_before': size, 'free_bytes_before': free, 'cap_bytes': MAX_BYTES,
            'free_floor_bytes': MIN_FREE, 'retention_days': KEEP_DAYS, 'minimum_archives': KEEP_MIN}


def export(credentials, root, stage):
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    removed = prune(root)
    storage = storage_guard(root)
    created = datetime.now(timezone.utc)
    engine, config = engine_for(credentials)
    dump_container = 'car-canonical-dump-' + uuid.uuid4().hex[:12]
    work = Path(tempfile.mkdtemp(prefix='.incomplete-', dir=root))
    try:
        with engine.connect().execution_options(isolation_level='REPEATABLE READ') as c, c.begin():
            c.execute(text('SET TRANSACTION READ ONLY'))
            c.execute(text("SET LOCAL idle_in_transaction_session_timeout='300s'"))
            if c.execute(text('SELECT current_user')).scalar() != 'car_analyzer_backup':
                raise ValueError('Unexpected database role')
            state = catalog(c)
            if tuple(r['relname'] for r in state['tables']) != NAMES:
                raise ValueError('Application table inventory changed; review coverage')
            snapshot = c.execute(text('SELECT pg_export_snapshot()')).scalar()
            values = {name: rows(c, name) for name in NAMES}
            checksums = {name: digest(value) for name, value in values.items()}
            role_data = role_manifest(c)
            seq = {}
            for r in state['sequences']:
                name = r['sequencename']
                seq[name] = dict(c.execute(text('SELECT last_value,is_called FROM public.' + quote_ident(name))).mappings().one())
            password = config['password'].replace('\\', '\\\\').replace(':', '\\:')
            pgpass = work / 'pgpass'
            private_write(pgpass, (':'.join(str(config[k]) for k in ('host','port','dbname','user')) + ':' + password + '\n').encode())
            args = ['docker','run','--rm','--name',dump_container,'--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
                    '--tmpfs','/tmp:rw,noexec,nosuid,size=16m','--ulimit','fsize=67108864:67108864','-v',str(work.resolve())+':/backup',
                    '-e','PGPASSFILE=/backup/pgpass','-e','PGSSLMODE=require','postgres:17-alpine',
                    'pg_dump','-h',config['host'],'-p',str(config['port']),'-U',config['user'],'-d',config['dbname'],
                    '--no-password','--schema=public','--enable-row-security','--inserts','--format=custom',
                    '--snapshot='+snapshot,'--file=/backup/application.dump']
            command(args)
            pgpass.unlink()
            # Sequence counters are nontransactional. Verify the state actually saved
            # by pg_dump, rather than comparing against a racing source read.
            for name in seq:
                raw = command(['docker','run','--rm','--network=none','--read-only',
                    '-v',str(work.resolve())+':/backup:ro','postgres:17-alpine','pg_restore',
                    '--data-only','--file=-','--table='+name,'/backup/application.dump']).decode()
                match = re.search(r"setval\('[^']+', (\d+), (true|false)\)", raw)
                if not match:
                    raise ValueError('Archive sequence state missing')
                seq[name] = {'last_value':int(match[1]),'is_called':match[2]=='true'}
            command(['docker','run','--rm','--network=none','--read-only','-v',str(work.resolve())+':/backup:ro',
                     'postgres:17-alpine','pg_restore','--list','/backup/application.dump'])
            json_write(work / 'catalog.json', state)
            json_write(work / 'roles.json', role_data)
            private_write(work / 'roles.sql', roles_sql(role_data).encode())
            json_write(work / 'sequences.json', seq)
            compressed = gzip.compress(json.dumps({'format':1,'tables':values,
                'checksums':checksums},sort_keys=True,default=str).encode())
            if len(compressed) > MAX_SNAPSHOT:
                raise ValueError('Snapshot exceeds backup storage contract')
            private_write(work / 'snapshot.json.gz', compressed)
            version = c.execute(text('SELECT version()')).scalar()
        files = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in work.iterdir() if p.is_file()}
        actual = sum(p.stat().st_size for p in work.iterdir())
        if retained_size(root) + actual > MAX_BYTES or shutil.disk_usage(root).free < MIN_FREE:
            raise RuntimeError('Export exceeded storage policy; incomplete output discarded')
        manifest = {'format':1,'complete':True,'created_at':created.isoformat(),'stage':stage,
                    'scope':'public application schema and all 12 tables; not platform disaster recovery',
                    'database_version':version,'counts':{k:len(v) for k,v in values.items()},
                    'checksums':checksums,'files':files,'archive_bytes':actual,'storage':storage,'expired_removed':removed}
        json_write(work / 'manifest.json', manifest)
        for p in work.iterdir():
            p.chmod(0o600)
        destination = root / (PREFIX + created.strftime('%Y%m%dT%H%M%S%f') + '-' + stage)
        work.rename(destination)
        json_write(root / 'latest.json', {'path':str(destination),**manifest})
        return {'path':str(destination),'counts':manifest['counts'],'archive_bytes':actual,'storage':storage,'expired_removed':removed}
    finally:
        # Also remove a pg_dump container when its client was killed/timed out.
        subprocess.run(['docker','rm','-f',dump_container],capture_output=True,timeout=30)
        engine.dispose()
        if work.exists():
            shutil.rmtree(work)


def validate_bundle(path):
    path = Path(path)
    manifest = json.loads((path/'manifest.json').read_text())
    if manifest.get('format') != 1 or not manifest.get('complete') or set(manifest['counts']) != set(NAMES):
        raise ValueError('Invalid application bundle')
    expected = {'application.dump','catalog.json','roles.json','roles.sql','sequences.json','snapshot.json.gz'}
    if set(manifest['files']) != expected:
        raise ValueError('Invalid bundle files')
    for name, sha in manifest['files'].items():
        if (path/name).is_symlink() or hashlib.sha256((path/name).read_bytes()).hexdigest() != sha:
            raise ValueError('Archive file checksum mismatch')
    data = json.loads(gzip.decompress((path/'snapshot.json.gz').read_bytes()))
    if set(data['tables']) != set(NAMES):
        raise ValueError('Snapshot coverage changed')
    for name in NAMES:
        if digest(data['tables'][name]) != manifest['checksums'][name] or len(data['tables'][name]) != manifest['counts'][name]:
            raise ValueError('Snapshot content checksum mismatch')
    return manifest


def verify_restore(path):
    """Only a fresh Docker database with network disabled, no ports or persistent volume."""
    path = Path(path).resolve()
    manifest = validate_bundle(path)
    container = 'car-backup-recovery-' + uuid.uuid4().hex[:12]
    # Docker exec uses a private stdin pipe, never credentials or data in arguments.
    try:
        command(['docker','run','-d','--name',container,'--network=none','--memory=256m',
                 '--tmpfs','/var/lib/postgresql/data:rw,nosuid,size=192m','-e','POSTGRES_HOST_AUTH_METHOD=trust',
                 '-v',str(path)+':/backup:ro','postgres:17-alpine'])
        import time
        for attempt in range(30):
            ready = subprocess.run(['docker','exec',container,'sh','-c',
                '[ \"$(cat /proc/1/comm)\" = postgres ] && pg_isready -U postgres'], capture_output=True)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Scratch database did not become ready')
        command(['docker','exec',container,'psql','-U','postgres','-d','postgres','-v','ON_ERROR_STOP=1','-f','/backup/roles.sql'])
        command(['docker','exec',container,'createdb','-U','postgres','delivery_recovery_canonical'])
        command(['docker','exec',container,'psql','-U','postgres','-d','delivery_recovery_canonical',
                 '-v','ON_ERROR_STOP=1','-c','DROP SCHEMA public'])
        command(['docker','exec',container,'pg_restore','-U','postgres','-d','delivery_recovery_canonical',
                 '--single-transaction','--exit-on-error','--no-owner','/backup/application.dump'])
        # Ask psql for exactly the same JSON rows used by the export's checksum helper.
        found = {}
        for name in NAMES:
            raw = command(['docker','exec',container,'psql','-U','postgres','-d','delivery_recovery_canonical',
                           '-At','-c',row_sql(name)])
            values = [json.loads(line) for line in raw.decode().splitlines()]
            if digest(values) != manifest['checksums'][name]:
                raise ValueError('Restored data checksum mismatch: '+name)
            found[name] = len(values)
        # Constraint/index/RLS/policy/column/grant definitions must match as well.
        saved = json.loads((path/'catalog.json').read_text())
        sql_map = catalog_sql_for_restore()
        for name, query in sql_map.items():
            raw = command(['docker','exec',container,'psql','-U','postgres','-d','delivery_recovery_canonical','-At',
                           '-c','SELECT row_to_json(t) FROM ('+query+') t'])
            actual = [json.loads(line) for line in raw.decode().splitlines()]
            if actual != saved[name]:
                raise ValueError('Restored schema/access mismatch: '+name)
        sequence_saved = json.loads((path/'sequences.json').read_text())
        for name, expected in sequence_saved.items():
            raw = command(['docker','exec',container,'psql','-U','postgres','-d','delivery_recovery_canonical','-At','-c',
                           'SELECT row_to_json(t) FROM (SELECT last_value,is_called FROM public.'+quote_ident(name)+') t'])
            if json.loads(raw) != expected:
                raise ValueError('Restored sequence mismatch')
        result = {'verified_at':datetime.now(timezone.utc).isoformat(),'bundle':str(path),'counts':found,
                  'content_checksums_match':True,'schema_access_match':True,'sequence_state_match':True,
                  'scratch_network':'none','scratch_removed':True}
    finally:
        cleanup = subprocess.run(['docker','rm','-f',container],capture_output=True,timeout=30)
        if cleanup.returncode:
            raise RuntimeError('Scratch cleanup failed; inspect isolated container')
    json_write(path.parent/'latest-restore.json',result)
    return result


def catalog_sql_for_restore():
    # Reuse catalog query definitions without a second independently maintained list.
    class Recorder:
        def __init__(self): self.queries = {}
        def execute(self, sql): self.queries[len(self.queries)] = str(sql); return self
        def mappings(self): return []
    r = Recorder()
    names = list(catalog(r))
    return dict(zip(names, r.queries.values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation',required=True)
    dump = sub.add_parser('export')
    dump.add_argument('--credentials',type=Path,required=True)
    dump.add_argument('--directory',type=Path,required=True)
    dump.add_argument('--stage',choices=['before','after','manual'],required=True)
    restore = sub.add_parser('verify-restore')
    restore.add_argument('bundle',type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    started = datetime.now(timezone.utc).isoformat()
    marker_root = args.directory if args.operation == 'export' else args.bundle.parent
    marker = marker_root / ('latest-attempt.json' if args.operation == 'export' else 'latest-restore-attempt.json')
    try:
        result = export(args.credentials,args.directory,args.stage) if args.operation == 'export' else verify_restore(args.bundle)
        json_write(marker, {'started_at':started,'finished_at':datetime.now(timezone.utc).isoformat(),'status':'success','operation':args.operation,**result})
        print(json.dumps(result,sort_keys=True))
        return 0
    except Exception as exc:
        failure = {'started_at':started,'finished_at':datetime.now(timezone.utc).isoformat(),'status':'failed','operation':args.operation,'failure_class':type(exc).__name__}
        if marker_root.is_dir():
            json_write(marker,failure)
        print(json.dumps(failure),file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
