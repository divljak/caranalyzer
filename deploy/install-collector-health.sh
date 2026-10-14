#!/usr/bin/env bash
# Separate read-only schedule. Does not edit/start the collector or reset its state.
set -euo pipefail
umask 077
cd /opt/car-analyzer
[[ $EUID -eq 0 ]] || { echo 'Install health status as root.'; exit 1; }
release="$(readlink -f .daily-delivery/direct-current)"
[[ "$release" == /opt/car-analyzer/.daily-delivery/releases/* ]] || exit 1
[[ -f "$release/database/collector_connection.py" && -f .daily-delivery/backup-secret.json ]] || exit 1
[[ -f scripts/collector_health.py ]] || exit 1
install -d -m 700 .collector-health
# Refuse changing an executing checker. Upload its script with temporary-name + rename.
[[ "$(systemctl is-active car-analyzer-health.service || true)" != activating ]] || exit 1
install -m 644 deploy/health/car-analyzer-health.service /etc/systemd/system/car-analyzer-health.service
install -m 644 deploy/health/car-analyzer-health.timer /etc/systemd/system/car-analyzer-health.timer
systemd-analyze verify /etc/systemd/system/car-analyzer-health.service /etc/systemd/system/car-analyzer-health.timer
systemctl daemon-reload
systemctl enable --now car-analyzer-health.timer
systemctl start car-analyzer-health.service
