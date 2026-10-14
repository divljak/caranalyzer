# Coverage improvement plan for dealer research

Prepared and updated **14 October 2026**. The retained baseline queries are as of **13 October,
23:50:24.723210 UTC / 14 October, 01:50:24 Sarajevo**, before the scheduled
14 October, 07:17 UTC / 09:17 Sarajevo collection. This planning work made no
OLX listing/search requests and changed no runtime settings, budgets, schedules,
schemas, deployments or evidence. Baseline metrics below describe that earlier
snapshot; they are not presented as post-run counts.

## Confirmed preference: all car models

The user answered **“All”**, meaning **all car models, with no narrower buying
filters**. This explicit preference supersedes the earlier suggestion to choose
one to three models or begin with a small watchlist. No further model choice is
needed to continue the conservative policy. Keep the existing **three discovery
visits and twenty direct attempts per UTC day**, fair model rotation and protected
oldest-attempt slots. This preference does **not** authorize higher traffic,
frequency, source-budget resets, bypassing pauses or new collection runs.

The practical goal is broad research across the models represented in retained
evidence, with per-model sample counts, age distributions and unknown coverage
visible. It is not a promise to discover every model or keep every ad fresh.
Validate reliable bounded checks, then assess two weeks of actual model/stale
rotation before proposing any permitted access expansion. Broader discovery alone
can add records faster than the direct-check allowance can maintain them.

The baseline had zero saved searches and zero candidates; “all models” is now
recorded here as a user preference, without inserting fake saved searches or
changing ownership/baselines. Diagnostic specification cohorts and small-watchlist
capacity examples below remain measurements/alternatives, not active buying filters.

## 14 October run update and next validation

Main reported **real run 21: 104 discovery observations, 62 IDs new to the database,
and zero direct attempts**, because the collector lacked the SELECT/RLS access
needed by the priority inputs. These figures are coordinator-provided post-run
evidence, separate from this document's earlier read-only audit. They do not
establish publication dates, direct availability, or successful priority rotation.
The older 69-success/seven-day rate below is historical, not a fresh post-run rate.

The direct-cutover chat exclusively owns the permission repair. While it works,
this coverage chat makes **documentation edits only**: no Git mutation, runtime,
schema or grant change and no live collection. Main will integrate these documents
in its serialized Git step. The next existing **15 October, 07:17 UTC / 09:17
Sarajevo** run is the live validation target for repaired direct-check selection.
Do not use another run or reset allowances to validate it sooner.

## Refreshed evidence and its limits

At the pre-run audit, Supabase `ojmokmsjppkqwppmrzpv` had **6,612 listing records,
16,514 observations and 151 direct-page checks**, unchanged since the 13 October
cutover test. Of those listings, **6,406** were non-legacy OLX API/website records; 206 legacy
rendered records remain outside the analytical scope. Latest observation:
**13 October, 17:53:21.536028 UTC**. Last clean collection anchor:
**1 October, 17:31:22.610647 UTC**. No 14 October run result existed at audit time.

| Dimension | Verified state | Required improvement |
| --- | --- | --- |
| Discovery completeness | 609 distinct retained website-discovered IDs in 1,010 observations; recorded pages 1–3. Historical API observations document page 1. | Validate scoped partitions/pagination and record overlap, rejections and truncation. |
| Model coverage | 453 recognized make/model label groups; 100 have ten priced ads across all ages, 65 within 30 days, only five within seven days. | Exact cohort sample counts and trustworthy normalization. |
| Price freshness | 72 ads observed within one day; 388 within seven days; 3,165 within 30 days. | Recent comparable asking prices in the chosen scope. |
| Availability freshness | 69 ads successfully directly checked within seven days; 6,388 due; 6,284 never successfully checked. | Monitoring scope that fits actual successful-check capacity. |
| Attributes | 5,046 ads lack gearbox (78.8%); only 229 have structured gearbox evidence (3.6%); 4,662 lack body category. | Structured seller fields with provenance and explicit unknowns. |
| History | 16,274 non-legacy observations of 6,406 ads, starting 22 June; earlier coverage/recovery incomplete. | Preserve evidence and verify recovery; disclose historical gaps. |

