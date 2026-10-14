# Independent scheduled-collection verification

The actual **14 October 2026, 07:17 UTC / 09:17 Europe/Sarajevo** scheduled run
has been independently assessed. **62 new listings and 104 observations were
persisted to canonical Supabase and are visible through the protected hosted API
and Market notice.** The run is honestly **partial** because the installed priority
query lacks collector-role read access. The audit reports **38 passing checks and
one failing capability check**. Main's recurring heartbeat remains paused at the
user's request. No extra source run, permission change, budget reset, merge or
backup-destination change was performed.

## Actual scheduled result — 14 October 2026

The timer triggered at **07:17:03 UTC**. The full pre-backup completed before the
collector process started at **07:17:32**. Supabase run **21**, session
`direct-d780e549-910a-4998-90f3-bdfe8d306b5c`, ran from
**07:17:46.380688 to 07:19:29.726420 UTC**. The installed real job marker and journal
agree: three discovery attempts, discovery complete, 104 observations, 62 new
listings, zero detail attempts/successes, one error and
`stopped_reason=ProgrammingError`. The job exited **1**, systemd `Result=exit-code`.
The journal reader uses `--all`: ordinary systemd JSON output replaces a report
over 4096 bytes with null, which must not be mistaken for a missing real report.

| Evidence | Preserved baseline | After scheduled run |
| --- | ---: | ---: |
| Listings | 6,612 | 6,674 |
| Immutable observations | 16,514 | 16,618 |
| Direct checks | 151 | 151 |
| Derived labels | 6,406 | 6,468 |
| Website run records | 15 | 16 |

All **104** observations fall inside the real run interval. All previous immutable
IDs/content match the retained pre-run backup and the 13 October verifier baseline.
All 12 current table checksums match the actual post-run snapshot; the pre-run
snapshot independently decoded with matching checksums. All labels have current
source fields/version. Following filters/original baselines, candidates, all
108 imported enrichment rows and the dealer source pause are unchanged. Current
searches/candidates are empty, so live preservation covers the empty state;
nonempty preservation is covered by existing implementation tests.

Newest observation: **14 October 07:19:19.988340 UTC**. Last clean anchor:
**1 October 17:31:22.610647 UTC**, unchanged. UTC-day counters are **3/3 discovery,
0/20 direct**, with no active website-budget pause. Unused capacity was not spent
by starting an extra collector during verification.

### Failure cause and repair dependency

A read-only reproduction of installed `load_priority_inputs()` under the actual
`car_analyzer_collector` login returned **ProgrammingError / SQLSTATE 42501**:
**“permission denied for table dealer_listing_details.”** Privilege queries also
confirmed missing SELECT on **dealer_candidates** and **dealer_model_searches**.
The installed query joins details/candidates and then reads Following filters;
all three reads are required even when private tables are empty. Successful
backup-role or rolled-back write verification did not exercise this collector-role
queue query. The stored run/journal retained only the generic exception class;
the exact denial comes from independent read-only reproduction.

The priority and collector-role owners need a focused SELECT/RLS integration
repair and an actual collector-role queue read test. Preserve denials on private
state updates/deletes, immutable evidence updates/deletes and app evidence writes.
This verifier does not change runtime code, schema or roles. The reported failure
is a capability gap, not an expanded app access boundary.

**Zero detail attempts means the repaired browser, allocation fairness and successful
priority detail collection remain unvalidated by this run.** No `priority_selection`
plan was persisted because loading inputs failed first. Discovery success is real;
it does not validate the queue or browser repair. No OLX request, fabricated listing
evidence or private filter output was used in diagnosis.

### Provenance, backups and hosted evidence

Active release: **`priority-b6e94ff`**, combined manifest
`coverage-priority-installation.json`, extending browser **`1327757`** and cutover
**`3bb5a10`**. All release hashes and live entry scripts match. Pinned image:
`sha256:7d51ac14987e3a9d17decc711146db2191d741f2fa5ca31bf677170c0ee8884d`.
The service remains standalone direct Supabase collection with no local DB or old
sync post-step. Next existing timer: **15 October, 07:17 UTC / 09:17 Sarajevo**.

Full backup extension: **`e61e59d94f0ad52d9f3b2815a48646c91bb07ed7`**. All five
installed hashes match, including `zz-canonical-backup.conf`; approved pre/post
hooks exited **0**. Direct JSON post-backup was captured at **07:19:35**. Full bundle
**`canonical-20261014T071941745546-after`** was exported at **07:19:41** and restored
successfully at **07:20:38.944891 UTC**. Actual artifact hashes, decoded 12-table
snapshot and current table checksums match. Current export/restore attempt markers
refer to that exact bundle; content, schema/access and sequence checks passed with
scratch network disabled and cleanup confirmed. This verifier ran no new restore.
Recurring off-VPS copying remains unconfigured, as the backup owner's handoff states.

