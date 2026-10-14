"""Fail-closed connections, overlap/lost-lock handling, honest errors and backups."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from database.collector_connection import credential_url, LockedSession
from database.models import get_database_url, create_tables
from scripts.direct_collector import preflight, pinned_collection
from scripts.backup_supabase import backup
from scripts.verify_delivery_backup import verify


class ConnectionTests(unittest.TestCase):
    def test_direct_mode_never_falls_back_to_local_or_url(self):
        with patch.dict(os.environ, {'CAR_ANALYZER_COLLECTOR':'supabase','DATABASE_URL':'postgresql://localhost/local'}, clear=True):
            with self.assertRaises(KeyError):
                get_database_url()
            with self.assertRaisesRegex(RuntimeError, 'cannot create'):
                create_tables()

    def test_transaction_pooler_and_world_readable_secret_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'secret.json'
            data=dict(host='aws-0-eu-north-1.pooler.supabase.com',port=6543,dbname='postgres',
                      user='car_analyzer_collector.ojmokmsjppkqwppmrzpv',password='test-only',sslmode='require')
            p.write_text(json.dumps(data));p.chmod(0o600)
            with self.assertRaisesRegex(ValueError,'Unexpected'):
                credential_url(p,'car_analyzer_collector')
            data['port']=5432;p.write_text(json.dumps(data));p.chmod(0o644)
            with self.assertRaisesRegex(ValueError,'private'):
                credential_url(p,'car_analyzer_collector')

    def test_preflight_is_bounded_without_replaying_writes(self):
        failure=OperationalError('select',{},Exception())
        sleep=Mock()
        with patch('scripts.direct_collector.verify_connection',side_effect=failure) as connect:
            with self.assertRaises(OperationalError):
                preflight(Mock(),'car_analyzer_collector',sleep)
            self.assertEqual(3,connect.call_count)
            self.assertEqual([15,30],[c.args[0] for c in sleep.call_args_list])

    def test_lost_advisory_connection_cannot_reconnect(self):
        connection=Mock(closed=False,invalidated=True)
        with self.assertRaisesRegex(RuntimeError,'lock connection was lost'):
            LockedSession(bind=connection)


@unittest.skipUnless(os.getenv('DEALER_TEST_DATABASE_URL'),'Requires loopback audit PostgreSQL')
class LiveLockTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine(os.environ['DEALER_TEST_DATABASE_URL'])
        self.addCleanup(self.engine.dispose)

    def test_lock_survives_commits_and_rejects_retired_sync(self):
        from types import SimpleNamespace
        db=SimpleNamespace(engine=self.engine,SessionLocal=sessionmaker(bind=self.engine))
        store=SimpleNamespace(sessions=sessionmaker(bind=self.engine))
        with pinned_collection(db,store) as c:
            with store.sessions.begin() as s:
                s.execute(text('select 1'))
            with self.engine.begin() as other:
                self.assertFalse(other.execute(text('select pg_try_advisory_xact_lock(731400220)')).scalar())
            self.assertFalse(c.in_transaction())
            with db.SessionLocal() as s:
                self.assertEqual(c.execute(text('select pg_backend_pid()')).scalar(),s.execute(text('select pg_backend_pid()')).scalar())
            c.rollback()
        with self.engine.begin() as other:
            self.assertTrue(other.execute(text('select pg_try_advisory_xact_lock(731400220)')).scalar())
