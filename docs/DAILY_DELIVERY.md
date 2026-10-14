# Daily collector delivery to Supabase

Installed and verified on 13 October 2026 on the existing collector VPS,
`root@167.99.139.58`, application directory `/opt/car-analyzer`.
Supabase project: `ojmokmsjppkqwppmrzpv`.

The existing **car-analyzer-collector.timer** remains the only collection schedule.
Its override makes the existing time explicit: **07:17 UTC every day**, which is
09:17 in Europe/Sarajevo during daylight saving time and 08:17 during standard
time. On 13 October the next trigger was 14 October, 07:17 UTC. The timer is
enabled, active and persistent. No Mac, Codex heartbeat, GitHub schedule or
Vercel scraper is involved.

## Actual evidence collected and delivered

The morning collector had failed with a Chromium renderer timeout. Earlier
daily attempts were partial or failed; the latest genuine clean collection
remains **1 October 2026, 17:31:22.610647 UTC**.

One controlled live run at 16:48 UTC on 13 October used the remaining daily
budget: two discovery pages and two direct-page attempts, with the established
15-second minimum delay. It recorded **68 observations, including 53 new ads**.
The newest observation is **13 October, 16:48:53.351421 UTC**. Both direct checks
timed out and the run stopped after two consecutive failures; the run is partial,
with three reported errors (the two detail failures plus the run stop). No ad was
marked sold or removed from search-window absence.

These observations, run records and failure checks were delivered at
**16:51:00.917953 UTC**. A repeat at 16:51:54 and a systemd service verification
at **16:54:22.814509 UTC** changed zero evidence rows. The latter ran the same
installed pipeline in sync-only mode through a temporary service override,
which was removed immediately afterward. The final service points to the normal
collection pipeline; its last verification result is success, exit status 0.
The first live pipeline delivered successfully but its final shell exit was
affected by an in-place script update during execution. Installation now uses
atomic replacement and rejects an activating collector service.

| Evidence table | VPS rows | Supabase rows |
| --- | ---: | ---: |
| car_listings | 6,612 | 6,612 |
| listing_snapshots | 16,501 | 16,501 |
| scraping_logs | 130 | 130 |
| website_collection_budget | 1 | 1 |
| website_collection_runs | 14 | 14 |
| website_listing_states | 665 | 665 |
| website_listing_checks | 135 | 135 |

All source rows in these seven tables were verified by canonical full-row SHA256
checksums inside the delivery transaction. Destination-only history is retained.
Supabase also retains 108 imported gearbox detail records, 6,406 derived vehicle
labels, one source-pause record, and zero saved searches/candidates at verification.
Before/after checksums for searches (including filters and original baselines),
candidates, gearbox enrichment and pauses matched. Tests additionally exercise
nonempty filters/baselines, enrichment and destination-only observations.

The protected hosted `/api/overview` returned 6,612 listings, 16,501 observations,
the partial 13 October run with 68 observations, and the unchanged clean anchor.
The Vercel integration chat deployed the related freshness notice at commit
`29d09fc` in PR #2, with deployment status READY. The live Market browser showed
“Last clean collection: 1 Oct 2026. Latest website collection: 13 Oct 2026 ·
partial · 68 recorded observations. New observations do not mean every listing
was freshly checked.” The integration chat verified the shared notice on all
four pages. Importing data does not refresh old listings.

## Pipeline and failure behavior

`scripts/daily-delivery.sh` runs bounded collection, verifies the full VPS
custom-format PostgreSQL backup, then runs `scripts/sync_supabase.py` in the
existing collector image. Mounted source files allow the collector repair and
delivery to run without rebuilding or restarting the VPS web app. Collection
remains limited to three newest-result search visits and 20 direct checks per
UTC day; existing persistent budget/pause and source advisory locks still apply.

The browser uses eager navigation and a DOM readiness check to avoid waiting
for unrelated ad/analytics loading. A navigation timeout can inspect the existing
DOM without requesting the page again. HTTP refusal, identity and parser checks
remain mandatory. Refusals preserve the existing persistent source pause.
Incomplete discovery cannot become a clean freshness anchor, even on a rerun
that successfully checks some details.

Delivery uses a repeatable-read source snapshot and a single target transaction,
advisory/table locks, immutable-history and identity collision checks, sequence
advancement and foreign-key/content verification. It never deletes collector
rows, rewrites observations, restores source data over the whole target, or
modifies hosted searches, baselines, candidates, enrichment or pauses. Derived
labels are refreshed transactionally. A conflict or validation failure rolls
back all evidence changes. Sequence advancement is monotonic; rolled-back
transactions may leave harmless sequence gaps.

