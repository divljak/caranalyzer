"""Private, bounded SSH backup copying and a forced-command archive-only endpoint."""
import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid

SOURCE = Path('/opt/car-analyzer/.daily-delivery/canonical-backups')
PATTERN = re.compile(r'canonical-[0-9]{8}T[0-9]{12}-(after|manual)')
PAYLOAD = {'application.dump','catalog.json','roles.json','roles.sql','sequences.json','snapshot.json.gz'}
FILES = PAYLOAD | {'manifest.json','restore-verified.json'}
TABLES = {'car_listings','listing_snapshots','scraping_logs','website_collection_budget','website_collection_runs',
          'website_listing_states','website_listing_checks','dealer_vehicle_labels','dealer_model_searches',
          'dealer_candidates','dealer_listing_details','dealer_source_pauses'}
CAP = 512 * 1024 * 1024
FREE_FLOOR = 5 * 1024 * 1024 * 1024
RESERVE = 96 * 1024 * 1024
ARCHIVE_LIMIT = 96 * 1024 * 1024
KEEP_DAYS = 90
KEEP_MIN = 14


def stamp(): return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    tmp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    with tmp.open('w') as f:
        os.chmod(tmp, 0o600)
        json.dump(value, f, sort_keys=True)
        f.flush(); os.fsync(f.fileno())
    tmp.replace(path)


def digest(values):
    return hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def receipt(path):
    if not PATTERN.fullmatch(path.name) or path.is_symlink() or not path.is_dir():
        raise ValueError('Invalid archive path')
    for name in ('manifest.json','restore-verified.json'):
        p = path/name
        if p.is_symlink() or not p.is_file() or p.stat().st_size > 1024*1024:
            raise ValueError('Recovery receipt missing or invalid')
    manifest_raw = (path/'manifest.json').read_bytes()
    manifest = json.loads(manifest_raw)
    proof = json.loads((path/'restore-verified.json').read_bytes())
    if manifest.get('format') != 1 or manifest.get('complete') is not True or set(manifest['files']) != PAYLOAD:
        raise ValueError('Incomplete archive')
    if set(manifest['counts']) != TABLES or set(manifest['checksums']) != TABLES:
        raise ValueError('Application coverage changed')
    if proof.get('manifest_sha256') != hashlib.sha256(manifest_raw).hexdigest():
        raise ValueError('Recovery receipt belongs to another manifest')
    if Path(proof.get('bundle','')).name != path.name or proof.get('counts') != manifest['counts']:
        raise ValueError('Recovery receipt belongs to another bundle')
    if any(proof.get(k) is not True for k in ('content_checksums_match','schema_access_match','sequence_state_match','scratch_removed')):
        raise ValueError('Archive has not passed isolated recovery')
    return manifest, proof


def validate_copy(path):
    manifest, proof = receipt(path)
    if {p.name for p in path.iterdir()} != FILES:
        raise ValueError('Unexpected archive contents')
    total = 0
    for name in PAYLOAD:
        p=path/name
        if p.is_symlink() or not p.is_file(): raise ValueError('Invalid archive file')
        total += p.stat().st_size
        if total > ARCHIVE_LIMIT: raise ValueError('Archive exceeds transfer bound')
        with p.open('rb') as f: sha = hashlib.file_digest(f,'sha256').hexdigest()
        if sha != manifest['files'][name]: raise ValueError('Copied file checksum mismatch')
    with gzip.open(path/'snapshot.json.gz','rb') as f:
        decoded=f.read(128*1024*1024+1)
    if len(decoded) > 128*1024*1024: raise ValueError('Snapshot decompression exceeds bound')
    data=json.loads(decoded)
    if data.get('format') != 1 or set(data['tables']) != TABLES: raise ValueError('Invalid copied snapshot')
    for name in TABLES:
        if digest(data['tables'][name]) != manifest['checksums'][name] or len(data['tables'][name]) != manifest['counts'][name]:
            raise ValueError('Copied content checksum mismatch')
    return manifest, proof


def inventory(root=SOURCE):
    daily = {}
    for p in root.glob('canonical-*'):
        try:
            manifest,proof=receipt(p)
            date=datetime.fromisoformat(manifest['created_at'])
            candidate={'name':p.name,'created_at':manifest['created_at'],'manifest_sha256':proof['manifest_sha256'],
                       'restored_at':proof['verified_at'],'counts':manifest['counts']}
            day=date.astimezone(timezone.utc).date().isoformat()
            if day not in daily or candidate['created_at'] > daily[day]['created_at']: daily[day]=candidate
        except (ValueError,KeyError,TypeError,OSError):
            continue  # Exports without a matching successful restore proof are not offered.
    return sorted(daily.values(),key=lambda x:x['created_at'],reverse=True)[:KEEP_DAYS]


