#!/usr/bin/env bash
# Install the GitHub workflow_dispatch trigger on the VPS. Run as root from a checkout.
#   deploy/trigger/install-dispatch-trigger.sh                 install + enable the new timer
#   deploy/trigger/install-dispatch-trigger.sh --disable-old   also disable (not delete) the old collector timer
# The token must already be at /etc/car-analyzer/github-dispatch-token (mode 600).
set -euo pipefail
umask 077
[[ "$EUID" == 0 ]] || { echo 'Run as root.'; exit 1; }
here="$(cd "$(dirname "$0")" && pwd)"
token=/etc/car-analyzer/github-dispatch-token
[[ -s "$token" ]] || { echo "Missing $token; create the fine-grained token first."; exit 1; }
chmod 600 "$token"

install -m 755 "$here/dispatch-daily-collection.sh" /usr/local/bin/car-analyzer-dispatch
install -m 644 "$here/car-analyzer-dispatch.service" /etc/systemd/system/car-analyzer-dispatch.service
install -m 644 "$here/car-analyzer-dispatch.timer" /etc/systemd/system/car-analyzer-dispatch.timer
systemctl daemon-reload
systemctl enable --now car-analyzer-dispatch.timer

if [[ "${1:-}" == --disable-old ]]; then
  # Keep the unit files, release and local PostgreSQL; only stop the schedule.
  systemctl disable --now car-analyzer-collector.timer
fi
systemctl list-timers car-analyzer-dispatch.timer car-analyzer-collector.timer --all --no-pager
