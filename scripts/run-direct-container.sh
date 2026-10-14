#!/usr/bin/env bash
set -euo pipefail
umask 077
release="$1"
shift
image="$(cat /opt/car-analyzer/.daily-delivery/direct-image)"
# Standalone container: no Compose .env, DB_HOST, local PostgreSQL or dependencies.
exec docker run --rm --name car-analyzer-direct-collector \
  -e CHROME_BIN=/usr/bin/chromium -e CHROMEDRIVER_PATH=/usr/bin/chromedriver \
  -v "$release/database:/app/database:ro" \
  -v "$release/scripts:/app/scripts:ro" \
  -v "$release/run_website_sync.py:/app/run_website_sync.py:ro" \
  -v "$release/market_labels.py:/app/market_labels.py:ro" \
  -v "$release/scrapers/olx_website_collector.py:/app/scrapers/olx_website_collector.py:ro" \
  -v /opt/car-analyzer/.daily-delivery:/delivery \
  "$image" python scripts/direct_collector.py \
  --credentials /delivery/collector-secret.json \
  --backup-credentials /delivery/backup-secret.json --state-dir /delivery "$@"
