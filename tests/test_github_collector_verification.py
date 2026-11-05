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
    encrypt_latest_backup,
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

    def test_collection_requires_manual_dispatch_and_exact_confirmation(self):
        environ = {
            "GITHUB_ACTIONS": "true",
            "COLLECTOR_DB_PASSWORD": "collector-test-only",
            "BACKUP_DB_PASSWORD": "backup-test-only",
        }
        runner = Mock()
        with self.assertRaisesRegex(ConfigurationError, "COLLECT_ONCE"):
            run_verification("test-image", environ=environ, runner=runner, collect=True)
        runner.assert_not_called()

    def test_confirmed_collection_omits_verify_only(self):
        seen = {}

        def runner(command, *, env, check):
            seen.update(command=command, env=env, check=check)
            return SimpleNamespace(returncode=0)

        environ = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "COLLECTOR_SHADOW_CONFIRMATION": "COLLECT_ONCE",
            "COLLECTOR_DB_PASSWORD": "collector-test-only",
            "BACKUP_DB_PASSWORD": "backup-test-only",
            "PATH": os.environ.get("PATH", ""),
        }
        self.assertEqual(0, run_verification(
            "car-analyzer:test", environ=environ, runner=runner, collect=True))
        self.assertNotIn("--verify-only", seen["command"])
        self.assertNotIn("COLLECTOR_DB_PASSWORD", seen["env"])
        self.assertNotIn("BACKUP_DB_PASSWORD", seen["env"])

    def test_failed_collection_still_encrypts_completed_after_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recipient = root / "recipient.pub"
            recipient.write_text("ssh-ed25519 test-only\n", encoding="utf-8")
            output = root / "backup.age"
            calls = []

            def runner(command, *, env, check):
                calls.append(command)
                if command[0] == "docker":
                    state_mount = next(
                        command[index + 1] for index, value in enumerate(command)
                        if value == "--mount" and "dst=/state" in command[index + 1])
                    state = Path(state_mount.split(",")[1].removeprefix("src="))
                    (state / "supabase-canonical-20261105-after.json.gz").write_bytes(
                        b"test-only-snapshot")
                    return SimpleNamespace(returncode=1)
                Path(command[command.index("--output") + 1]).write_bytes(
                    b"encrypted-test-only")
                return SimpleNamespace(returncode=0)

            environ = {
                "GITHUB_ACTIONS": "true",
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "COLLECTOR_SHADOW_CONFIRMATION": "COLLECT_ONCE",
                "COLLECTOR_DB_PASSWORD": "collector-test-only",
                "BACKUP_DB_PASSWORD": "backup-test-only",
            }
            self.assertEqual(1, run_verification(
                "car-analyzer:test", environ=environ, runner=runner,
                encrypted_backup=output, recipient_file=recipient, collect=True))
            self.assertTrue(output.is_file())
            self.assertEqual("docker", calls[0][0])
            self.assertEqual("age", calls[1][0])

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

    def test_verified_after_snapshot_is_encrypted_without_private_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            state.mkdir()
            snapshot = state / "supabase-canonical-20261105-after.json.gz"
            snapshot.write_bytes(b"test-only-snapshot")
            recipient = root / "recipient.pub"
            recipient.write_text("ssh-ed25519 test-only\n", encoding="utf-8")
            output = root / "backup.age"
            seen = {}

            def runner(command, *, env, check):
                seen.update(command=command, env=env, check=check)
                Path(command[command.index("--output") + 1]).write_bytes(b"encrypted-test-only")
                return SimpleNamespace(returncode=0)

            self.assertEqual(0, encrypt_latest_backup(
                state, output, recipient, environ={"PATH": "/test"}, runner=runner))
            self.assertEqual(0o600, output.stat().st_mode & 0o777)
            self.assertEqual("age", seen["command"][0])
            self.assertIn(str(recipient), seen["command"])
            self.assertIn(str(snapshot), seen["command"])
            self.assertFalse(seen["check"])
