import copy
from datetime import datetime,timedelta,timezone
import gzip,hashlib,io,json
from pathlib import Path
import tarfile,tempfile,unittest
from unittest.mock import patch
from scripts import off_vps_backup as m


def fixture(root,day=0,now=None):
    now=(now or datetime.now(timezone.utc))-timedelta(days=day)
    path=root/('canonical-'+now.strftime('%Y%m%dT%H%M%S%f')+'-after');path.mkdir()
    values={name:[] for name in m.TABLES}
    values['dealer_model_searches']=[{'id':'test-private','filters':{'fuel':'diesel'},'baseline':{'median':45000}}]
    content={name:b'fixture' for name in m.PAYLOAD}
    content['snapshot.json.gz']=gzip.compress(json.dumps({'format':1,'tables':values}).encode())
    for name,data in content.items():(path/name).write_bytes(data)
    manifest={'format':1,'complete':True,'created_at':now.isoformat(),'counts':{name:len(v) for name,v in values.items()},
              'checksums':{name:m.digest(v) for name,v in values.items()},'files':{name:hashlib.sha256(v).hexdigest() for name,v in content.items()}}
    m.write_json(path/'manifest.json',manifest)
    proof={'bundle':str(path),'verified_at':now.isoformat(),'manifest_sha256':hashlib.sha256((path/'manifest.json').read_bytes()).hexdigest(),
           'counts':manifest['counts'],'content_checksums_match':True,'schema_access_match':True,'sequence_state_match':True,'scratch_removed':True}
    m.write_json(path/'restore-verified.json',proof)
    return path

class OffVpsTests(unittest.TestCase):
    def test_forced_endpoint_rejects_shell_and_credential_paths(self):
        for cmd in ['cat /etc/passwd','fetch ../backup-secret.json','fetch canonical-20260101T010101000000-after;id','inventory;cat secret','fetch /etc/passwd']:
            with self.assertRaises(ValueError):m.serve(cmd,Path('/unused'),io.BytesIO())

    def test_wrong_or_failed_restore_is_never_offered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=fixture(root)
            proof=json.loads((path/'restore-verified.json').read_text());proof['schema_access_match']=False
            m.write_json(path/'restore-verified.json',proof)
            self.assertEqual(m.inventory(root),[])
            proof['schema_access_match']=True;proof['manifest_sha256']='wrong'
            m.write_json(path/'restore-verified.json',proof)
            self.assertEqual(m.inventory(root),[])

    def test_transfer_preserves_nonempty_private_baseline_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=fixture(root);buffer=io.BytesIO()
            m.serve('fetch '+source.name,root,buffer)
            tar=root/'transfer.tar';tar.write_bytes(buffer.getvalue());dest=root/'copy'/source.name;dest.mkdir(parents=True)
            m.extract(tar,dest);manifest,_=m.validate_copy(dest)
            self.assertEqual(manifest['counts']['dealer_model_searches'],1)
            (dest/'snapshot.json.gz').write_bytes(b'damaged')
            with self.assertRaisesRegex(ValueError,'checksum'):m.validate_copy(dest)

    def test_extraction_rejects_links_and_extra_credentials(self):
        for name,kind in [('backup-secret.json',tarfile.REGTYPE),('application.dump',tarfile.SYMTYPE),('../roles.sql',tarfile.REGTYPE)]:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);archive=root/'bad.tar'
                with tarfile.open(archive,'w') as t:
                    info=tarfile.TarInfo(name);info.type=kind;info.linkname='/etc/passwd';t.addfile(info)
                dest=root/'out';dest.mkdir()
                with self.assertRaises(ValueError):m.extract(archive,dest)

    def test_expiry_preserves_minimum_copies_and_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);now=datetime.now(timezone.utc)
            for day in range(95,115):
                path=fixture(root,day,now);_,proof=m.receipt(path)
                m.write_json(path/'copy-verified.json',{'manifest_sha256':proof['manifest_sha256'],'file_content_checksums_match':True})
            rollback=root/'rollback.tar.gz';rollback.write_bytes(b'retained')
            self.assertEqual(len(m.prune(root,now)),6)
            self.assertTrue(rollback.exists());self.assertEqual(len(list(root.glob('canonical-*'))),14)

    def test_cap_failure_preserves_unexpired_copies(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(m,'size',return_value=m.CAP):
            root=Path(tmp);path=fixture(root)
            with self.assertRaises(RuntimeError):m.guard(root)
            self.assertTrue(path.exists())

    def test_free_space_floor_preserves_existing_copy(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(m.shutil,'disk_usage') as usage:
            root=Path(tmp);path=fixture(root);usage.return_value.free=m.FREE_FLOOR
            with self.assertRaises(RuntimeError):m.guard(root)
            self.assertTrue(path.exists())

    def test_connection_failure_preserves_last_success_and_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp);archives=state/'archives';archives.mkdir();path=fixture(archives)
            m.write_json(state/'status.json',{'last_success':'previous','latest_copied':path.name})
            with patch.object(m,'transfer',side_effect=ConnectionError):
                with self.assertRaises(ConnectionError):m.copy(state)
            status=json.loads((state/'status.json').read_text())
            self.assertEqual(status['status'],'failed');self.assertEqual(status['last_success'],'previous');self.assertTrue(path.exists())

if __name__=='__main__':unittest.main()
