# Direct Supabase collector — installed handoff, 13 October 2026

The active path is **VPS scheduler/browser → Supabase canonical database → private
Vercel API/UI**. Local PostgreSQL and VPS-to-Supabase synchronization are no longer
required by collection. They were retained, not deleted.

## Installed runtime contract

- VPS: `root@167.99.139.58`, `/opt/car-analyzer`.
- The sole collection schedule remains `car-analyzer-collector.timer`, enabled,
  active, persistent, **07:17 UTC daily**. Next trigger verified: 14 October,
  07:17 UTC / 09:17 Europe/Sarajevo. Standard-time Sarajevo is 08:17.
- Service override: `/etc/systemd/system/car-analyzer-collector.service.d/delivery.conf`.
  It calls `/opt/car-analyzer/scripts/daily-direct-collection.sh`, clears the former
  post-collection local backup command, bounds the job to 25 minutes and stops a
  surviving collector container on termination. It does not restart failed jobs.
- Entry scripts: `scripts/daily-direct-collection.sh` and `scripts/run-direct-container.sh`.
  Active immutable source directory is selected by `.daily-delivery/direct-current`;
  at handoff it points to `.daily-delivery/releases/direct-20261013-final`.
- Image is pinned in `.daily-delivery/direct-image` to
  `sha256:7d51ac14987e3a9d17decc711146db2191d741f2fa5ca31bf677170c0ee8884d`.
  The standalone container `car-analyzer-direct-collector` uses outbound Docker
  bridge networking. It has no Compose dependency, local DB settings or `.env`.
  Required Python sources are mounted from the release; Chromium stays in the image.
- Runtime sets `CAR_ANALYZER_COLLECTOR=supabase` and
  `COLLECTOR_CREDENTIAL_FILE=/delivery/collector-secret.json`. Missing/invalid
  credentials fail closed, including when a local `DATABASE_URL` exists.
  `run_website_sync.py` contributes `sync()` only; direct-mode invocation of its
  legacy main is rejected. Schema creation/migration is administrator-only.
- Private credentials: `.daily-delivery/collector-secret.json` and
  `.daily-delivery/backup-secret.json`, root-owned mode 600 inside a mode-700
  directory, mounted as `/delivery`. Passwords were separately provisioned as
  SCRAM verifiers. No passwords enter command arguments, source or browser bundles.
- Connection: Supabase `ojmokmsjppkqwppmrzpv`, existing shared **session pooler**
  `aws-0-eu-north-1.pooler.supabase.com:5432`, database `postgres`, TLS `require`.
  SQLAlchemy uses a one-connection pool, zero overflow, pre-ping, 300-second recycling,
  20-second connection/pool timeouts and TCP keepalives. TLS encrypts traffic;
  certificate verification has not been upgraded beyond the existing `require` setup.

The scheduler wrapper holds `.daily-delivery/pipeline.lock` with nonblocking flock.
The Python runtime holds PostgreSQL session advisory lock **731400220** on one
pinned connection; both listing and tracking sessions use that connection across
commits. This conflicts with the former sync transaction lock and existing website
collector lock. Browser waits do not hold an idle transaction. Losing that connection
stops collection; a session cannot reconnect and continue without its lock.

Installation acquires the pipeline lock, rejects an active service/Compose collector,
reconciles the last source snapshot on first cutover, verifies direct rolled-back
operations, then changes the release symlink and service override atomically and
re-enables the existing timer. Updates do not repeat reconciliation after cutover.
Do not edit an active release in place: stage a new release and use
`deploy/install-direct-collector.sh /opt/car-analyzer/.daily-delivery/releases/RELEASE`.

`.daily-delivery/direct-active` disables the old delivery entry point and installer.
`car_analyzer_sync` is now **NOLOGIN**, its evidence/sequence write grants are revoked,
and its pre-existing pooler session was terminated. Its credential and old pipeline
files remain retained for controlled recovery, not active use.

## Actual collection and hosted verification

Locked final reconciliation at 17:47 UTC changed zero rows across all seven evidence
tables; complete source checksums matched. It preserved destination-only history,
Following filters/original baselines, candidates, imported gearbox details and source
pauses. The retained VPS copy remains at 6,612 listings / 16,501 observations / 135
checks and is deliberately frozen behind the canonical database.

One installed systemd run operated from **17:49:13 to 17:54:19 UTC**. Its database
run is id **17**, session `direct-87b84bbc-43c4-4bb1-b7f1-8f19631dc7b1`, source run
start 17:49:23.788696 and finish 17:54:09.124973 UTC. It made **0 discovery visits**
and **16 direct attempts**, with **13 successful listed checks / 13 real immutable
observations**, 0 new listings, 3 failed checks and 4 reported errors including the
required stop after two consecutive detail failures. Status **partial**, service
exit **1**, `Result=exit-code`, `ActiveState=failed`. This is an honest collection
coverage failure, not a failed Supabase cutover. Do not clear it merely for appearance.

