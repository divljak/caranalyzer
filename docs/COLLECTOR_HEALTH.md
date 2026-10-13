# Collector health and Main notification handoff

The checker is separate from the collection pipeline: `car-analyzer-health.timer`
inspects the VPS every ten minutes and two minutes after boot. It never starts
collection, requests OLX, spends budgets, resets source pauses, writes canonical
business tables, changes roles or sends messages. The only collection schedule
remains `car-analyzer-collector.timer`, daily at **07:17 UTC** (09:17 Sarajevo in
DST; 08:17 standard time).

It uses the completed direct-write contract: `.daily-delivery/direct-current`,
`direct-image`, `backup-secret.json`, `latest-direct-attempt.json` and verified
`supabase-canonical-*.json.gz` backups. It consumes the existing **read-only
car_analyzer_backup** role through the session pooler over TLS. Its bounded
container mounts only that credential, the newest backup and release code,
all read-only; collector credentials are not mounted. Queries run in one
repeatable-read **READ ONLY** transaction with 10-second statement timeouts.
The probe never invokes backup creation/restoration or imports the collector.
Its Docker resource limits are 192 MB, 0.25 CPU and 64 processes. Each probe is
bounded to 75 seconds; the service to 120 seconds. No retry hits OLX.

`current.json` is an atomic, root-private safe status artifact under
`/opt/car-analyzer/.collector-health/`, independent of collector state.
A nonblocking file lock prevents overlapping checkers. Credentials, full backup
rows, saved searches/baselines, candidate notes, query text, raw exception
messages and private listing reports never enter status or service output.
Status contains operational timestamps, run totals/status, backup verification
metadata, severity, issue explanations and actionable next steps.

## Main: read and notify

Read through existing authenticated SSH; this is a read operation and does not
run the checker, collector or source requests:

```sh
ssh -o BatchMode=yes -o ConnectTimeout=15 root@167.99.139.58 \
  'python3 /opt/car-analyzer/scripts/collector_health.py --read'
```

Equivalent file: `/opt/car-analyzer/.collector-health/current.json`. A missing
artifact returns a safe `health_status_missing` object and nonzero exit. Main
must independently treat an SSH failure or `checked_at` older than **25 minutes**
as a monitoring failure. A current old last-clean timestamp is a collection
freshness warning, not evidence that the health checker itself stopped.

Persist the last consumed **stream_id + sequence** in Main's monitor state.
Every transition has a globally unique `id = stream_id:sequence`, `kind`
(`opened`, `severity_changed`, `recovered`), UTC time, stable issue code,
severity, safe explanation and next step. `issues` holds all currently open
conditions and their `opened_at`; `evidence` supplies last clean collection,
latest real observation, last successful detail check, expected daily start,
last collector start, latest run totals and verified-backup time.

On first read, summarize current actionable issues once and save the cursor.
On later reads, consume only events with a higher sequence in the same stream;
combine simultaneous events into one concise Main update. Say explicitly when
a partial run recorded useful observations while its clean anchor remains old.
Retain warnings during transport outages: unknown freshness is not recovery.
Notify one recovery for each cleared condition, rather than repeating the same
warning/recovery on every run. Stay quiet while conditions are unchanged.

The artifact retains the latest 512 events. If the stream changes or the saved
cursor precedes `oldest_event_sequence - 1`, reconcile Main's remembered open
issue codes against current `issues`, report actual changes plus the monitoring
gap once, then save the new cursor. Do not replay all retained historical events.
Do not notify every changing count/timestamp. No automation is created/changed
by this installation; Main incorporates this contract in its existing monitor.

Server detection runs while Codex is closed. **User notification delivery still
requires Main's Codex follow-up monitor to run**, including the app/host and
account being available. This checker cannot guarantee a notification while
that channel is unavailable. A VPS outage also prevents server-local detection;
Main's SSH/status freshness checks cover that case when Main is running.

## Thresholds and meanings

- Daily start missing: critical after **07:32 UTC** (15-minute grace). Before
  today's 07:17, the expected cycle is yesterday's. Actual systemd start time
  and canonical run start are read; a verification-only artifact is excluded.
- Collector active or canonical run unfinished beyond **40 minutes**: critical.
  The collector's own 25-minute service limit remains unchanged.
- Last finished run failed: critical; partial: warning. A new running attempt
  does not clear the previous failure/partial warning before it finishes. Useful partial observations stay
  visible. A clean run with zero new ads/observations is not a collector failure.
  Pipeline transport/runtime/backup failures remain critical independently.
- Canonical read unreachable: critical, freshness unknown. Existing evidence
  warnings are retained until a successful probe can establish recovery.
- Last clean run or real listing observation older than **48 hours**: warning.
  Partial observations can refresh the observation clock, not the clean clock.
