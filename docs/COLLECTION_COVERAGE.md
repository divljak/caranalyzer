# Canonical collection coverage and priority policy

Read-only Supabase baseline: **13 October 2026, 18:49:08.905567 UTC**
(20:49 Sarajevo), project `ojmokmsjppkqwppmrzpv`. The audit used the installed
`car_analyzer_backup` role in a repeatable-read, read-only transaction. It made
**zero OLX requests** and changed no evidence, preferences, pauses or budgets.
The direct-cutover and browser-reliability handoffs were consumed before release.

## Reproducible baseline

`scripts/collection_coverage_report.py --credential-file PATH --output NEW_FILE`
uses the completed direct-runtime credential reader; supply the private backup
credential path, never a password or URL on the command line. The script verifies
the role, applies a statement timeout and refuses to overwrite reports. Reports
are created mode 600. `--as-of` labels a current snapshot; it is not time travel.
For an earlier database state, use its historical backup. The report combines
`docs/coverage-audit.sql`, complete tracked rows, saved scopes, remaining budget,
model sample labels, a read-only adapter check and a next-day queue comparison.

Private baseline on the VPS:
`.daily-delivery/coverage-reports/baseline-release-ready-20261013.json`.
Local copy: this worktree's `.private/coverage/`. Full reports can contain private
followed scopes and must stay excluded from Git/public hosting. The queries and
aggregate results below are the reproducible public-source implementation record.

| Measure | Canonical result |
| --- | ---: |
| All listings / immutable observations | 6,612 / 16,514 |
| Non-legacy tracked ads | 6,406 |
| Last observed within 24 hours / 7 days / 30 days | 72 / 388 / 3,165 |
| Last observed more than 30 days ago | 3,241 (50.6%) |
| Never directly attempted / never successfully directly checked | 6,255 / 6,284 |
| Successful direct checks within seven days | 69 ads (1.08% of tracked) |
| Due direct-check backlog | 6,388 |
| New to the retained database today / within seven days | 53 / 232 |
| All successful / failed direct attempts | 122 / 29 (151 total) |
| Last-seven-day successful / failed direct attempts | 69 / 16 (85 total) |
| Saved scopes / candidates | 0 / 0 |

Direct-attempt failure rate is **19.2% overall / 18.8% in seven days**. These
denominators are actual check records, not run `errors`, which also include stop
events. The 15 website runs are one completed, eleven partial and three failed.
The clean collection anchor remains **1 October, 17:31:22.610647 UTC**; latest
actual observation is **13 October, 17:53:21.536028 UTC**. New database IDs do not
mean newly published cars. Stored `posted_date` exists on 6,054 tracked ads, while
122 have a direct-page publication date; neither establishes an independently
verified original publication date or a transaction date.

Stored API observations document **page 1 only** with requested sizes 100, 200
or 300. Stored website discovery documents **pages 1–3**, with 1,010 observations
of **609 distinct retained ads**. The 13 October recovery saw 79 raw cards across
two pages, persisted 68 distinct observations and added 53 database IDs; cross-page
overlap explains why raw cards are not distinct discoveries. The last stored source
total was 47,046, with an advertised last page of 50. This changing, truncated
window is not a census: subtracting retained history from that total would invent
an omission count. No deeper page, partition traversal or additional live sample
was requested by this task.

There are 708 stored make/model label groups: 100 with at least ten priced ads,
353 recognized groups with smaller priced samples and 255 groups consisting only
of unrecognized labels. 5,914 ads have structured or normalized recognized model
labels. These are labels and seller-entered fields, not spot-verified vehicle facts.
The report marks unknown source coverage and attributes explicitly. A ten-ad
threshold is a small-sample label, not statistical confidence. Model totals can
differ from label-table-only SQL because direct-page make/model fields take the
same precedence as the private UI.

## Existing-budget policy

`database/collection_priority.py` owns pure deterministic selection plus an opt-in
`PriorityWebsiteStore` adapter. Candidate statuses `shortlisted`, `contacted` and
`inspect` receive priority; `passed` receives no candidate boost. Due availability
rechecks come next, then matching followed scopes, possible followed matches with
missing attributes, and all other existing tracked models. Explicit field mismatch
does not count as a followed comparable. Full saved year/mileage/price/fuel/gearbox/
category filters are read unchanged; `days` is deliberately unused for refreshing
stale matching records. Searches, fingerprints and original baselines are never
written. A missing attribute is labeled as uncertain, rather than treated as a
verified match.

Each priority lane rotates one ad per model group per round. Stable hashed group
ordering spreads makes, and its starting point rotates each UTC day. Unrecognized
title fragments share one review pool so they cannot masquerade as hundreds of
equal-priority models. Within a group, oldest attempts go first. With no user
preferences, all existing tracked models form the default scope; no preferred
price, year, body type, fuel or buying strategy was invented.

