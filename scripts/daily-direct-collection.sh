#!/usr/bin/env bash
set -euo pipefail
umask 077
cd /opt/car-analyzer
exec 9>.daily-delivery/pipeline.lock
flock -n 9 || { echo 'Another daily pipeline is active.'; exit 1; }
release="$(readlink -f .daily-delivery/direct-current)"
[[ "$release" == /opt/car-analyzer/.daily-delivery/releases/* ]] || exit 1
exec /opt/car-analyzer/scripts/run-direct-container.sh "$release" "$@"