- Active website or details source pause: warning, with its resume time; never
  bypass it. Details API pause is separate from the website collector pause.
- Detail failures: warning after two consecutive failed/blocked checks or at
  least 50% failures among the latest ten checks with at least four recent
  checks. Only checks in the preceding **48 hours** count. Successful unavailable
  or source-ended checks are evidence, not transport failures.
- Newest canonical snapshot missing/invalid: critical. It is checksum-verified
  for all 12 application tables, without restoring. Verified snapshot older
  than **30 hours**: critical. No new backup files are created by health checks.
- Disk available below **10% or 2 GiB**: warning; below **5% or 512 MiB**:
  critical. Memory available below **10%**: warning; below **5%**: critical.
- Probe unable to execute: critical `health_probe_failed`, with database/backup
  state unknown; this does not claim a Supabase outage or corrupt backup. Previously
  open evidence/backup conditions are retained until verification succeeds.
- Checker unable to inspect required state: critical `checker_failed`; previously
  open conditions remain open. Service logs print only safe status summaries.

Changing counts or explanations with unchanged issue code/severity does not
create another event. Severity changes create one transition. Clearing a code
creates exactly one recovery event. Reappearance after recovery opens a new
incident. Source freshness does not establish sales, full-market coverage or
absence of new listings.

## Install, inspect and rollback

Install only after consuming the completed direct-cutover handoff. Upload these
focused files atomically after ensuring this checker is inactive, then run
`deploy/install-collector-health.sh`. This installs only the health service/timer
and starts one read-only inspection; it does not edit collector/runtime code,
credentials, backups, source state, the web app or the collection timer.

```sh
systemctl status car-analyzer-health.timer car-analyzer-health.service --no-pager
journalctl -u car-analyzer-health.service -n 10 --no-pager
python3 scripts/collector_health.py --read
```

A oneshot service being inactive after a successful check is normal. Check the
enabled/active timer, service Result/ExecMainStatus and advancing `checked_at`.
To disable this checker, stop/disable `car-analyzer-health.timer`; leave its status
and event history for Main to reconcile. The collector continues unchanged.
Tests use simulated samples and temporary artifacts only, without production
fault injection or notifications:

```sh
python -m unittest discover -s tests -p test_collector_health.py -v
```

Connection handling follows the current
[Supabase session-pooler guidance](https://supabase.com/docs/guides/database/connecting-to-postgres).

## Installation verification

Installed on 13 October 2026 after the direct-cutover chat completed and its
`DIRECT_SUPABASE_COLLECTOR.md` handoff was consumed. The health timer is enabled,
active and persistent; the oneshot service completed with `Result=success`, exit
0. Systemd validated both unit files. Status directory/file permissions are root
700/600. The collector service/timer configuration hashes stayed unchanged; its
next scheduled trigger remains 14 October at 07:17 UTC. Canonical counts remain
6,612 listings, 16,514 observations and 151 checks; today's budgets remain 3
search visits and 18 direct visits. No OLX requests or evidence writes were made.

The successful 18:46 UTC probe reached Supabase as `car_analyzer_backup`, read
real run 17 (partial, 13 observations / 16 checks / 4 errors), and verified the
newest 12-table snapshot created at 18:38:20 UTC. Clean anchor: 1 October,
17:31:22.610647 UTC. Latest real observation and successful direct check: 13
October, 17:53:21.536028 UTC. Approximately 1.39 GiB disk reserve remained.
Current status is **warning**, with `clean_stale`, `collection_partial`,
`detail_failures`, `disk_pressure`; no source pause, canonical outage or invalid
backup is reported.

An installed repeat advanced `checked_at` from 18:46:10 to 18:46:59 UTC while
retaining the same stream/sequence and emitting zero duplicate events. Fourteen
focused tests cover failure, stale evidence, scheduled grace, partial/paused/budget
outcomes, launch failure versus old partial status, probe failure versus outage,
provisional running state, severity changes, bounded event retention, safe atomic
status, one recovery per condition and no false recovery during unknown state.

Startup verification exposed a missing `market_labels.py` read-only code mount;
it was corrected before handoff. Bootstrap events remain in the history; Main's
**first read must summarize current issues and save its current cursor**, rather
than replaying those obsolete bootstrap events. The stream at verification is
`1225462c-8b40-4daf-b2f9-bce108d66fd6`, sequence **10**. Read live state when
initializing; do not hardcode this cursor if newer transitions have occurred.
No notifications were sent and Main's automation was not changed.

Main still needs to incorporate this read/cursor/freshness contract in its own
follow-up monitor. The first next-day daily run is also pending, and will be
assessed from recorded status/evidence without starting a second collector.
