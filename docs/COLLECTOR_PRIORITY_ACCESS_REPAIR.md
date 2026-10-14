# Collector priority read-access repair — 14 October 2026

Installed Supabase migration **20261014155031**, `collector_priority_read_access`,
repairs the collector-role integration defect diagnosed by the independent verifier.
It grants **SELECT only** on `public.dealer_listing_details`, `dealer_candidates`
and `dealer_model_searches` to **car_analyzer_collector**, with three SELECT-only
RLS policies scoped to that role. These supply the enrichment join, candidate
status join and saved Following filters required by installed `load_priority_inputs()`.
No INSERT/UPDATE/DELETE, role membership, schema privilege, RLS bypass, app grant,
password or credential configuration was added or changed.

This intentionally extends collector read access to these three private application
tables. They remain inaccessible through anonymous/authenticated roles; the private
VPS collector reads them for priority selection. It still cannot write Following
filters/original baselines, candidates, imported enrichment or dealer source pauses.
The app retains its evidence-write restrictions and is not a collector-role member.
The read-only backup role remains separate; legacy sync remains NOLOGIN.

## Actual integration verification

The source-controlled test `scripts/verify_collector_priority_access.py` is installed
separately at `/opt/car-analyzer/.daily-delivery/verify-collector-priority-access.py`.
It imports the **actual installed** priority module from the active immutable release
and verifies its SHA256 against `coverage-priority-installation.json`. It connects
with the existing collector credential; successful backup-role reads do not count
as collector capability verification. It makes no OLX/browser request, inserts no
listing/check/run evidence, consumes no sequence IDs and reserves/resets no budget.

At **15:49:38 UTC**, before repair, the real collector query failed with
**ProgrammingError / SQLSTATE 42501**. At **15:50:34 UTC**, after repair, the same
installed `load_priority_inputs()`, `database_plan()` and pinned-session adapter
query passed under **car_analyzer_collector**:

- 6,468 tracked input listings, 6,450 due eligible ads.
- 20 unique planned checks, remaining daily allowance 20, no website-budget pause.
- All 108 imported detail rows readable under collector RLS, matching the backup
  reader. Current candidates and saved searches are empty and readable.
- Twelve actual zero-match private/history UPDATE/DELETE statements returned
  **42501**. No mutation was allowed or committed.
- Table/column privilege, role membership, RLS and schema checks found no boundary
  violations, including app evidence writes and app saved-filter/baseline updates.
- All **12 full-table content checksums**, table counts, sequence values, UTC-day
  budget row, newest observation and clean anchor matched before and after repair,
  and before/after each probe. Private rows/filters/baselines are never printed.

The independent existing scheduled verifier was then rerun without changing its
source or historical inputs. At **15:51:46.255151 UTC**, it reported **39 pass /
0 fail / 0 pending**; `priority_collector_read_access` now passes. The original
38-pass/1-fail audit remains retained as the pre-repair record. This recheck reused
the existing actual-run scheduler, hosted API and browser captures, so it does not
claim a new browser refresh or another real collection.

Canonical data remains **6,674 listings, 16,618 observations, 151 direct checks**.
The real 14 October run **21** still has **104 observations, 62 new ads, 0 direct
attempts**, status **partial**, `stopped_reason=ProgrammingError`, exit **1**.
Newest observation remains **07:19:19.988340 UTC on 14 October**. The clean anchor
remains **1 October, 17:31:22.610647 UTC**. Today's budget remains **3/3 discovery,
0/20 direct**, with no active website-budget pause. No extra source run was started.
A readable planned queue does not establish successful live checks or coverage gains.

## Preserved runtime and backups

The active release remains
`/opt/car-analyzer/.daily-delivery/releases/priority-b6e94ff`, selected by
`.daily-delivery/direct-current`. Its combined runtime manifest and all hashes
remain unchanged, including the repaired browser, priority module, direct entry
scripts, real attempt marker, service configuration and full-backup extension.
The original `delivery.conf` and `zz-canonical-backup.conf` hashes were checked.
No service/timer restart, release replacement, backup hook/destination change,
legacy database mutation or Main heartbeat change was performed.

The only collection timer remains enabled/active at **07:17 UTC daily**.
Next existing run: **15 October 2026, 07:17 UTC / 09:17 Europe/Sarajevo**.
The service's recorded failed/exit-1 state was retained honestly. Full backups
continue to capture schema/grants/RLS and data through their existing pre/post
hooks. Recovery from an older archive must reapply this migration before testing
priority access; do not infer that an older access catalog contains the repair.
The existing full archive/restore records, destinations and retained local rollback
PostgreSQL database remain intact.

Private evidence is retained at
`/opt/car-analyzer/.daily-delivery/priority-access-repair/` and this worktree's
`.private/priority-access-repair/`:
`before-actual-role.json`, `after.json`, `runtime-before.json` and
`scheduled-audit.json`. The initial probe harness import failure is retained
separately as `before.json`; it is not the actual-role pre-repair baseline.
`installation.json` records the focused source/migration/probe hashes.
The test is separate from the active release and does not replace runtime status files.

## Independent recheck instructions

Run the installed actual-role integration test under the existing pipeline file
lock, with a **new output filename**, using the same collector and backup files:

```sh
ssh root@167.99.139.58 'cd /opt/car-analyzer
flock -n .daily-delivery/pipeline.lock docker run --rm --read-only \
 --name car-analyzer-priority-access-verifier \
 -v /opt/car-analyzer/.daily-delivery:/delivery \
 -v /opt/car-analyzer/.daily-delivery/releases/priority-b6e94ff:/runtime:ro \
 "$(cat .daily-delivery/direct-image)" \
 python /delivery/verify-collector-priority-access.py \
 --runtime-dir /runtime \
 --runtime-manifest /delivery/coverage-priority-installation.json \
 --collector-credentials /delivery/collector-secret.json \
 --backup-credentials /delivery/backup-secret.json \
 --before /delivery/priority-access-repair/after.json \
 --output /delivery/priority-access-repair/independent-NEW.json'
```

Expected exit 0, actual role `car_analyzer_collector`, queue readable, no boundary
failures, all denial SQLSTATEs 42501 and data/sequence/budget preservation true.
Do not use `--before` after a legitimate later collection or Following change;
those changes should be attributed, not treated as repair-induced differences.
The probe always checks preservation within its own execution. It fails rather
than overwriting existing output, accepting module drift or visiting OLX.

To reproduce the scheduled audit, follow `docs/SCHEDULED_COLLECTION_VERIFICATION.md`
in the shared workspace, preserving the original audit and using a new output path.
The existing verifier should now pass `priority_collector_read_access` without any
verifier-source modification. Compare exact runtime hashes and catalog/privileges,
not merely a backup-role queue read or the direct runner's rolled-back write check.

After **the existing 15 October timer run**, independently capture fresh scheduler,
Supabase, hosted API and signed-in Market evidence. Inspect persisted
`priority_selection`, actual attempted check IDs/outcomes and browser diagnostics.
Zero direct attempts in the 14 October run still leave live priority/browser
validation pending; do not spend today's unused allowance or reset budgets to
manufacture it. Preserve honest partial/failed status, clean anchor and source pauses.

The original overly restrictive role was safe for cutover collection but incompatible
with the later priority joins. The earlier generic `--verify-only` check exercised
writes/backups, not this priority read capability. The new integration test addresses
that gap directly without expanding collection traffic or web privileges.

Guidance: [Supabase grants and RLS](https://supabase.com/docs/guides/database/postgres/row-level-security)
and [PostgreSQL GRANT](https://www.postgresql.org/docs/current/sql-grant.html).
