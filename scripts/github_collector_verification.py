#!/usr/bin/env python3
"""Run the direct collector safely from an ephemeral GitHub Actions container."""

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
DAILY_WORKFLOW = "/.github/workflows/daily-collect.yml@"
# Public-repo artifact: counts, timings and source URLs only. No credentials or backup content.
DIAGNOSTIC_KEYS = ("started_at", "finished_at", "exit_code", "failure_class", "sqlstate",
                   "connection", "backup_verified", "verify_only", "collection")


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


def encrypt_latest_backup(state_dir, output, recipient_file, *, environ, runner=subprocess.run):
    snapshots = list(state_dir.glob("supabase-canonical-*-after.json.gz"))
    if len(snapshots) != 1:
        raise ConfigurationError("Expected exactly one verified after snapshot")
    if not recipient_file.is_file():
        raise ConfigurationError("Backup recipient file is missing")
    if output.exists():
        raise ConfigurationError("Encrypted backup output already exists")

    completed = runner([
        "age", "--encrypt", "--recipients-file", str(recipient_file),
        "--output", str(output), str(snapshots[0]),
    ], env=environ, check=False)
    if completed.returncode:
        return completed.returncode
    if not output.is_file() or output.stat().st_size == 0:
        raise ConfigurationError("Encrypted backup output was not created")
    output.chmod(0o600)
    return 0


BUDGET_SETTINGS = ("COLLECTOR_SEARCH_PAGES_PER_RUN", "COLLECTOR_SEARCH_DAY_CAP",
                   "COLLECTOR_DETAIL_DAY_CAP", "COLLECTOR_DETAIL_MORNING")


def budget_settings(environ):
    """COLLECTOR_SLOT and the budget integers to forward into the container; anything malformed is an error."""
    out = {}
    slot = (environ.get("COLLECTOR_SLOT") or "").strip().lower()
    if slot:
        if slot not in {"auto", "morning", "evening"}:
            raise ConfigurationError("COLLECTOR_SLOT must be auto, morning or evening")
        out["COLLECTOR_SLOT"] = slot
    for name in BUDGET_SETTINGS:
        value = (environ.get(name) or "").strip()
        if value:
            if not value.isdigit():
                raise ConfigurationError(f"{name} must be a whole number")
            out[name] = value
    return out


def collection_authorized(environ):
    """A real collection needs a confirmed manual shadow run or the daily workflow itself."""
    event = environ.get("GITHUB_EVENT_NAME")
    shadow = event == "workflow_dispatch" and environ.get("COLLECTOR_SHADOW_CONFIRMATION") == "COLLECT_ONCE"
    daily = (environ.get("COLLECTOR_RUN_MODE") == "daily" and event in {"workflow_dispatch", "schedule"}
             and DAILY_WORKFLOW in environ.get("GITHUB_WORKFLOW_REF", ""))
    return shadow or daily


def write_diagnostics(state_dir, output, collect):
    marker = "latest-direct-attempt.json" if collect else "latest-direct-verification.json"
    source = state_dir / marker
    try:
        result = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result = {"failure_class": "CollectorResultMissing"}
    summary = {key: result[key] for key in DIAGNOSTIC_KEYS if key in result}
    for stage in ("before", "after"):
        if isinstance(result.get(stage), dict) and "counts" in result[stage]:
            summary[f"{stage}_counts"] = result[stage]["counts"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return summary


def run_verification(image, environ=None, runner=subprocess.run, *, encrypted_backup=None,
                     recipient_file=None, collect=False, diagnostics=None):
    environ = dict(os.environ if environ is None else environ)
    if environ.get("GITHUB_ACTIONS") != "true":
        raise ConfigurationError("This wrapper only runs inside GitHub Actions")
    if not image or image.isspace():
        raise ConfigurationError("Collector image name is missing")
    if collect and not collection_authorized(environ):
        raise ConfigurationError(
            "Collection requires the daily workflow, or a manual dispatch with COLLECT_ONCE confirmation")
    if bool(encrypted_backup) != bool(recipient_file):
        raise ConfigurationError("Encrypted backup and recipient file must be provided together")
    encrypted_backup = Path(encrypted_backup) if encrypted_backup else None
    recipient_file = Path(recipient_file) if recipient_file else None

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

        # Run slot and budget settings are plain integers/words, not secrets; validated before they reach the container.
        budget_env = budget_settings(environ)
        command = [
            "docker", "run", "--rm", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user", f"{os.getuid()}:{os.getgid()}",
            # Chromium needs a writable HOME and room for its profile in /tmp.
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=512m", "--env", "HOME=/tmp",
            *[arg for name, value in budget_env.items() for arg in ("--env", f"{name}={value}")],
            "--mount", f"type=bind,src={secrets_dir},dst=/run/car-analyzer-secrets,readonly",
            "--mount", f"type=bind,src={state_dir},dst=/state",
            image,
            "--credentials", "/run/car-analyzer-secrets/collector.json",
            "--backup-credentials", "/run/car-analyzer-secrets/backup.json",
            "--state-dir", "/state",
        ]
        if not collect:
            command.append("--verify-only")
        completed = runner(command, env=child_env, check=False)
        if diagnostics is not None:
            write_diagnostics(state_dir, Path(diagnostics), collect)
        if encrypted_backup is None:
            return completed.returncode
        try:
            encryption_status = encrypt_latest_backup(
                state_dir, encrypted_backup, recipient_file, environ=child_env, runner=runner)
        except ConfigurationError:
            if completed.returncode:
                return completed.returncode
            raise
        if completed.returncode:
            return completed.returncode
        return encryption_status
    finally:
        shutil.rmtree(root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--encrypted-backup", type=Path)
    parser.add_argument("--recipient-file", type=Path)
    parser.add_argument("--diagnostics", type=Path,
                        help="Write a small non-secret run summary (counts, timings, failures) here")
    parser.add_argument(
        "--collect", action="store_true",
        help="Run one manually confirmed source collection instead of no-source verification",
    )
    args = parser.parse_args(argv)
    try:
        return run_verification(
            args.image,
            encrypted_backup=args.encrypted_backup,
            recipient_file=args.recipient_file,
            collect=args.collect,
            diagnostics=args.diagnostics,
        )
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
