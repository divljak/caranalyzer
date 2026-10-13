#!/usr/bin/env bash
set -euo pipefail
umask 077
cd /opt/car-analyzer
mkdir -p .daily-delivery
chmod 700 .daily-delivery
exec 9>.daily-delivery/pipeline.lock
flock -n 9 || { echo 'Another daily pipeline is active.'; exit 1; }
started_at="$(date -u +%FT%TZ)"
trap 'result=$?; printf "{\"started_at\":\"%s\",\"finished_at\":\"%s\",\"exit_code\":%s,\"collection_exit_code\":%s}\n" "$started_at" "$(date -u +%FT%TZ)" "$result" "${collect_status:-0}" > .daily-delivery/latest-attempt.json.partial; mv .daily-delivery/latest-attempt.json.partial .daily-delivery/latest-attempt.json' EXIT

collect_status=0
if [[ "${1:-}" != '--sync-only' ]]; then
  docker compose run --rm --no-deps \
    -v /opt/car-analyzer/run_website_sync.py:/app/run_website_sync.py:ro \
    -v /opt/car-analyzer/scrapers/olx_website_collector.py:/app/scrapers/olx_website_collector.py:ro \
    collector python run_website_sync.py --search-pages 3 --detail-checks 20 --delay 15 || collect_status=$?
fi
# Preserve and deliver partial evidence and failure records as well. The clean
# collection anchor is taken from genuine error-free source runs, never this job.
/opt/car-analyzer/scripts/backup-postgres.sh
docker compose run --rm --no-deps \
  -v /opt/car-analyzer/scripts/sync_supabase.py:/app/scripts/sync_supabase.py:ro \
  -v /opt/car-analyzer/market_labels.py:/app/market_labels.py:ro \
  -v /opt/car-analyzer/.daily-delivery:/delivery \
  collector python scripts/sync_supabase.py --credentials /delivery/sync-secret.json --state-dir /delivery
if [[ "$collect_status" != 0 ]]; then
  echo 'Evidence delivered; collection was incomplete. Clean freshness was not advanced.'
  exit "$collect_status"
fi
echo 'Backup, delivery and content verification completed.'
