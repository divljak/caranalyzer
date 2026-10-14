#!/usr/bin/env bash
set -euo pipefail
umask 077
app=/opt/car-analyzer
state="$app/.daily-delivery"
exec 8>"$state/canonical-backup.lock"
flock -n 8 || { echo 'Canonical backup already running.'; exit 1; }
exec 9>"$state/pipeline.lock"
flock -n 9 || { echo 'Collection pipeline is active; backup deferred.'; exit 1; }
"$state/backup-tools/venv/bin/python" "$state/backup-tools/scripts/canonical_backup.py" export \
  --credentials "$state/backup-secret.json" --directory "$state/canonical-backups" --stage "$1"
# Every post-attempt archive is also restored into a network-disabled tmpfs DB.
if [[ "$1" == after ]]; then
  bundle="$("$state/backup-tools/venv/bin/python" - "$state/canonical-backups/latest.json" <<'PYTHON'
import json,sys
print(json.load(open(sys.argv[1]))['path'])
PYTHON
)"
  "$state/backup-tools/venv/bin/python" "$state/backup-tools/scripts/canonical_backup.py" verify-restore "$bundle"
fi