Observation-age buckets: **72 at 0–1 days, 316 at 1–7, 2,777 at 7–30,
1,599 at 30–90 and 1,642 over 90 days**. Thus 3,241 (50.6%) are over 30 days old,
including 25.6% over 90 days. Retained records are not verified current stock.

The source total **47,046** was recorded in the **13 October, 16:48 UTC run**,
with an advertised last page of 50. It was not fetched again for this plan and is
neither a stable universe nor today's verified total. Subtracting retained history
from it would invent a missing-ad count. Older ads outside the newest window and
ads appearing between successful runs can be omitted; their number is unknown.

There were **53 IDs first seen on UTC 13 October** and **232 within the rolling
seven days**. These are new to the retained database, not proven newly published
cars. Discovery can refresh a price without directly verifying availability or
attributes. A clean run means its bounded configured work finished under the
existing rules; it does not establish full-model or full-market coverage.

## Actual model/cohort evidence

Diagnostic examples use large retained model groups and existing UI examples,
without assigning user preference. Counts use the UI's direct-page/normalized-label
precedence and the **audit clock**, rather than its older clean-analysis anchor.

| Model label | Retained | Priced observed within 7 / 30 days | Successful direct checks within 7 days | Gearbox unknown |
| --- | ---: | ---: | ---: | ---: |
| Volkswagen Golf | 599 | 42 / 273 | 6 | 503 |
| Volkswagen Passat | 343 | 20 / 168 | 1 | 237 |
| Škoda Octavia | 261 | 17 / 121 | 2 | 176 |
| Volkswagen Tiguan | 218 | 15 / 105 | 2 | 96 |
| Peugeot 3008 | 99 | 5 / 48 | 1 | 37 |
| Volvo XC60 | 65 | 6 / 34 | 0 | 51 |

In the diagnostic UI specification scope—**2020–23, SUV, diesel, automatic,
50,000–150,000 km, no price ceiling**—3008 has **23 retained / 22 priced /
14 priced observed within 30 days**; Tiguan **9 / 9 / 4**; Karoq **5 / 5 / 2**;
XC60 **1 / 1 / 0**. None has a successful direct check in the past seven days
within that exact scope. Unknown gearbox/category may exclude a genuine comparable;
count missing attributes instead of guessing them into a cohort. Ten distinct priced
ads is a small-sample cutoff, not statistical confidence or representativeness.
Trim/generation/engine/condition comparisons need additional trustworthy fields.

## All-model capacity and alternative freshness scenarios

For the confirmed all-model objective, the historical 6,388-ad due backlog still
requires at least **320 perfect-success days** for one frozen pass, or **649 days**
at the historical realized output. New discovery, repeat checks, pauses and the
14 October permission failure make these lower-bound scenarios, not completion
forecasts. The 62 new IDs reported on 14 October add discovery coverage without
reducing the direct-check backlog through successful checks.

Even the weaker benchmark of **one successful check for each of the 453 recognized
baseline model label groups** would require at least **23 perfect-success days**
using all twenty slots, or **46 days** at historical realized output, if every
success reached a different group. Actual repeats, allocation and unknown labels
extend that horizon. One ad per model is not sufficient model pricing evidence.
Many groups do not even have ten retained priced ads available to refresh.

Keep broad rotation active and show per-model limitations. The small-cohort scenarios
below illustrate the tradeoff if the user later requests shorter freshness for a
subset; they do not replace the confirmed all-model preference.

The installed allowance is **20 direct attempts and three discovery visits per UTC
day**, at the existing daily schedule. Failures consume allowance. In the rolling
seven days, **85 attempts produced 69 successes**: conditional success **81.2%**,
nominal attempt utilization **85/140 = 60.7%**, realized output **9.86 successes/day**.
Search failures, early stops and unused allowance are reflected in realized output.
Overall, 29/151 direct attempts failed (19.2%). Run errors also include stop events;
they are not an attempt-failure denominator.

