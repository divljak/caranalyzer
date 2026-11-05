import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from scripts.github_collector_verification import (
    ConfigurationError,
    HOST,
    PROJECT,
    run_verification,
    write_credential,
)


class GitHubCollectorVerificationTests(unittest.TestCase):
    def test_credential_is_private_and_pinned_to_expected_role(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "collector.json"
            write_credential(path, "car_analyzer_collector", "test-only-password")
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertEqual(HOST, value["host"])
            self.assertEqual(5432, value["port"])
            self.assertEqual(f"car_analyzer_collector.{PROJECT}", value["user"])
            self.assertEqual("require", value["sslmode"])

    def test_wrapper_refuses_to_run_outside_github_actions(self):
        runner = Mock()
        with self.assertRaisesRegex(ConfigurationError, "only runs inside GitHub Actions"):
            run_verification("test-image", environ={}, runner=runner)
        runner.assert_not_called()

    def test_verification_uses_files_and_does_not_forward_password_env(self):
        seen = {}

        def runner(command, *, env, check):
            seen.update(command=command, env=env, check=check)
            mounts = [command[index + 1] for index, value in enumerate(command) if value == "--mount"]
            secret_mount = next(value for value in mounts if "dst=/run/car-analyzer-secrets" in value)
            secret_dir = Path(secret_mount.split(",")[1].removeprefix("src="))
            self.assertTrue((secret_dir / "collector.json").is_file())
            self.assertTrue((secret_dir / "backup.json").is_file())
            return SimpleNamespace(returncode=0)

        environ = {
            "GITHUB_ACTIONS": "true",
            "COLLECTOR_DB_PASSWORD": "collector-test-only",
            "BACKUP_DB_PASSWORD": "backup-test-only",
            "PATH": os.environ.get("PATH", ""),
        }
        self.assertEqual(0, run_verification("car-analyzer:test", environ=environ, runner=runner))
        rendered = " ".join(seen["command"])
        self.assertIn("--verify-only", rendered)
        user_index = seen["command"].index("--user")
        self.assertEqual(f"{os.getuid()}:{os.getgid()}", seen["command"][user_index + 1])
        self.assertNotIn("collector-test-only", rendered)
        self.assertNotIn("backup-test-only", rendered)
        self.assertNotIn("COLLECTOR_DB_PASSWORD", seen["env"])
        self.assertNotIn("BACKUP_DB_PASSWORD", seen["env"])
        self.assertFalse(seen["check"])
