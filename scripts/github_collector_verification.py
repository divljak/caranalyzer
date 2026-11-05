#!/usr/bin/env python3
"""Run the direct collector's no-source verification in an ephemeral container."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


PROJECT = "ojmokmsjppkqwppmrzpv"
HOST = "aws-0-eu-north-1.pooler.supabase.com"
SECRET_NAMES = ("COLLECTOR_DB_PASSWORD", "BACKUP_DB_PASSWORD")


class ConfigurationError(RuntimeError):
    """Safe configuration failure that never includes a credential value."""


def _password(environ, name):
    value = environ.get(name)
    if not value:
        raise ConfigurationError(f"Required GitHub secret is missing: {name}")
    return value


def write_credential(path, role, password):
    if role not in {"car_analyzer_collector", "car_analyzer_backup"}:
        raise ConfigurationError("Unexpected database role")
    payload = {
        "host": HOST,
        "port": 5432,
        "dbname": "postgres",
        "user": f"{role}.{PROJECT}",
        "password": password,
        "sslmode": "require",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def run_verification(image, environ=None, runner=subprocess.run):
    environ = dict(os.environ if environ is None else environ)
    if environ.get("GITHUB_ACTIONS") != "true":
        raise ConfigurationError("This wrapper only runs inside GitHub Actions")
    if not image or image.isspace():
        raise ConfigurationError("Collector image name is missing")

    collector_password = _password(environ, "COLLECTOR_DB_PASSWORD")
    backup_password = _password(environ, "BACKUP_DB_PASSWORD")
    root = Path(tempfile.mkdtemp(prefix="car-analyzer-verification-"))
    root.chmod(0o700)
    secrets_dir = root / "secrets"
    state_dir = root / "state"
    secrets_dir.mkdir(mode=0o700)
    state_dir.mkdir(mode=0o700)
    collector_path = secrets_dir / "collector.json"
    backup_path = secrets_dir / "backup.json"

    try:
        write_credential(collector_path, "car_analyzer_collector", collector_password)
        write_credential(backup_path, "car_analyzer_backup", backup_password)

        child_env = dict(environ)
        for name in SECRET_NAMES:
            child_env.pop(name, None)

        command = [
            "docker", "run", "--rm", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--mount", f"type=bind,src={secrets_dir},dst=/run/car-analyzer-secrets,readonly",
            "--mount", f"type=bind,src={state_dir},dst=/state",
            image,
            "--credentials", "/run/car-analyzer-secrets/collector.json",
            "--backup-credentials", "/run/car-analyzer-secrets/backup.json",
            "--state-dir", "/state",
            "--verify-only",
        ]
        completed = runner(command, env=child_env, check=False)
        return completed.returncode
    finally:
        shutil.rmtree(root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    args = parser.parse_args(argv)
    try:
        return run_verification(args.image)
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
