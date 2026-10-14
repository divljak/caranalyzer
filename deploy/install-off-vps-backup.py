"""Install the private Mac copier and a restricted archive-only SSH key on the existing VPS."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

SOURCE=Path(__file__).resolve().parents[1]
STATE=Path.home()/'Library/Application Support/CarAnalyzerBackups'
REMOTE='/opt/car-analyzer/.daily-delivery/backup-tools/off_vps_backup.py'
LABEL='com.caranalyzer.backup-copy'
PLIST=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'


def run(args,**kw):
    result=subprocess.run(args,capture_output=True,timeout=90,**kw)
    if result.returncode: raise RuntimeError('Off-VPS installation command failed')
    return result.stdout


def main():
    os.umask(0o077)
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True);STATE.chmod(0o700)
    if STATE.is_symlink(): raise ValueError('Backup destination must not be a symlink')
    script=STATE/'off_vps_backup.py'
    temporary=STATE/'off_vps_backup.py.new';temporary.write_bytes((SOURCE/'scripts/off_vps_backup.py').read_bytes());temporary.chmod(0o600);temporary.replace(script)
    tools=STATE/'recovery-tools'
    (tools/'scripts').mkdir(mode=0o700,parents=True,exist_ok=True)
    for name in ('canonical_backup.py','sync_supabase.py'):
        path=tools/'scripts'/name;path.write_bytes((SOURCE/'scripts'/name).read_bytes());path.chmod(0o600)
    for origin,target in [('market_labels.py','market_labels.py'),('deploy/backup/requirements.txt','requirements.txt'),('docs/CANONICAL_BACKUPS.md','RECOVERY.md')]:
        path=tools/target;path.write_bytes((SOURCE/origin).read_bytes());path.chmod(0o600)
    if not (tools/'venv/bin/python').exists():
        run([sys.executable,'-m','venv',str(tools/'venv')])
    run([str(tools/'venv/bin/python'),'-m','pip','install','--no-cache-dir','--disable-pip-version-check','-r',str(tools/'requirements.txt')])
    key=STATE/'ssh-key'
    if not key.exists():
        run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-C','caranalyzer-backup-copy','-f',str(key)])
    key.chmod(0o600)
    run(['/usr/bin/scp','-q','-o','BatchMode=yes',str(script),'root@167.99.139.58:'+REMOTE+'.new'])
    run(['/usr/bin/ssh','-T','-o','BatchMode=yes','root@167.99.139.58',
         'chmod 600 '+REMOTE+'.new && mv '+REMOTE+'.new '+REMOTE])
    pub=Path(str(key)+'.pub').read_text().strip()
    if not pub.startswith('ssh-ed25519 ') or '\n' in pub: raise ValueError('Invalid copy public key')
    line='restrict,command="/usr/bin/python3 '+REMOTE+' serve" '+pub+'\n'
    # Append only the scoped public key; preserve all existing authorized access.
    append="""import os,sys
from pathlib import Path
p=Path('/root/.ssh/authorized_keys')
line=sys.stdin.buffer.read(4096).decode()
if not line.startswith('restrict,command="/usr/bin/python3 /opt/car-analyzer/.daily-delivery/backup-tools/off_vps_backup.py serve" ssh-ed25519 '): raise ValueError('Invalid scoped key')
current=p.read_text()
if line.strip() not in current.splitlines():
 with p.open('a') as f:
  if current and not current.endswith('\\n'):f.write('\\n')
  f.write(line);f.flush();os.fsync(f.fileno())
p.chmod(0o600)
"""
    import shlex
    run(['/usr/bin/ssh','-T','-o','BatchMode=yes','root@167.99.139.58','/usr/bin/python3 -c '+shlex.quote(append)],input=line.encode())
    data={'Label':LABEL,'ProgramArguments':[sys.executable,str(script),'copy','--state-dir',str(STATE)],
          'RunAtLoad':True,'StartCalendarInterval':{'Minute':45},'ProcessType':'Background',
          'LimitLoadToSessionType':'Aqua','StandardOutPath':str(STATE/'job.stdout.log'),
          'StandardErrorPath':str(STATE/'job.stderr.log')}
    # Safe fixed-size status logs only; cap job output via the module's bounded result.
    for name in ('job.stdout.log','job.stderr.log'):
        path=STATE/name;path.touch(mode=0o600,exist_ok=True);path.chmod(0o600)
    PLIST.parent.mkdir(parents=True,exist_ok=True)
    tmp=PLIST.with_suffix('.plist.new');tmp.write_bytes(plistlib.dumps(data));tmp.chmod(0o600);tmp.replace(PLIST)
    domain='gui/'+str(os.getuid())
    # Reinstallation preserves archives/keys; replace only this label's job.
    subprocess.run(['/bin/launchctl','bootout',domain+'/'+LABEL],capture_output=True,timeout=30)
    run(['/bin/launchctl','bootstrap',domain,str(PLIST)])
    installation={'label':LABEL,'state_dir':str(STATE),'plist':str(PLIST),'script_sha256':hashlib.sha256(script.read_bytes()).hexdigest(),
                  'hourly_minute':45,'login_catchup':True,'restricted_remote_key':True}
    (STATE/'installation.json').write_text(json.dumps(installation,sort_keys=True));(STATE/'installation.json').chmod(0o600)
    print(json.dumps(installation,sort_keys=True))

if __name__=='__main__':main()