No successful direct checks from the repaired priority-input path are evidenced
here; run 21 stopped before direct attempts. **15 October, 09:17 Sarajevo** remains
the next existing live validation target. Historical output includes bounded manual
cutover work on 13 October; it is not a forecast of unattended operation. Refusals,
pauses and the two-consecutive-failure stop remain authoritative in all scenarios.

Five nominal slots protect oldest attempts; up to 15 prioritize candidates/followed
scopes. Assuming a static cohort receives three quarters of successful work and
has no competing candidate/recheck queue, first-pass durations are:

| Target ads | Perfect success: 15 priority successes/day | All 20 attempts used, historical success: ≈12.18 priority successes/day | Historical realized output: ≈7.39 priority successes/day |
| --- | ---: | ---: | ---: |
| 25 | 2 days | 3 days | 4 days |
| 50 | 4 days | 5 days | 7 days |
| 100 | 7 days | 9 days | 14 days |
| 200 | 14 days | 17 days | 28 days |

These are capacity scenarios, not recurring service guarantees. The middle column
requires actually consuming all 20 attempts; it does not remove the failure stop.
Recent realized priority capacity is roughly **22 ads per three days, 51 per seven,
or 103 per fourteen**, before churn, retries and competing scopes. A 24-hour due
guard can also make a later-in-run check ineligible when the next day's queue forms.

Optional narrower-scope targets, **not selected or activated**:

- **25 ads / seven days:** start here; aim for at least 90% successfully checked
  within seven days, with failed/unknown outcomes visible and useful headroom.
- **50 ads / seven days:** close to recent capacity; use a 10–14-day fallback if
  freshness deteriorates or candidate/newcomer competition grows.
- **100 ads / fourteen days:** a realistic wider research scope; do not promise
  seven-day direct freshness until measured capacity supports it.

Price sightings can be fresher when relevant ads reappear in discovery. Measure
price and direct availability freshness separately.

The **320-day** calculation is an ideal *single pass over a frozen backlog*:
6,388/20, without failures, additions or repeats. Full utilization at the historical
success ratio implies **394 days**; realized output implies **649 days**. Five
aging slots alone imply at least 1,278 perfect-success days for that static backlog.
Meanwhile discovery added **232 database IDs, about 33/day**, exceeding even the
20/day perfect-success capacity. Checking every new discovery once would grow a
queue by roughly 13/day at perfect success or 23/day at recent output, before
revisits. This is database discovery churn, not new market supply or sales.
Prioritization cannot make all historical records current.

## Fairness, duplicates and obsolete/malformed records

The installed policy deduplicates IDs, honors due/excluded records, groups uncertain
labels into one review pool, rotates models within ordered priority lanes, and
protects every fourth nominal slot for oldest attempts. Failures advance attempt
rotation but not successful freshness; new search sightings cannot reset an old
unattempted record's age. Its prior no-source comparison selected 20 model groups /
17 stale ads versus 15 / 10 previously—allocation, not successful live evidence.

Code-inspection limits: saturated candidates can dominate followed-model lanes;
day-based model rotation is not a persistent per-cohort service guarantee; small
groups can be revisited while others remain sparse; two early failures may stop
before any aging slot. Measure actual protected slots reached, unique ads/models,
longest eligible wait and candidate/followed shares before changing policy. If
sustained starvation appears, plan weighted lane shares and a persistent cursor,
not an automatic traffic increase.

The audit found **zero malformed numeric IDs, duplicate
(listing_id, observed_at, source_query) groups or orphan snapshots**. It found
30 years outside 1990–2026, 18 mileages outside 0–500,000 km, one missing mileage,
76 missing fuels, 521 missing/unusable prices and 492 unrecognized model labels.
Use review flags and explicit analytic exclusions, not deletion. Labels such as
XC 60/XC60 can fragment samples; preserve originals and confidence before merging.

**3,554 ads have the legacy inactive flag, but zero have a direct terminal status.**
Moving-window absence cannot identify obsolete ads or sales. Another 528 website
states say listed after discovery without successful direct checks; label them as
last seen in search. Keep retained history separate from a freshness-defined
research cohort rather than presenting every retained row as current inventory.