def serve(original, root=SOURCE, output=None):
    output=output or sys.stdout.buffer
    if original == 'inventory':
        output.write(json.dumps({'format':1,'archives':inventory(root)},sort_keys=True).encode())
        return
    parts=original.split(' ')
    if len(parts)!=2 or parts[0]!='fetch' or not PATTERN.fullmatch(parts[1]):
        raise ValueError('Only backup inventory and named archive fetch are allowed')
    path=root/parts[1]
    receipt(path)
    # Stream only this fixed whitelist: no shell, traversal, links, credentials or arbitrary files.
    with tarfile.open(fileobj=output,mode='w|') as tar:
        total=0
        for name in sorted(FILES):
            p=path/name
            fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
            with os.fdopen(fd,'rb') as f:
                st=os.fstat(f.fileno())
                import stat
                if not stat.S_ISREG(st.st_mode): raise ValueError('Not a regular backup file')
                total+=st.st_size
                if total>ARCHIVE_LIMIT: raise ValueError('Archive transfer exceeds bound')
                info=tarfile.TarInfo(name);info.size=st.st_size;info.mode=0o600
                tar.addfile(info,f)


def extract(archive, destination):
    seen=set();total=0
    with tarfile.open(archive,'r:') as tar:
        for item in tar:
            if item.name not in FILES or item.name in seen or not item.isfile():
                raise ValueError('Unexpected archive member or link')
            total+=item.size
            if total>ARCHIVE_LIMIT: raise ValueError('Transfer exceeds archive bound')
            seen.add(item.name)
            source=tar.extractfile(item)
            with (destination/item.name).open('xb') as f:
                os.chmod(destination/item.name,0o600)
                shutil.copyfileobj(source,f)
        if seen != FILES: raise ValueError('Transfer is incomplete')


def size(root):
    return sum(p.stat().st_size for d in root.glob('canonical-*') if d.is_dir() and not d.is_symlink()
               for p in d.iterdir() if p.is_file() and not p.is_symlink())


def prune(root, now=None):
    now=now or datetime.now(timezone.utc)
    completed=[]
    for path in root.glob('canonical-*'):
        try:
            manifest,_=receipt(path)
            marker=path/'copy-verified.json'
            if marker.is_symlink() or not marker.is_file(): continue
            proof=json.loads(marker.read_text())
            if proof.get('file_content_checksums_match') is not True: continue
            if proof.get('manifest_sha256') != hashlib.sha256((path/'manifest.json').read_bytes()).hexdigest(): continue
            completed.append((datetime.fromisoformat(manifest['created_at']),path))
        except (ValueError,KeyError,OSError): continue
    removed=[]
    for when,path in sorted(completed,reverse=True)[KEEP_MIN:]:
        if when < now-timedelta(days=KEEP_DAYS):
            shutil.rmtree(path);removed.append(path.name)
    return removed


def guard(root, reserve=RESERVE):
    if size(root)+reserve > CAP or shutil.disk_usage(root).free < FREE_FLOOR+reserve:
        raise RuntimeError('Copy storage cap/free-space floor reached; unexpired archives were preserved')


def ssh_args(state, operation):
    return ['/usr/bin/ssh','-T','-i',str(state/'ssh-key'),'-o','IdentitiesOnly=yes','-o','BatchMode=yes',
            '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=15',
            '-o','ServerAliveCountMax=2','-o','IdentityAgent=none','root@167.99.139.58',operation]


