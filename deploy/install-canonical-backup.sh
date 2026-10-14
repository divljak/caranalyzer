#!/usr/bin/env bash
set -euo pipefail
umask 077
app=/opt/car-analyzer
state="$app/.daily-delivery"
source_dir="$(readlink -f "$1")"
[[ "$EUID" == 0 && -f "$source_dir/scripts/canonical_backup.py" ]] || exit 1
[[ -f "$state/direct-active" && -r "$state/backup-secret.json" ]] || exit 1
exec 9>"$state/pipeline.lock"
flock -n 9 || { echo 'Collection is active; installation deferred.'; exit 1; }
service_state="$(systemctl show car-analyzer-collector.service -p ActiveState --value)"
[[ "$service_state" != active && "$service_state" != activating && "$service_state" != deactivating ]] || exit 1
[[ -z "$(docker ps -q --filter name=car-analyzer-direct-collector)" ]] || exit 1
mkdir -p "$state/backup-tools/scripts" "$state/canonical-backups"
chmod 700 "$state/backup-tools" "$state/canonical-backups"
# Use the existing Python/pip without changing the OS package dependency tree.
python3 -m venv --without-pip "$state/backup-tools/venv"
pip3 --python "$state/backup-tools/venv/bin/python" install --disable-pip-version-check -r "$source_dir/deploy/backup/requirements.txt"
for name in canonical_backup.py sync_supabase.py; do
  install -m 600 "$source_dir/scripts/$name" "$state/backup-tools/scripts/$name.new"
  mv "$state/backup-tools/scripts/$name.new" "$state/backup-tools/scripts/$name"
done
install -m 600 "$source_dir/market_labels.py" "$state/backup-tools/market_labels.py"
install -m 700 "$source_dir/deploy/backup/run-canonical-backup.sh" "$state/backup-tools/run-canonical-backup.sh"
# SQL grants are administrator-provisioned separately; never load admin credentials here.
"$state/backup-tools/venv/bin/python" "$state/backup-tools/scripts/canonical_backup.py" export \
  --credentials "$state/backup-secret.json" --directory "$state/canonical-backups" --stage manual
install -m 644 "$source_dir/deploy/backup/canonical-backup.conf" /etc/systemd/system/car-analyzer-collector.service.d/zz-canonical-backup.conf.new
mv /etc/systemd/system/car-analyzer-collector.service.d/zz-canonical-backup.conf.new /etc/systemd/system/car-analyzer-collector.service.d/zz-canonical-backup.conf
systemctl daemon-reload
systemctl is-enabled car-analyzer-collector.timer
# Never trigger collection or clear its partial/failed result during backup installation.
