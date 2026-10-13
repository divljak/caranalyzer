#!/usr/bin/env bash
set -euo pipefail
umask 077
cd /opt/car-analyzer
[[ "$EUID" == 0 ]] || { echo 'Run on the collector VPS as root.'; exit 1; }
release="$(readlink -f "$1")"
[[ "$release" == /opt/car-analyzer/.daily-delivery/releases/* && -f "$release/scripts/direct_collector.py" ]] || exit 1
exec 9>.daily-delivery/pipeline.lock
flock -n 9 || { echo 'A collection/delivery is active; installation stopped.'; exit 1; }
state="$(systemctl show car-analyzer-collector.service -p ActiveState --value)"
[[ "$state" != activating && "$state" != active && "$state" != deactivating ]] || exit 1
[[ -z "$(docker ps -q --filter label=com.docker.compose.service=collector)" ]] || exit 1
for name in collector backup; do
  [[ -r ".daily-delivery/$name-secret.json" ]] || exit 1
  chmod 600 ".daily-delivery/$name-secret.json"
done
if [[ ! -e .daily-delivery/direct-active ]]; then
  # Last source snapshot reconciles evidence, including budgets, before direct writes.
  scripts/backup-postgres.sh
  docker compose run --rm --no-deps \
    -v /opt/car-analyzer/scripts/sync_supabase.py:/app/scripts/sync_supabase.py:ro \
    -v /opt/car-analyzer/market_labels.py:/app/market_labels.py:ro \
    -v /opt/car-analyzer/.daily-delivery:/delivery \
    collector python scripts/sync_supabase.py --credentials /delivery/sync-secret.json --state-dir /delivery
  cp /etc/systemd/system/car-analyzer-collector.service.d/delivery.conf .daily-delivery/pre-direct-delivery.conf
  docker image inspect car-analyzer-collector:latest --format '{{.Id}}' > .daily-delivery/direct-image
fi
install -m 700 "$release/scripts/run-direct-container.sh" scripts/run-direct-container.sh.new
mv scripts/run-direct-container.sh.new scripts/run-direct-container.sh
scripts/run-direct-container.sh "$release" --verify-only
systemctl stop car-analyzer-collector.timer
ln -s "$release" .daily-delivery/direct-current.new
mv -Tf .daily-delivery/direct-current.new .daily-delivery/direct-current
install -m 700 "$release/scripts/daily-direct-collection.sh" scripts/daily-direct-collection.sh.new
mv scripts/daily-direct-collection.sh.new scripts/daily-direct-collection.sh
# This sentinel makes the old entry point fail closed even if invoked manually.
touch .daily-delivery/direct-active
install -m 644 "$release/deploy/systemd/car-analyzer-direct.conf" /etc/systemd/system/car-analyzer-collector.service.d/delivery.conf.new
mv /etc/systemd/system/car-analyzer-collector.service.d/delivery.conf.new /etc/systemd/system/car-analyzer-collector.service.d/delivery.conf
systemctl daemon-reload
systemctl enable --now car-analyzer-collector.timer
systemctl list-timers car-analyzer-collector.timer --no-pager
