# Discovery measurement and selective refresh

This extends the installed 24 October ingestion fixes in PR #12. The daily
collector still uses the original 07:17 UTC timer, at most three search-page
visits and twenty direct-page visits, minimum 15-second spacing, persistent
refusal pauses and the two-consecutive-detail-failure stop. No API access,
source allowance, extra scheduler or notification automation is introduced.

## Discovery evidence

Each real run now persists `discovery_window` and `request_efficiency` in its
existing report. There is no schema migration or synthetic listing evidence.
Search observations already update asking price and positive availability;
these sightings still do not advance successful direct/detail-check timestamps.

The comparison baseline is the latest finished, same-scope run whose discovery
was validated. A partial run with completed discovery remains useful when its
later detail checks failed. Its IDs are reconstructed from the actual immutable
search observations inside its real run interval; direct observations, unrelated
queries, unfinished runs and failed discovery are excluded. The lookup returns
at most 1,000 distinct IDs and rejects an oversized or empty baseline. Interval
attribution relies on the existing exclusive collector lock.

Per-page metrics distinguish previous-window overlap from overlap with any
historical database row. Listings are identified by source ID, not a renewed
date or a presumed monotonically increasing ID. The report contains:

- `possible_gap` when the bounded scan has insufficient overlap evidence;
- `possible_gap_stale_baseline` when the previous successful window is over
  48 hours old, even if some familiar IDs appear;
- `overlap_observed` only when two consecutive pages each have at least five
  saved IDs and at least 80% are in the previous window;
- `baseline_unavailable`, `incomplete` or `not_observed` when comparison or
  collection cannot be established;
- `current_results_exhausted` for a parser-verified terminal current result set,
  including a verified empty result. This provides no historical completeness.

The overlap threshold is a conservative diagnostic heuristic, not a validated
market boundary. Promotions, renewals and moving rankings can still omit ads.
Full-market coverage remains `unknown` and missed-listing count remains null.
One familiar ad never stops discovery. All configured pages continue to be
scanned: this release measures whether a later depth/frequency change is useful
before changing that policy or reducing discovery coverage.

Counters follow committed observations, including a listing saved before a later
state-write failure. Discovery and direct observations have separate counts;
new IDs per search visit and successful detail checks per reserved detail visit
use their actual denominators. Failed visits still consume the shared allowance.

## Refresh policy v2

The planner reads existing `last_listed_at` and `details_checked_at` fields. It
does not change stored clocks, due dates, private preferences or source budgets.

| Record | Eligibility policy |
| --- | --- |
| Shortlisted/contacted/inspect candidate | Detail refresh after one day |
| Due pending-unavailable check | Preserve its existing confirmation timing |
| New-to-database followed match missing a filter attribute | Prioritize needed details; seven-day detail cadence after a successful check |
| Other followed match | Seven-day detail cadence |
| Ordinary tracked record with missing core attributes | Useful detail work when not recently checked |
| Ordinary record with sufficient attributes and a positive sighting within seven days | Defer an availability-only visit |
| Ordinary successful detail check within thirty days | Defer repeat detail work, including genuinely unspecified attributes |
| Confirmed persistent unavailability or source-reported ended/sold | Thirty-day backoff; a real search sighting can restore listed state |

These are eligibility intervals, not guaranteed refresh deadlines. Raw fields
remain recorded/inferred evidence; a search sighting never becomes a verified
detail check. Missing asking price can be a legitimate on-request ad and does
not by itself trigger endless detail visits. Existing saved filters, explicit
mismatches and uncertain attributes remain authoritative. No buying scope is
invented when Following/candidates are empty.

Every fourth selected slot remains reserved for the oldest eligible attempt,
including at least one slot in a short plan. Deferred rows keep their old attempt
age. Model rotation and failed-attempt rotation remain intact. Planned slots
are separate from actual successful visits; an early failure stop can prevent
later aging slots from being reached.

`priority_selection` records the work each selected check needs and aggregates
deferrals by reason. A deferred-row count is not a claim that an equal number
of visits was saved: the old planner also had a twenty-check cap.

Removal handling stays conservative: absence from search is unused, timeouts
are check failures, and repeated direct unavailability across at least 72 hours
is required for `removed_unconfirmed`. Historical observations are retained;
no removal becomes an inferred sale.

## Seven-day measurement

`scripts/collection_efficiency_report.py --credential-file PATH` reads seven UTC
calendar days using the existing backup role, a repeatable-read READ ONLY
transaction and twelve-second statement timeouts. It never opens a browser,
requests OLX, reserves visits or mutates canonical data. Optional `--output PATH`
creates a new mode-600 report and refuses to overwrite an existing file.

