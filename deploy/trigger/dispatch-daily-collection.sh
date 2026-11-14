#!/usr/bin/env bash
# Ask GitHub Actions to run the daily collection (workflow_dispatch). This host does
# not visit OLX and holds no database credentials; it holds only a fine-grained token
# limited to Actions: read and write on divljak/caranalyzer.
set -euo pipefail
umask 077

REPO="${CAR_ANALYZER_REPO:-divljak/caranalyzer}"
WORKFLOW="${CAR_ANALYZER_WORKFLOW:-daily-collect.yml}"
REF="${CAR_ANALYZER_REF:-main}"
MODE="${1:-collect}"                     # collect | verify
TOKEN_FILE="${CAR_ANALYZER_DISPATCH_TOKEN_FILE:-/etc/car-analyzer/github-dispatch-token}"

[[ "$MODE" == collect || "$MODE" == verify ]] || { echo "Mode must be collect or verify." >&2; exit 2; }
[[ -r "$TOKEN_FILE" ]] || { echo "Token file $TOKEN_FILE is missing; see docs/DAILY_COLLECTION.md." >&2; exit 2; }
[[ "$(stat -c %a "$TOKEN_FILE")" == 600 ]] || { echo "Token file must be mode 600." >&2; exit 2; }
token="$(tr -d '[:space:]' < "$TOKEN_FILE")"
[[ -n "$token" ]] || { echo "Token file is empty." >&2; exit 2; }

body="$(printf '{"ref":"%s","inputs":{"reason":"vps-timer","mode":"%s"}}' "$REF" "$MODE")"
url="https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches"

for attempt in 1 2 3; do
  # The token goes to curl via a config file on stdin, so it is never in argv (ps) or logs.
  status="$(printf 'header = "Authorization: Bearer %s"\n' "$token" | curl --silent --show-error \
    --config - --max-time 30 --output /dev/null --write-out '%{http_code}' \
    --header 'Accept: application/vnd.github+json' --header 'X-GitHub-Api-Version: 2022-11-28' \
    --header 'User-Agent: car-analyzer-dispatch' --request POST --data "$body" "$url" || echo 000)"
  case "$status" in
    204|200) echo "Dispatched ${WORKFLOW} on ${REF} (mode=${MODE}) at $(date -u +%FT%TZ)."; exit 0 ;;
    401|403|404|422) echo "GitHub refused the dispatch (HTTP ${status}); check the token scope/expiry and workflow name." >&2; exit 1 ;;
  esac
  echo "Dispatch attempt ${attempt} failed (HTTP ${status}); retrying." >&2
  sleep $((attempt * 30))
done
echo "Dispatch failed after 3 attempts." >&2
exit 1