Hosted `/api/overview`: **200**, `Cache-Control: no-store`, 6,674 listings /
16,618 observations, exact new partial run and unchanged clean anchor. Signed-in
Market was reloaded and visibly showed **“Last clean collection: 1 Oct 2026. Latest
website collection: 14 Oct 2026 · partial · 104 recorded observations.”** The
initially stale open tab showed 13 October; reloading resolved that retained-page
state. Anonymous Market and API still return **302** to Vercel sign-in. App cannot
mutate evidence or saved filters/baselines or inherit collector access; backup and
collector mutation restrictions and RLS remain intact.

Private evidence: `.private/scheduled-verification/2026-10-14/` and
`/opt/car-analyzer/.daily-delivery/scheduled-verification/2026-10-14/`, containing
`audit.json`, combined scheduler/runtime/backup capture, API/access captures,
browser metadata and local `market.png`. Final audit at **15:36:37 UTC**:
**38 pass / 1 fail**, no pending scheduled-run/browser checks. The only failure is
`priority_collector_read_access`. The old `current/` baseline and pending probe are
retained. Consume the current coverage and backup handoffs before any repair:
`/Users/ognjendivljak/.codex/worktrees/coverage-priority/Car analyzer/docs/COLLECTION_COVERAGE.md`
and `/Users/ognjendivljak/.codex/worktrees/canonical-backup-recovery/Car analyzer/docs/CANONICAL_BACKUPS.md`.

## 13 October cutover baseline

The read-only audit passed for the installed **manual cutover validation**, run
**17**, session `direct-87b84bbc-43c4-4bb1-b7f1-8f19631dc7b1`, from
17:49:23.788696 to 17:54:09.124973 UTC. This does **not** certify a normal timer run.

- The system journal, service result, direct job marker and Supabase run agree:
  **partial**, exit **1**, 13 observations, 0 new ads, 16 checks, 13 listed outcomes,
  3 failed checks and 4 errors including the two-consecutive-failure stop.
- Canonical totals are **6,612 listings, 16,514 observations, 151 direct checks**.
  The newest observation remains **13 October 17:53:21.536028 UTC**. Running this
  verifier did not change it. The clean anchor remains **1 October
  17:31:22.610647 UTC**.
- All 6,406 derived labels have current source fields/version. All pre-run
  immutable observations/checks remain unchanged. Full current table checksums
  match the collector's actual post-run backup. The pre-run backup was decoded
  independently and its complete table checksums matched the marker.
- Following searches and candidates are currently empty. Their checksums, all
  108 imported enrichment rows and the retained dealer source-pause record match
  before/after. This proves empty-state preservation, not a live nonempty saved
  search flow. Existing cutover tests cover nonempty filters/original baselines;
  no verification searches or fabricated listing evidence were inserted here.
- Role inventory confirms RLS, no anonymous/authenticated reads or writes,
  no app evidence writes, no app filter/baseline update, no app collector-role
  membership, no backup writes and no collector protected-state/history updates.
- Installed service starts the standalone direct container, clears the old
  post-step and has no local DB/sync configuration in its entry scripts. Timer
  is enabled/active at **07:17 UTC**, next trigger **14 October**. Historical
  `installation.json` and `latest-success.json` describe old delivery and are
  not active collection/freshness markers.
- Capture records source commit, image digest, active release, all runtime
  file hashes against the installation manifest and hashes of the live entry
  scripts. The audited 17:49 real run used cutover commit `3bb5a10`. The browser
  follow-up subsequently installed release **`details-1327757`** at **18:53:13 UTC**;
  its combined manifest is `detail-reliability-installation.json`. Independent
  preparation capture confirms that release's full hashes and live entry scripts.
  It adds no real observations. Re-capture after other work chats install changes
  and attribute the scheduled result to those deployed versions.
- Independent protected API response is **200**, `Cache-Control: no-store`, with
  matching counts, run status, latest observed run and clean anchor. Signed-in
  Market displays “Last clean collection: 1 Oct 2026. Latest website collection:
  13 Oct 2026 · partial · 13 recorded observations.” Anonymous Market and API
  return **302** to Vercel sign-in.
- UTC-day budget remains **3/3 search, 18/20 details**, no active website-budget
  pause. No collector, browser source request, budget reset or extra OLX request
  was used by verification. The legacy local database remains retained.

