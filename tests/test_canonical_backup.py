import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts.canonical_backup import prune, storage_guard, roles_sql, validate_bundle, KEEP_MIN, MAX_BYTES, MIN_FREE, NAMES
from scripts.sync_supabase import digest
import gzip

class BackupSafetyTests(unittest.TestCase):
    def test_only_expired_complete_owned_archives_rotate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); now=datetime.now(timezone.utc)
            for i in range(20):
                p=root/f'canonical-{i:02d}';p.mkdir()
                (p/'manifest.json').write_text(json.dumps({'format':1,'complete':True,
                    'created_at':(now-timedelta(days=40-i)).isoformat()}))
            preserved=root/'rollback.dump';preserved.write_text('rollback')
            incomplete=root/'canonical-unfinished';incomplete.mkdir()
            unrelated=root/'supabase-before-private.json.gz';unrelated.write_text('protected')
            removed=prune(root,now)
            self.assertEqual(len(removed),20-KEEP_MIN)
            self.assertTrue(preserved.exists() and unrelated.exists() and incomplete.exists())
            self.assertEqual(len(prune(root,now)),0)

    def test_unexpired_archives_never_deleted_for_space(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);now=datetime.now(timezone.utc)
            for i in range(20):
                p=root/f'canonical-{i}';p.mkdir()
                (p/'manifest.json').write_text(json.dumps({'format':1,'complete':True,'created_at':now.isoformat()}))
            self.assertEqual(prune(root,now),[])
            with patch('scripts.canonical_backup.shutil.disk_usage') as usage:
                usage.return_value.free=MIN_FREE
                with self.assertRaises(RuntimeError):storage_guard(root)
            self.assertEqual(len(list(root.iterdir())),20)

    def test_storage_cap_fails_without_policy_deletions(self):
        with tempfile.TemporaryDirectory() as tmp, patch('scripts.canonical_backup.retained_size',return_value=MAX_BYTES):
            with self.assertRaises(RuntimeError):storage_guard(Path(tmp))

    def test_tampered_private_state_is_rejected_before_any_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            values={name:[] for name in NAMES}
            values['dealer_model_searches']=[{'id':'test','filters':{'fuel':'diesel'},'baseline':{'median':45000}}]
            checksums={name:digest(data) for name,data in values.items()}
            files={name:b'placeholder' for name in ['application.dump','catalog.json','roles.json','roles.sql','sequences.json']}
            files['snapshot.json.gz']=gzip.compress(json.dumps({'tables':values}).encode())
            for name,data in files.items():(root/name).write_bytes(data)
            manifest={'format':1,'complete':True,'counts':{name:len(data) for name,data in values.items()},
                'checksums':checksums,'files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
            (root/'manifest.json').write_text(json.dumps(manifest))
            self.assertEqual(validate_bundle(root)['counts']['dealer_model_searches'],1)
            values['dealer_model_searches'][0]['baseline']['median']=1
            damaged=gzip.compress(json.dumps({'tables':values}).encode())
            (root/'snapshot.json.gz').write_bytes(damaged)
            # Even a recalculated file digest cannot hide a changed original baseline.
            manifest['files']['snapshot.json.gz']=hashlib.sha256(damaged).hexdigest()
            (root/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'content checksum'):validate_bundle(root)

    def test_restore_roles_are_disabled_and_no_passwords(self):
        result=roles_sql({'roles':[{'rolname':'car_analyzer_app','rolinherit':False,
            'rolconnlimit':2,'rolconfig':['statement_timeout=20s']}],'memberships':[]})
        self.assertIn('NOLOGIN',result)
        self.assertIn('NOBYPASSRLS',result)
        self.assertNotIn('PASSWORD',result)
        self.assertIn("statement_timeout = '20s'",result)

if __name__=='__main__':unittest.main()