Of 9,868 repeat observations, **9,090 (92.1%)** repeat the preceding price/status.
These are valid repeated sightings, not extra independent cars or sales. Summaries
need distinct ads and one appropriate observation per period; observed price changes
among matching ads must be separate from sample-membership changes. Preserve unchanged
observations rather than deleting them to improve productivity metrics.

## Prioritized stages and gates

Effort is engineering time for this stack, not elapsed time or a commitment.
Source access and uncertain failures can dominate.

| Stage | Work to commission | Success measure / gate | Effort and dependencies |
| --- | --- | --- | --- |
| 1. Baseline/observability | Reconcile run 21 separately from the earlier baseline; after 15 October's existing run, reconcile actual direct checks, budgets, priority selection, outcome and hosted notice. Report per-model ages, distinct prices, unknown fields and reached aging slots. | Counts reconcile; zero direct attempts are not treated as a successful queue test; dated scope and denominator on every metric. | 0.5–1 day, then 7–14 days of scheduled evidence. Depends on the permission-repair owner and next-run verifier. |
| 2. Reliable bounded checks | Assess installed browser diagnostics and DB failures. Fix demonstrated readiness/parser gaps with offline fixtures and existing limits; validate on regular runs. | Provisional ≥90% success over ≥100 actual attempts and ≥90% utilization on nonpaused days. Report pauses separately; do not force runs for the target. | 1–3 days if reproducible; refusals may remain external. |
| 3. All-model/stale rotation | Keep all models eligible without new buying filters; assess actual model coverage, oldest wait, candidate/followed pressure and structured enrichment from the same checks. Add per-model coverage reporting only in a later authorized implementation step. | Broad allocation is visible; success shares and sample gaps are dated; no universal seven-day freshness promise; saved baselines intact. | 0.5–1 day for analysis; 1–3 days if reporting/fairness needs changes; two-week observation window. |
| 4. Targeted/deeper discovery | Validate model partitions fairly across the all-model research scope, including page identity/clamping, boundary/unknown-field inclusion and overlap. Any reallocation of an existing discovery visit requires validated filters and measured tradeoff against global discoveries. | Explicit complete/partial/truncated pass labels; reproducible distinct IDs and per-model gains. Changing totals alone never prove completeness. | 2–5 days for bounded partition feasibility after access decision; broader traversal 1–2 weeks or external agreement. Not activated here. |
| 5. Longer-term access/cadence | Choose coverage versus freshness, then size permitted access, browser/runtime costs, database/storage and independent recovery. Budget and frequency are separate decisions. | Sustainable useful yield/success/pause rates, bounded cost and restore coverage, explicit source allowance. | 0.5–1 day sizing after measurements; negotiation/implementation uncertain. |

Thresholds are proposed operational goals, not proven capability or guarantees
against systematic sampling bias.

## Hypothetical expansion and source-access uncertainty

Conditional on consuming every attempt with historical 81.2% success:

| Daily attempts | Conditional successes/day | Priority ads per seven-day window at 75% share |
| --- | ---: | ---: |
| 20, existing | 16.2 | 85 |
| 40, hypothetical | 32.5 | 170 |
| 100, hypothetical | 81.2 | 426 |

At historical utilization as well as success, capacities would be about **51, 103
and 258**, if behavior scaled linearly—unproven. More traffic may cause more failures
or refusals. Keeping all 6,406 retained ads successfully checked within seven days
needs roughly **916 successes/day**, about **1,128 attempts/day** at the conditional
ratio, before churn/revisits. This is not a reasonable browser-pilot extension
without supported bulk access and a new objective. More frequent newest-window
checks cannot reach excluded older ads; distributing the same budget more often
creates no additional capacity. No expansion is activated.