Final current audit: `.private/scheduled-verification/current/audit.json`, captured
at **18:51:28 UTC**; browser screenshot `market.png`, API/access captures and scheduler
capture are alongside it. VPS copies are under
`/opt/car-analyzer/.daily-delivery/scheduled-verification/current/`.
The explicit next-run probe returned **pending**, `run: null`, exit **2**, at
18:51:51 UTC; both copies retain `next-pending.json`.

The cutover contract is [DIRECT_SUPABASE_COLLECTOR.md](DIRECT_SUPABASE_COLLECTOR.md)
in draft [PR #4](https://github.com/divljak/caranalyzer/pull/4), runtime commit
`3bb5a10`, handoff `a058c4d`. At preparation it resides in the separate worktree
`/Users/ognjendivljak/.codex/worktrees/direct-supabase-collector/Car analyzer/docs/`.
Read that current contract before resolving paths or touching runtime services.
Also read the current browser [DETAIL_CHECK_RELIABILITY.md](DETAIL_CHECK_RELIABILITY.md)
handoff in `/Users/ognjendivljak/.codex/worktrees/detail-check-reliability/Car analyzer/docs/`.
The verifier recognizes its combined manifest when that exact release is active.
For a later approved coverage/backup release, pass its exact combined manifest with
`--runtime-manifest PATH` when capturing scheduler state; do not silently accept
unexplained hash drift. The new browser's successful check evidence includes
`browser.navigation_timeout`, `browser.script_retries`, and `browser.elapsed_ms`;
inspect those after the actual run to attribute recovery. Real reliability of this
repair is still pending the scheduled run.

## Reproduce a scheduled-run audit

Only `scripts/verify_scheduled_collection.py`,
`scripts/capture_collection_hosted.py`, this document and
`tests/test_scheduled_collection_verification.py` belong to this task. A copy of
the verifier is installed at
`/opt/car-analyzer/.daily-delivery/verify-scheduled-collection.py`.
It uses the existing **read-only backup login**, a repeatable-read READ ONLY
transaction, bounded queries and a separate short-lived container. It does not
import application startup, hold source advisory locks, run DDL, collect ads or
alter evidence/status markers. It writes only its own private audit files.

1. Re-read the cutover handoff and inspect the existing service. If it is still
   activating, wait briefly and inspect again; never start a competing collector
   or change its timeout. Check Main's coordination before reacting to runtime
   changes by other work chats.
2. Capture new service/timer/journal metadata on the VPS. The helper stores safe
   metadata and selected report fields, not arbitrary environment/command lines:

   ```sh
   ssh -o BatchMode=yes root@167.99.139.58 'umask 077
   mkdir -p /opt/car-analyzer/.daily-delivery/scheduled-verification/2026-10-14
   python3 /opt/car-analyzer/.daily-delivery/verify-scheduled-collection.py --capture-scheduler --runtime-manifest /opt/car-analyzer/.daily-delivery/coverage-priority-installation.json --output /opt/car-analyzer/.daily-delivery/scheduled-verification/2026-10-14/scheduler.json'
   ```

3. From the repository root on the Mac, confirm the existing Vercel CLI points
   to `prj_HMP6rQiBrGFYzTnFCaVIocPNFv9x` and
   `ogilud-gmailcoms-projects`. Use its existing authentication. Global CLI
   39.3.0 lacks `curl`; the already-cached CLI 63.1.2 was used successfully:

   ```sh
   node /Users/ognjendivljak/.npm/_npx/67eb4586ca667318/node_modules/vercel/dist/index.js project inspect --non-interactive
   .venv/bin/python scripts/capture_collection_hosted.py --output-dir .private/scheduled-verification/2026-10-14 --vercel-js /Users/ognjendivljak/.npm/_npx/67eb4586ca667318/node_modules/vercel/dist/index.js
   ```

   The helper performs only GET requests to the exact preview API and anonymous
   Market/API endpoints. A modern installed CLI can be used by omitting
   `--vercel-js`. Keep body/header files private; headers may contain cookies.
   Do not disable protection, use a public alias or expose tokens.
4. Open a new signed-in browser tab on the private `/market` URL. Capture a new
   screenshot after the run, then compare the visible notice with the new API
   and canonical results. Use browser tools for this step. Write `browser.json`
   in the new local private directory with actual `captured_at`, exact `url`,
   screenshot path, observed `freshness_text`, and `freshness_text_matches` set
   from that comparison. Preserve the prior observed run when the new attempt
   has zero observations. A backend match alone does not verify the browser.
5. Copy only the new verification input JSONs to the private VPS directory:

   ```sh
   scp .private/scheduled-verification/2026-10-14/overview.json .private/scheduled-verification/2026-10-14/access.json .private/scheduled-verification/2026-10-14/browser.json root@167.99.139.58:/opt/car-analyzer/.daily-delivery/scheduled-verification/2026-10-14/
   ```

6. Run the read-only verifier with the preserved current baseline:

   ```sh
   ssh -o BatchMode=yes root@167.99.139.58 'docker run --rm --read-only --name car-analyzer-readonly-verifier --tmpfs /tmp -v /opt/car-analyzer/.daily-delivery:/delivery:ro -v /opt/car-analyzer/.daily-delivery/scheduled-verification:/verification-output "$(cat /opt/car-analyzer/.daily-delivery/direct-image)" python /delivery/verify-scheduled-collection.py --credentials /delivery/backup-secret.json --state-dir /delivery --scheduler-json /delivery/scheduled-verification/2026-10-14/scheduler.json --overview-json /delivery/scheduled-verification/2026-10-14/overview.json --browser-json /delivery/scheduled-verification/2026-10-14/browser.json --access-json /delivery/scheduled-verification/2026-10-14/access.json --baseline-json /delivery/scheduled-verification/current/audit.json --expected-start 2026-10-14T07:17:00Z --scheduled --output /verification-output/2026-10-14/audit.json'
   ```

7. Retrieve the audit and scheduler JSONs to the matching local private directory.
   Keep the baseline; do not replace it with a failed/incomplete audit.
   Update this document with the actual scheduled result and handoff to Main.

## Interpretation and failure checklist

Exit **0/pass** means the layers agree; read `run.status` separately. It can
honestly report a **partial, failed or paused** collection. Exit **1/fail** means
a discrepancy requires investigation. Exit **2/pending** means required evidence
is missing or the real scheduled run is future. Missing scheduled evidence after
the 25-minute job allowance becomes failure. Unit tests cover this distinction.

- **Timer origin:** last trigger must be within 120 seconds of expected 07:17 UTC,
  service start within 120 seconds of trigger, and real DB run within 120 seconds
  of service start. A manually rerun job cannot certify the normal timer run.
  Persistent catch-up or delayed startup is a specific timing finding to report.
- **Launch/termination:** stale/missing direct JSON after a newer service start
  is not success. Inspect the journal, container-launch failure and timeout/result;
  report the gap even if Supabase still holds yesterday's good records. An
  interrupted process may leave `running` rows until the next normal acquired
  collector lock marks them failed. Do not repair records with this verifier.
- **Collection quality:** inspect run report `stopped_reason`, rejected records,
  discovery completeness, check outcomes, errors and pause/budget records. Zero
  new ads or zero observations alone is not failure. Source refusal, browser
  timeout, incomplete discovery and DB failure are different findings. A completed
  website run with no observations can retain a partial scraping log/old clean
  anchor; report that faithfully. A verification timestamp never advances evidence.
- **Attribution:** snapshot timestamps have no run foreign key. Their match uses
  the real run interval and the cutover's exclusive collector lock. Direct checks
  have a real run foreign key. If another writer appears, investigate attribution
  instead of treating time-window counts as proof.
- **Preservation:** compare pre-run backup to its own checksums, old immutable IDs
  and content to current data, protected state before/after and saved current
  baseline. A user Follow/Unfollow or another authorized enrichment edit can explain
  a checksum change; attribute it and do not overwrite it to make an audit pass.
  Current post-backup equality is deliberately strict and can flag later legitimate
  edits. Budget reset is valid only on a new UTC day; never clear persistent pauses.
- **Configuration/access:** confirm direct entry, no post-sync/local settings,
  session-pooler backup credential, role/RLS boundaries and private hosted access.
  If runtime hashes/release changed, inspect the new handoff and installed source;
  script token checks classify entry configuration and hashes verify the recorded
  installation, but are not a proof of all code behavior. Compare any newer
  approved release/handoff. The retained local DB is expected to lag; do not run
  old sync against it.
- **UI:** match exact run start/status/count and clean anchor, latest run with
  observations and uncached API. Reopen/reload Market after the actual run. A partial
  result must not make all ads look freshly checked or imply confirmed sales.

Validation: **13 focused unit tests passed**, including missing collector read
permissions and stale successful restore markers that must not certify a newer
failed archive. The actual scheduled VPS/DB/API/browser/access assessment is
complete with the permission defect explicitly unresolved. Only the four focused
verification files belong to source publication. No collector runtime, schema,
schedule, role, backup destination or source budget was changed.

Read-only transaction behavior follows
[PostgreSQL SET TRANSACTION](https://www.postgresql.org/docs/current/sql-set-transaction.html).
Role checks follow [Supabase Postgres roles](https://supabase.com/docs/guides/database/postgres/roles),
and protected requests use [Vercel CLI curl](https://vercel.com/docs/cli/curl).