| Table | Canonical rows after run |
| --- | ---: |
| car_listings | 6,612 |
| listing_snapshots | 16,514 |
| scraping_logs | 131 |
| website_collection_budget | 1 |
| website_collection_runs | 15 |
| website_listing_states | 672 |
| website_listing_checks | 151 |
| dealer_vehicle_labels | 6,406 |
| dealer_listing_details | 108 |
| dealer_source_pauses | 1 |
| dealer_model_searches / dealer_candidates | 0 / 0 |

Latest observation: **13 October, 17:53:21.536028 UTC**, listing `73903507`,
asking price 22,900 KM. Last genuine clean anchor remains **1 October,
17:31:22.610647 UTC**. Discovery was already exhausted at cutover. At handoff,
UTC-day counters are **3/3 discovery, 18/20 direct**, no active website-budget pause;
2 direct attempts remain unused. No further source run was started during final
verification. Preserve these counters, pauses and the 15-second minimum spacing.

The protected hosted `/api/overview` returned 6,612 listings / 16,514 observations,
the exact partial run with 13 observations and the unchanged clean anchor. Market
visibly displays “Last clean collection: 1 Oct 2026. Latest website collection:
13 Oct 2026 · partial · 13 recorded observations.” Hosted history for `73903507`
reads availability `listed`, last check **17:53:21.536028 UTC**. Its asking-price
history remains intentionally bounded by the clean anchor; the newer observation
exists in Supabase and overview counts but does not advance that anchor.

Verification used the existing Vercel CLI account with the exact project/team and
preview URL. Anonymous API returned a Vercel sign-in **302**; protection was not
changed. The Vercel connector lacked project/team permission, and the browser blocked
standalone API navigation; existing signed-in Market and authenticated CLI reads
provided verification. No hosted deployment/source changes were made by this task.

## Permissions and failure behavior

`car_analyzer_collector` has SELECT/INSERT/UPDATE on mutable collector evidence and
derived labels; snapshots/checks have SELECT/INSERT only, sequences have USAGE only.
It cannot delete evidence, reset sequences, create schema/roles, bypass RLS or write
Following, candidates, imported detail enrichment or dealer source pauses.
`car_analyzer_backup` can SELECT all 12 application tables, with no mutation grants.
Actual connections tested prohibited operations; PostgreSQL returned **42501**.

`car_analyzer_app` remains the restricted preview runtime role: evidence reads and
Following insert/delete/fingerprint updates only. It cannot alter baselines or
collector evidence and is not a collector-role member. Anonymous/authenticated table
access stays closed. Supabase security advisors returned zero findings.

Connection preflight retries at most 3 times with 15/30-second backoff **before**
source work. Writes/collection are never automatically replayed after ambiguous
commits or source failures. A database error stops further detail attempts rather
than treating it as an ordinary failed website check. Failed direct processes may
leave honest running records; under the next acquired lock, unfinished `direct-%`
records are marked failed with `previous_direct_process_interrupted` (historical
non-direct records are untouched). No failure writes local fallback data.

Safe status files under `.daily-delivery/`:

- `latest-direct-attempt.json`: latest real pipeline, start/end, exit code, report,
  before/after backup paths/counts/checksums or safe failure class/SQLSTATE.
- `latest-direct-backup.json`: latest real run with verified before/after recovery
  snapshots; this means backup success, not necessarily clean collection.
- `latest-direct-verification.json` and `latest-direct-verification-backup.json`:
  isolated verification status, kept separate so it cannot hide the real partial run.
- `latest-success.json`: **legacy final reconciliation only**, no longer a freshness
  marker for active collection.

These markers are written by the Python process. Container-launch failure or a hard
systemd termination can leave the previous marker in place; follow-up monitoring
should compare timestamps and the systemd result/journal as well as reading JSON.

A no-source verification command is
`/opt/car-analyzer/scripts/daily-direct-collection.sh --verify-only`. It uses the same
locks, rolled-back budget/listing updates and diagnostic run inserts, label refresh
inside the rolled-back transaction, and full before/after table checksums. It inserts
no fabricated listing evidence. It may leave normal sequence gaps. Verification
passed twice before cutover and again on the final installed release after real
collection, with identical full application content and unchanged daily budgets.

## Backups, retained dependencies and rollback