def transfer(state, operation, target=None):
    # Discard SSH stderr: status records a class/exit status, never raw remote diagnostics.
    if target is None:
        p=subprocess.run(ssh_args(state,operation),capture_output=True,timeout=30)
        if p.returncode: raise ConnectionError('Backup inventory SSH failed')
        if len(p.stdout)>1024*1024: raise ValueError('Inventory exceeds bound')
        return json.loads(p.stdout)
    # Read a bounded binary pipe. No download/credential bytes are logged.
    import selectors,time
    p=subprocess.Popen(ssh_args(state,operation),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    try:
        total=0;deadline=time.monotonic()+120
        with selectors.DefaultSelector() as selector,target.open('xb') as f:
            os.chmod(target,0o600)
            selector.register(p.stdout,selectors.EVENT_READ)
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0: raise TimeoutError('Backup transfer timed out')
                if not selector.select(min(remaining,5)): continue
                chunk=os.read(p.stdout.fileno(),1024*1024)
                if not chunk: break
                total+=len(chunk)
                if total>ARCHIVE_LIMIT: raise ValueError('Backup transfer exceeds bound')
                f.write(chunk)
            f.flush();os.fsync(f.fileno())
        if p.wait(timeout=15): raise ConnectionError('Backup archive SSH failed')
    finally:
        if p.poll() is None: p.kill();p.wait(timeout=15)
        p.stdout.close()


def copy(state):
    state=Path(state);root=state/'archives'
    if state.is_symlink() or root.is_symlink(): raise ValueError('Backup directories must not be symlinks')
    state.mkdir(mode=0o700,parents=True,exist_ok=True);state.chmod(0o700)
    root.mkdir(mode=0o700,exist_ok=True);root.chmod(0o700)
    for name in ('job.stdout.log','job.stderr.log'):
        log=state/name
        if log.exists() and not log.is_symlink() and log.stat().st_size>1024*1024:
            with log.open('w'): pass
            log.chmod(0o600)
    with (state/'copy.lock').open('a') as lock:
        os.chmod(state/'copy.lock',0o600)
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return {'status':'already_running'}
        start=stamp();previous={}
        if (state/'status.json').is_file(): previous=json.loads((state/'status.json').read_text())
        result={'started_at':start,'status':'running','last_success':previous.get('last_success'),
                'latest_copied':previous.get('latest_copied'),'latest_source_created_at':previous.get('latest_source_created_at'),'retention_days':KEEP_DAYS,'cap_bytes':CAP,'free_floor_bytes':FREE_FLOOR}
        write_json(state/'status.json',result)
        work=None;fetched=[]
        try:
            removed=prune(root)
            offered=transfer(state,'inventory')
            if offered.get('format')!=1 or not offered.get('archives'): raise ValueError('No verified recovery archive available')
            if len(offered['archives'])>KEEP_DAYS: raise ValueError('Inventory exceeds retention scope')
            # Newest first protects the recovery point before bounded historical catch-up.
            for item in offered['archives']:
                name=item['name']
                if not PATTERN.fullmatch(name): raise ValueError('Invalid remote archive name')
                target=root/name
                if target.exists():
                    # Reverify existing content. Do not overwrite a corrupted recovery point silently.
                    manifest,proof=validate_copy_existing(target)
                    if proof['manifest_sha256']!=item['manifest_sha256']: raise ValueError('Existing archive identity changed')
                    continue
                if len(fetched)>=7: break
                guard(root)
                work=Path(tempfile.mkdtemp(prefix='.partial-',dir=state));work.chmod(0o700)
                archive=work/'transfer.tar'
                transfer(state,'fetch '+name,archive)
                staged=work/name;staged.mkdir(mode=0o700)
                extract(archive,staged)
                manifest,proof=validate_copy(staged)
                if proof['manifest_sha256']!=item['manifest_sha256']: raise ValueError('Inventory/transfer identity mismatch')
                copied={'copied_at':stamp(),'manifest_sha256':proof['manifest_sha256'],'source_restored_at':proof['verified_at'],
                        'file_content_checksums_match':True,'counts':manifest['counts']}
                write_json(staged/'copy-verified.json',copied)
                if size(root)+sum(p.stat().st_size for p in staged.iterdir())>CAP or shutil.disk_usage(root).free<FREE_FLOOR:
                    raise RuntimeError('Copy exceeded storage policy')
                staged.rename(target);fetched.append(name)
                shutil.rmtree(work);work=None
            newest=offered['archives'][0]
            latest=root/newest['name'];manifest,proof=validate_copy_existing(latest)
            result.update(status='success',last_success=stamp(),latest_copied=newest['name'],
                latest_source_created_at=manifest['created_at'],latest_source_restored_at=proof['verified_at'],
                manifest_sha256=proof['manifest_sha256'],counts=manifest['counts'],fetched=fetched,
                expired_removed=removed,retained_bytes=size(root),free_bytes=shutil.disk_usage(root).free)
            write_json(state/'latest-copy.json',result)
            return result
        except Exception as exc:
            result.update(status='failed',failure_class=type(exc).__name__,fetched=fetched)
            raise
        finally:
            if work is not None: shutil.rmtree(work)
            result['finished_at']=stamp();write_json(state/'status.json',result)


def validate_copy_existing(path):
    marker=path/'copy-verified.json'
    # Validate the original whitelist without mutating a published archive.
    if marker.is_symlink() or not marker.is_file(): raise ValueError('Copy completion marker missing')
    manifest,proof=receipt(path)
    if {p.name for p in path.iterdir()}!=FILES|{'copy-verified.json'}: raise ValueError('Unexpected copied files')
    for name in PAYLOAD:
        p=path/name
        if p.is_symlink() or not p.is_file(): raise ValueError('Invalid copied file')
        with p.open('rb') as f: sha=hashlib.file_digest(f,'sha256').hexdigest()
        if sha!=manifest['files'][name]: raise ValueError('Existing copy checksum mismatch')
    if json.loads(marker.read_text()).get('manifest_sha256')!=proof['manifest_sha256']: raise ValueError('Invalid copy receipt')
    return manifest,proof


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('serve')
    c=sub.add_parser('copy');c.add_argument('--state-dir',type=Path,required=True)
    args=parser.parse_args();os.umask(0o077)
    try:
        if args.action=='serve': serve(os.environ.get('SSH_ORIGINAL_COMMAND',''))
        else: print(json.dumps(copy(args.state_dir),sort_keys=True))
        return 0
    except Exception as exc:
        # Especially for forced-command rejects, never echo an untrusted command.
        print(json.dumps({'status':'failed','failure_class':type(exc).__name__}),file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
