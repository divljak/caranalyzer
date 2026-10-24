# Daily ingestion validation — 24 October 2026

The daily collector was **disabled**, not successfully finding zero new ads. The
timer journal records its last stop at 14 October 15:54:54 UTC. Supabase contained
no subsequent runs or observations until this recovery. The journal establishes
when it stopped; it does not establish who disabled it. The earlier handoff's
claim that the next daily run would continue was incorrect. The existing health
checker correctly recorded `schedule_disabled`, missed collection and stale backup
incidents. Previously disabled chat follow-up messages remain disabled.

## Recovery and measured result

Read-only preflight verified the actual collector/backup logins, repaired priority
query, no active source pause, original runtime hashes and backup checksums. The
existing export disk reserve passed. The original persistent timer was enabled
at 11:01 UTC, producing **one catch-up run**, not ten replayed daily runs. Its daily
schedule stays **07:17 UTC**; the next ordinary run is **25 October, 08:17 Sarajevo**.
No source budget, refusal pause, frequency or request allowance was increased.

Run 22 used the original `priority-b6e94ff` release. It stored **73 new IDs and 78
immutable observations**, comprising 77 discovery observations and one direct ad
observation. Independent repeatable-read queries matched these counts to actual
rows and matched all three check records to the run. Final totals:

| Evidence | Rows |
| --- | ---: |
| Listings | 6,747 |
| Immutable observations | 16,696 |
| Direct ad checks | 154 |
| Website runs | 17 |
| Imported enrichment records | 108 |

Three search pages completed. One direct ad check succeeded and two timed out
with `WebsiteDOMTimeout`; the two-consecutive-failure rule stopped further visits.
The run remains **partial**, with 3/3 discovery and 3/20 detail visits consumed.
The remaining 17 visits were left unused. The latest real observation is
24 October 11:04:35.885088 UTC. The clean analysis anchor remains 1 October
17:31:22.610647 UTC. No successful timeout repair is claimed from this run.

The new full archive restored in a network-disabled scratch database with matching
table content, schema/access definitions and sequences; the scratch was removed.
Its bundle is `canonical-20261024T110541814210-after`. The existing Mac copy job
was exercised separately after this recovery. Recovery evidence and row-level
audits stay in private local/server directories, outside Git and public hosting.

## Database health and correctness

Supabase reported ACTIVE_HEALTHY, PostgreSQL 17.11 and approximately 20 MiB database
size. The sampled connection count was 9 of 60, with no idle-in-transaction state
or recorded deadlocks. Security advisors found no issues. All twelve application
tables retain RLS and the restricted app, collector and backup roles; anonymous
table access and retired-sync login remain closed.

The independent audit found no duplicate snapshot groups, orphan evidence,
future observations, missing derived labels or differences between current prices
and their latest snapshots. Full-row checksums confirm preservation of all 16,618
older snapshots, 151 older checks, 108 enrichment records and the source-pause row.
Following and candidate tables were empty before and after; this is not a live
test of nonempty user preferences.

Missing attributes remain explicit. Six of today's 73 new IDs have missing asking
prices; all 73 lack location and transmission in discovery-card evidence. None of
these new rows has a year outside 1900–2027, mileage outside 0–2,000,000 km or a
positive asking price below 1,000 KM. Older evidence includes eight suspect mileage
values and uncertain model labels. These need provenance review, not invented
replacement values or deletion of history.

## Correctness fixes prepared after the catch-up run

Offline reproduction found two latent defects; neither explains today's DOM
timeouts or contradicts today's reconciled counts:

1. A partially loaded search with no pagination could be interpreted as a final
   page and falsely marked clean. Readiness now waits on the same rendered
   document for verifiable cards/pagination. Missing controls prove a single page
   only when distinct valid source identities account for the advertised result
   count. Rejected prices do not erase other valid cards. Completion is based on
   validated pages or a verified terminal result, not the initial unused budget.
2. A listing/snapshot upsert commits before the website-state write. If that later
   write failed, run counters omitted the already saved observation. Counters now
   advance immediately after the committed upsert, so the subsequent failure
   records a partial run with accurate saved counts.

A verified empty discovery is recorded as a completed website search rather than
budget exhaustion; without price observations it still cannot advance the clean
price-analysis anchor. DOM timeout evidence now records stage, elapsed time,
navigation/script retry flags, parser reason and presence/count flags. It does not
retain seller text, contacts or full page HTML. This prepares diagnosis on the next
normal run without revisiting failed ads today.

Regression tests reproduce the former failures, preserve valid single-page and
empty results, retain valid ads beside rejected prices, and check partial-write
count reconciliation. Packaged Chromium validation uses only loopback fixtures
with external networking disabled. The fixture now contains one visible source
card for its one advertised result, consistent with the stronger readiness rule.
The fixture navigation timeout is one second against a three-second deferred
asset, allowing its document to arrive on a busy VPS while still proving a real
navigation timeout, one navigation and DOM recovery. An earlier 0.2-second harness
deadline sometimes fired before the fixture document arrived; production timeouts
and readiness assertions were not weakened.

Final relevant coverage comprises 81 passing tests, including both real Chromium
fixtures; the separate PostgreSQL advisory-lock test was skipped because no
disposable integration database was supplied. Sixty focused local cases also
passed. Test runs made no OLX requests and inserted no canonical evidence.

## What still needs real evidence

The repaired ordinary timer must actually fire on the next scheduled day. This
catch-up run proves current discovery and canonical persistence, not future daily
uptime or restored data for every missed day. Browser detail reliability remains
unresolved; future failures now have useful safe diagnostics. Preserve partial
status, failure stops and real freshness while investigating them.

Three newest-result pages and twenty permitted direct checks per day provide a
bounded sample. They do not cover every new OLX car, refresh all historical ads,
measure transactions or establish sales speed. UI-value work should use these
limits explicitly after sustained daily ingestion is demonstrated.