The report explicitly lists days without run records, excludes unfinished run
counters, preserves partial/failed statuses, and separates older uninstrumented
runs from the new overlap measurements. Seven actual instrumented calendar days
are required before `measurement_window_complete` becomes true. Multiple runs
on one day cannot substitute for missing days. The current partial UTC day is
labeled by `as_of_utc`; the report is an observation, not a seven-day SLA claim.

Measurements accumulate in normal daily reports, with no new scheduled job.
After seven real runs, review discovery-gap signals, new IDs/search visit,
detail success rate and planned deferrals. Those measurements can justify a
separate bounded collection-policy change. They cannot estimate missed ads or
prove a national-market census. At twenty direct visits/day, a large historical
fleet remains best-effort; closer freshness requires a focused cohort or a
supported data source with suitable access limits.

The official OLX documentation describes authenticated listing access; a
market-wide export/change feed and its permitted limits remain unverified:
https://api-documentation.olx.ba/listings. No OLX account integration or partner
contact was made for this release.

## Validation and operational handoff

The local suite and packaged Chromium checks use fixture pages; the packaged
container has external networking disabled. Regressions cover false overlap,
renewed/repeated IDs, stale/missing baselines, partial writes, same-scope immutable
baseline reads, empty results, fresh positive sightings, missing attributes,
candidate/followed cadence, unavailable confirmations and incomplete weeks.

The browser/parser code is unchanged from the installed ingestion repair.
DOM-timeout diagnostics and conservative parser checks are preserved. Actual
detail-page reliability still requires the next scheduled run's new diagnostics;
no live timeout fix or source throughput gain is claimed from offline tests.

Installation must copy the complete current immutable release, overlay only the
five runtime files in this change, verify actual collector/backup read capability,
and use the existing locked installer with no-source verification. Preserve the
existing backup hooks, pinned image, timer, retired web app state and real partial
run marker. Never start an extra source run merely to test installation.

Actual read-only preflight on 24 October at 16:38 UTC succeeded using the
collector login. It reconstructed run 22's **77 discovery IDs** as the next
baseline. The current plan deferred 122 recently checked detail records and one
complete record with a recent positive sighting. These are 123 eligibility
decisions, not 123 proven avoided requests. It still selected seventeen unique
checks from today's remaining allowance; the next scheduled day's counterfactual
plan selected twenty unique checks with five aging slots. Neither plan reserved
or performed a source visit.

The real backup-role seven-day report succeeded. It correctly reported **zero
instrumented days**, six days without any run record (18–23 October), and today's
legacy partial run: 73 new IDs / 3 search visits = 24.33 new IDs per search visit,
1 completed detail check / 3 attempts = 0.3333. These are the existing run's
results, not outcomes attributable to the new policy. New overlap/refresh
measurements start with the next real daily run.

## Installed release — 24 October, 16:45 UTC

Runtime source is `80d50ca68dc5d8bf639f42719f37ba466b3a214e`, installed as
`.daily-delivery/releases/discovery-80d50ca`. The complete parent release
`ingestion-f7c9d20` was retained and only the five runtime files were overlaid.
The 24-file combined manifest is
`.daily-delivery/discovery-refresh-installation.json`. For scheduler capture,
pass that exact path with `--runtime-manifest`; older manifest defaults do not
describe this release.

The existing installer acquired its normal pipeline lock and ran no-source
verification successfully. All twelve before/after table checksums matched;
there were zero source visits and zero fabricated listing observations. The real
partial-run marker, pinned image and canonical-backup override were preserved
byte for byte. Counts remain 6,747 listings, 16,696 observations and 154 checks;
Following/candidates, all 108 enrichment records and the source pause are intact.

Final packaged validation ran **97 tests: 96 passed, one optional PostgreSQL
advisory-lock integration test skipped**. Both real Chromium loopback tests
passed with external networking disabled. Fifteen new regression cases and the
existing priority cases also passed locally. Actual collector-role and
backup-role read-only preflights passed; their reports contain zero source visits.

The original timer stays enabled/active for **25 October, 07:17 UTC / 08:17
Sarajevo**. Its prior failed service result correctly remains today's partial
collection; installation did not turn it into a clean source run. The retired
legacy app was not recreated, and no database/container/image pruning occurred.

Private evidence is under `.private/discovery-refresh-20261024/` in this
worktree and `.daily-delivery/discovery-refresh-20261024/` on the VPS. It includes
the read-only pre/postflight, seven-day baseline, packaged test logs, installation
verification and explicit scheduler/manifest capture. Measurements accumulate
from the next real run; publication/installation does not backfill historical
coverage or establish that today's DOM timeouts are resolved.
