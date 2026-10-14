#!/usr/bin/env bash
set -euo pipefail
umask 077
cd /opt/car-analyzer
[[ "$EUID" == 0 ]] || { echo 'Run this installer as root on the collector VPS.'; exit 1; }
[[ "$(systemctl show car-analyzer-collector.service -p ActiveState --value)" != activating ]] || { echo 'Wait for the current collector run to finish before installation.'; exit 1; }
[[ -r .daily-delivery/sync-secret.json ]] || { echo 'Provision the scoped sync-role credential file first.'; exit 1; }
chmod 700 .daily-delivery scripts/daily-delivery.sh
chmod 600 .daily-delivery/sync-secret.json
for kind in service timer; do
  mkdir -p "/etc/systemd/system/car-analyzer-collector.$kind.d"
done
install -m 644 deploy/systemd/car-analyzer-delivery.conf /etc/systemd/system/car-analyzer-collector.service.d/delivery.conf.new
mv /etc/systemd/system/car-analyzer-collector.service.d/delivery.conf.new /etc/systemd/system/car-analyzer-collector.service.d/delivery.conf
install -m 644 deploy/systemd/car-analyzer-delivery-timer.conf /etc/systemd/system/car-analyzer-collector.timer.d/delivery.conf.new
mv /etc/systemd/system/car-analyzer-collector.timer.d/delivery.conf.new /etc/systemd/system/car-analyzer-collector.timer.d/delivery.conf
systemctl daemon-reload
systemctl enable --now car-analyzer-collector.timer
systemctl list-timers car-analyzer-collector.timer --no-pager