Before any increase, measure 7–14 days of actual utilization/successes, refusals,
pauses, failure stages/latencies, useful new scoped IDs, unchanged-price repeats,
missing fields, queue-age percentiles, discovery overlap, source-to-UI delay,
CPU/disk and backup growth. Determine whether access, reliability, scope, attributes
or prioritization is the bottleneck. Obtain permitted endpoints/credentials,
request/concurrency limits, filter/pagination guarantees, reuse conditions and
historical export availability. An approved change needs cumulative per-source
budgets across schedules, locks, bounded failures, provenance tests and rollback.

The [official OLX authentication guide](https://api-documentation.olx.ba/authentication)
requires authenticated requests. Its [listing API documentation](https://api-documentation.olx.ba/listings)
describes token-protected single-listing retrieval and management. These documents
do not establish authorized market-wide historical export or broad search-feed
access for this project. No bulk-access agreement or larger allowance is evidenced.
Ask OLX about a supported scoped feed/export or partnership before planning a census
or retrospective archive. No CAPTCHA bypass, source-control workaround or automatic
scraping scale-up is proposed. Documentation was read on 14 October Sarajevo time;
no listing/search endpoint was visited.

## History, recovery and product claims

Keep immutable observations and query/date provenance. The documented 30 September
recovery restored 139 deleted listings/239 observations, not all potentially earlier
lost history. The June-start archive cannot establish a complete earlier six-month
history. Inventory old backups read-only before any separately reviewed recovery.

The direct pipeline has no active history-cleanup step; its collector role cannot
delete evidence. Legacy cleanup code and cascading snapshot deletion remain a
privileged-maintenance risk, not a request to execute them. Backup rotation differs
from deleting database history. Full schema/data recovery hooks have verified
13 October restore evidence and a one-time independent Mac copy; recurring off-VPS
copying and the next complete scheduled backup cycle remain separate deferred work.
Any later compaction must preserve original sightings/prices/status evidence,
Following baselines, candidate state and tested recovery.

The UI may describe asking-price evidence for **N distinct ads in a named scope
and period**, dated source/direct outcomes, unknown fields and bounded/unknown
coverage. Show latest sightings, successful direct-check dates and clean status
separately. Useful partial observations do not silently advance the clean anchor.

It should not claim current inventory from all retained ads, representative complete
model prices from sparse samples, demand, confirmed sales, realized prices, profit
or days-to-sell from first/last-seen or source dates. Source-reported unavailable/ended
is not an independently confirmed transaction. OLX's [status guidance](https://pomoc.olx.ba/hc/bs/articles/10246780442258-Vrsta-ogla%C5%A1avanja-i-status-oglasa)
describes renewal dates, expiry and hidden/ended states; [ending-an-ad guidance](https://pomoc.olx.ba/hc/bs/articles/10249077684626-Kako-zavr%C5%A1iti-prodaju-oglasa)
explains removal from search. Those states do not establish sale price or every
reason for disappearance.

## Reproducibility and handoff

- `docs/coverage-audit.sql`: existing baseline query, rerun with the exact UTC
  timestamp above; separates sightings, direct checks, first-seen and scope.
- `docs/coverage-planning-audit.sql`: new read-only supplement, successfully executed
  on canonical Supabase; supplies age/field/model/cohort, daily utilization, repeat
  price and integrity aggregates. No database mutation statements.
- Private JSON: `.private/coverage/baseline-planning-20261014.json` and
  `.private/coverage/planning-planning-20261014.json` in this managed worktree,
  mode 600 and excluded from Git. Filenames use the local planning date; as-of
  fields use UTC 13 October. No secret or full private table export was needed.
- `docs/COLLECTION_COVERAGE.md` records installed `priority-b6e94ff`; browser and
  canonical-backup handoffs retain their separate validation boundaries.

Only this plan and supporting SQL were added for the follow-up; the latest update
changes this document only. No Git mutation, runtime or GitHub integration occurred
during the exclusive permission-repair window. Main should integrate the plan in
its serialized step, preserving the confirmed **all-model** scope and existing
source limits. After **15 October, 09:17 Sarajevo**, use actual direct-check output
to assess broad rotation; any larger-budget/access scenario remains a separate,
explicit future decision.