**Every fourth planned slot goes to the globally oldest attempt**, providing five
protected slots in a 20-check day and at least one in a shorter plan. Never-attempted
age uses `first_seen_at`; repeated discovery sightings cannot reset that age.
Failures advance the attempt clock for rotation but do not advance successful
freshness. Source IDs are deduplicated before allocation; malformed IDs, excluded
IDs, future-due records and legacy/other-source records are filtered before slots
are spent. Empty priority capacity spills to other eligible records.

The adapter uses the direct collector's pinned, lock-holding session. Its inherited
reservation, pause, availability, immutable check/history and run methods remain
authoritative. It caps plans at the **existing 20 daily checks minus actual used
visits**, never alters those counters, and stores `priority_selection` in each real
run report. This field is planned allocation; the original attempts/successes/check
records establish what actually happened, including an early failure stop. A new
run resets the cached plan so paused runs cannot inherit yesterday's selection.

Fairness requires actual visits. A refusal or two consecutive failures can prevent
later protected slots from being reached. Persistent attempt times let a later run
move past failed IDs, but no policy can guarantee a freshness deadline while source
access fails. Three discovery visits/day, minimum 15-second spacing, the single
07:17 UTC daily timer, source pauses and failure stops stay unchanged.

## Measured selection improvement and practical limits

Counterfactual comparison for **14 October, 07:17 UTC**, using the same canonical
snapshot and a nominal 20-check full-day allowance, without modifying any budget:

| Queue measure | Existing policy | Proposed policy |
| --- | ---: | ---: |
| Selected ads | 20 | 20 |
| Recognized model label groups | 15 | 20 |
| Unrecognized model ads | 2 | 0 |
| Ads last observed more than 30 days ago | 10 | 17 |
| Never directly attempted ads | 20 | 20 |

This demonstrates allocation, not newly successful source checks. Actual next-day
discovery, preferences, source availability and failures can change the final queue.
Today's real budget stayed **3/3 discovery, 18/20 direct**; the two unused visits
were left unused after today's failure stop.

At perfect success and no additions, 6,388 due records require **at least 320 days**
for a single pass at 20/day. Actual 69 successful checks in the recent seven days
would imply about **649 days** at that throughput, before revisits/additions.
Protected aging alone would require up to **1,278 full-success days** for the static
backlog at five slots/day; ordinary fair rotation can also serve those ads. These
are transparent capacity scenarios, not promised deadlines. Maintaining all 6,406
tracked ads within a seven-day direct-check age would need roughly **916 successful
checks/day**, over 45 times the current allowance, and is not authorized here.

An achievable existing-budget option is to follow a small exact cohort. For example,
100 relevant ads could receive one pass in roughly seven full-success days through
the 15 priority slots/day, provided candidate/recheck competition does not consume
them. This is a planning example, not a user preference or full-model coverage
claim. The concrete next decision is which models/specification scopes to follow;
until those exist, fair tracked-model rotation is the conservative default. Broader
discovery should first use supported access and measured partitions, with any
traffic expansion decided separately. Prioritization cannot establish sales,
demand or full-market prices.

## Release and next-run verification

Shared runtime integration is limited to `scripts/direct_collector.py`: import and
instantiate `PriorityWebsiteStore`. No browser, timeout, shared legacy store,
collector frequency, schema, grants, credential or backup code changes are needed.
The new module travels in the release's already-mounted `database` directory.
Build from the current complete immutable release and preserve the exact repaired
browser module and any independently installed backup hooks. Installation must
hold `.daily-delivery/pipeline.lock` and reject an active collector, then run the
existing no-source `--verify-only` check before switching `direct-current`.

Local validation: **58 tests run, 57 passed, one existing PostgreSQL lock test
skipped** because its separate audit database was not configured. Fifteen new
tests exercise actual selection limits, filters, starvation under continual new
priorities, fair rotation, duplicate latest-state handling, pauses, exclusions,
failed-check freshness, serialized plan persistence and the caller's pinned session.
The canonical read-only report also executes the adapter on the same backup-role
connection and verifies its selected IDs against the pure plan. No fabricated
listing evidence was inserted into Supabase.

After the first real scheduled run, inspect `priority_selection` and actual check
IDs/outcomes in Supabase, reconcile run counts, inspect the systemd status/private
hosted overview and repeat the read-only report. Report measured successful checks
and backlog changes separately from planned allocation. Partial/failed runs do not
advance the clean anchor. Keep the prior direct release for rollback; do not enable
legacy local collection/sync. Installation evidence is appended below.
