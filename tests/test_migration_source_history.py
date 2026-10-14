"""Keep unrecorded historical grants out of the default production migration queue."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.bootstrap_integration_test_database import bootstrap

ROOT = Path(__file__).resolve().parents[1]


class MigrationSourceTests(unittest.TestCase):
    def test_queue_is_exactly_recorded_history_and_matches_reviewed_hashes(self):
        history = json.loads((ROOT / 'supabase/applied-history.json').read_text())
        files = {p.relative_to(ROOT).as_posix() for p in (ROOT / 'supabase/migrations').glob('*.sql')}
        self.assertEqual(files, {entry['file'] for entry in history['applied']})
        self.assertEqual(['20261013132620', '20261013133615', '20261013174917', '20261014155031'],
                         [entry['version'] for entry in history['applied']])
        for entry in history['applied']:
            self.assertEqual(entry['source_sha256'], hashlib.sha256((ROOT / entry['file']).read_bytes()).hexdigest())
        self.assertEqual(2, len(history['manual_history']))
        for path in history['manual_history']:
            self.assertTrue((ROOT / path).exists())
            self.assertNotIn(path, files)

    def test_bootstrap_rejects_a_canonical_database_before_connecting(self):
        with patch('scripts.bootstrap_integration_test_database.create_engine') as create:
            with self.assertRaises(ValueError):
                bootstrap(database='postgres')
            create.assert_not_called()