Partial/failed collection records are also backed up and delivered. The systemd
job exits nonzero when collection is incomplete, even if delivery succeeded.
Connection failures get at most three sync attempts, with 15/30-second backoff.
Collector failures are not automatically retried; the next regular day uses the
existing budget/pause rules. Backup failure prevents delivery. A file lock
prevents overlapping pipelines; a target transaction lock also prevents competing
sync clients. Systemd bounds the entire operation to 25 minutes.

Private state is in `.daily-delivery/`: `latest-success.json` advances only after
commit and contains collection/observation dates, counts, source checksums and
protected-state checksums. `latest-attempt.json` records the pipeline start/end,
exit status and collection exit status. The systemd journal retains stage output;
errors expose only safe error classes/SQLSTATE, never SQL parameters or credentials.

## Credentials and installation

The separate `car_analyzer_sync` login uses the session pooler over TLS. Its
private credential JSON is stored only on the VPS, owner root, directory mode
700 and file mode 600, mounted into the collector container. No credential is
passed in process arguments. `.daily-delivery/` is excluded from Git and Docker
builds. The password was provisioned separately using a SCRAM verifier; the
role migration contains no password or verifier.

The role has read/insert/update permissions only on the mutable evidence and
derived-label tables; snapshots/checks permit read/insert only. It can read
protected dealer tables for backups and preservation checks, but cannot write
them, delete evidence, create schema/roles, or bypass RLS. The existing
`car_analyzer_app` role remains unable to update evidence and is not a member of
the sync role. Anonymous/authenticated Data API access remains closed. Supabase
security advisors reported no findings; performance reported only the existing
unused check-run-index informational notice.

After provisioning the credential and applying the role grants to the existing
application schema, install with `deploy/install-daily-delivery.sh` on the VPS.
It installs service/timer drop-ins, reloads systemd and enables the existing timer.
Run `scripts/daily-delivery.sh --sync-only` for a delivery retry without visiting
OLX. Do not update executable files in place during an active job; upload a
temporary file, set its mode, then rename it atomically after collection finishes.

## Backups and recovery

Thirty verified source PostgreSQL dumps are retained in `/opt/car-analyzer/backups`.
Thirty complete Supabase application snapshots are retained in `.daily-delivery/`
as `supabase-before-*.json.gz`, each with all 12 tables and per-table checksums.
These snapshots are created and decoded/verified before evidence writes. They
contain private saved-search state and must remain permission-restricted.

`scripts/verify_delivery_backup.py BACKUP` validates every table checksum.
For recovery, create an empty loopback PostgreSQL database named
`delivery_recovery_*`, install the application schema there, set
`RECOVERY_DATABASE_URL` privately, and add `--restore-empty-scratch`. The tool
refuses non-loopback databases or nonempty application tables. A real snapshot
with all 12 tables and 30,573 rows was restored into an empty scratch database
and rechecked successfully on 13 October; only that scratch database was removed.
Inspect a recovery copy and repair only affected evidence under an administrator
transaction after taking a new backup. Never blindly restore over hosted state.

For service rollback, retain `.daily-delivery/service.before` and
`browser.before.py`; remove the delivery drop-ins and reload systemd to recover
the prior schedule/service. This disables delivery and should be used only for
an operational rollback, with a follow-up sync after the fault is corrected.

Disk pressure was reduced from 145 MB free to approximately 1.6 GB free by
clearing package download caches and archiving two unused September collector
images before removing their VPS tags/layers. Verified compressed Docker archives
are retained locally under `.private/daily-delivery/rollbacks/`; restore with
`gzip -dc ARCHIVE | docker image load` if needed. The running app/database and
current collector image were retained.

## Remaining collection limits

The live repair successfully recovered discovery observations today. Direct-page
timeouts remain an observed coverage gap; the navigation-timeout DOM recovery
has focused tests but has not yet produced a successful live direct check.
The next scheduled run is the first full-day run with the final repair. Coverage
remains a bounded newest-ad sample, with only 56 tracked listings successfully
checked in the preceding seven days at the live run. This is not full-market
coverage, confirmed sales, demand or selling-speed evidence.

Implementation follows [Supabase connection guidance](https://supabase.com/docs/guides/database/connecting-to-postgres),
[PostgreSQL upserts](https://www.postgresql.org/docs/current/sql-insert.html) and
[transaction/advisory locks](https://www.postgresql.org/docs/current/explicit-locking.html).