Canonical backups run **before and after each daily collection attempt** on the
existing collection schedule. A failed pre-backup prevents source work. A failed
post-backup fails the pipeline while committed evidence remains canonical. The
read-only backup login captures all 12 tables in a repeatable-read read-only
transaction. gzip/JSON decode and every table checksum are verified before rotating.
Thirty `supabase-canonical-*.json.gz` snapshots are retained in `.daily-delivery/`
**in addition to** the useful pre-cutover `supabase-before-*.json.gz` snapshots.
No new paid storage or extra schedule was introduced.

The real post-run snapshot is
`.daily-delivery/supabase-canonical-20261013T175416935929-after.json.gz`.
Its **30,611 rows across 12 tables** were copied off-server and restored with
`scripts/verify_delivery_backup.py --restore-empty-scratch` into the empty loopback
`delivery_recovery_direct_final_20261013` database at port 55432; all full-row
checksums matched. The snapshot, verified application schema and recovery result are
retained privately in this worktree's `.private/direct-collector/`. The earlier empty
`delivery_recovery_direct_20261013` scratch attempt contains schema only and is not a
verified recovery copy. Recovery must use the **full canonical application schema**:
the VPS-only schema dump lacks hosted dealer tables and is insufficient by itself.
Use the verified full schema or the application baseline migration from the hosted
workspace task. Do not blindly restore snapshots over Supabase; inspect a scratch
copy and repair affected state under administrator locks after taking a new backup.

The local `car-analyzer-postgres-1` container remains healthy and retains 30 source
custom-format dumps in `/opt/car-analyzer/backups`, including the final cutover dump
`car_analyzer-20261013-174729.dump.gz`. The live `car-analyzer-app-1` still has
`DB_HOST=postgres`, port 5432, database `car_analyzer`; PostgreSQL activity confirms
its idle connection at `172.18.0.3`. That legacy VPS app therefore still depends on
local PostgreSQL. The separate Buzz app/PG17/Redis/MinIO stack was untouched. The
active collector has no local DB connection and its real Supabase writes did not
change the retained local evidence counts.

Preferred service rollback is to a retained **direct-write release**, under the file
lock, using the same canonical credentials and timer. The previous direct release
is `.daily-delivery/releases/direct-20261013`; its verification markers shared the
real status file, which the final release fixes. `.daily-delivery/pre-direct-delivery.conf`
retains the former sync service override; older `.daily-delivery/service.before`,
`browser.before.py` and local image archives remain retained. **Do not simply enable
the old local pipeline or legacy sync:** canonical evidence now has newer IDs,
observations, checks and budgets. Returning to local collection requires a separate
locked reconciliation of canonical evidence/IDs/sequences/budgets into that source,
protected-state preservation and deliberate administrator role re-provisioning.
If Supabase is unavailable, leave collection stopped instead of silently diverging.

## Source and remaining work

Focused branch: `codex/direct-supabase-collector`, based on PR #3's `21925fc`.
Installed runtime source commit: **`3bb5a10`**; all 15 deployed runtime files
matched its SHA256 manifest in `.daily-delivery/direct-installation.json`.
Draft [PR #4](https://github.com/divljak/caranalyzer/pull/4) is stacked on PR #3.
Changed runtime files: `database/collector_connection.py`, `database/models.py`,
`database/db_manager.py`, `run_website_sync.py`, `scripts/direct_collector.py`,
`scripts/backup_supabase.py`, the two direct shell entry scripts, direct installer and
service override, guards in the old delivery/installer/sync entry points, the
`direct_collector_roles` migration, focused tests and this documentation.
Migration version **20261013174917** matches installed Supabase history.

Validation: **28 focused tests passed**, including real PostgreSQL overlap/commit
locking, connection failure bounds, lost-lock refusal, immutable sync rollback and
saved-filter/original-baseline preservation; actual VPS role-denial and repeated
rolled-back write checks; live direct observations; hosted UI/API/private-boundary
checks; real isolated recovery of all tables. Shared original workspace edits were
not staged or reset.

The next scheduled day remains the first full discovery-plus-detail run with this
architecture. Direct-page reliability is improved in the observed run (13 successes),
but 3 failures remain. Source coverage is bounded, not full-market or sales evidence.
At completion, further timeout reliability, alerts, coverage policy, next-run
verification and expanded recovery audit belong to their separately assigned chats;
this task adds no duplicate scheduler, source-budget expansion or alert system.

Connection choices follow [Supabase connection guidance](https://supabase.com/docs/guides/database/connecting-to-postgres)
and [SQLAlchemy pooler guidance](https://supabase.com/docs/guides/troubleshooting/using-sqlalchemy-with-supabase-FUqebT).
