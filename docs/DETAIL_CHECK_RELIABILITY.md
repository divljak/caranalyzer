# Browser/detail-check reliability — 13 October 2026

This repair extends the completed direct-write cutover in PR #4. The runtime change
is limited to `scrapers/olx_website_collector.py`; scheduling, database roles,
credentials, discovery rules, daily budgets and run-status logic remain owned by
the existing direct collector. Tests and this handoff are the other task files.

## Evidence and diagnosis

Before requesting any source pages, this task inspected the saved canonical
application snapshot, the VPS service journal, browser code, run records and direct
cutover handoff. The snapshot contained 22 `TimeoutException` detail failures and
seven `price_not_verified` parser failures. The morning 13 October journal recorded
Chromium 154's “Timed out receiving message from renderer” during navigation.
The 16:48 run recovered two discovery pages but both detail attempts timed out.
The 17:49 cutover run produced 13 successful listed checks and three timeouts among
16 attempts, then stopped after its final two consecutive failures. No failed check
established removal or sale.

The old saved records retain exception classes, not failure-stage DOM/network
captures. They therefore cannot prove which missing field or resource caused each
failure. Eager navigation had already demonstrated successful real detail checks
in the cutover run. A remaining code defect was independently reproduced: readiness
accepted a visible ad ID before title, manufacturer/model and current price were
ready. That could turn a partially hydrated page into a premature parser failure.
No new OLX page was requested for diagnosis or validation by this task.

## Repair

- Retain eager navigation and the existing 30-second navigation timeout. A timeout
  can recover the existing rendered document; it never reloads the source page.
- Detail readiness now requires the full parser contract: expected HTTPS OLX URL,
  matching labeled ID, title, manufacturer/model and a single verified current
  price or explicit on-request price. Search behavior and discovery parsing stay
  unchanged.
- DOM extraction uses Selenium's async callback API so the five-second script
  timeout applies. Up to two failed extraction retries are allowed within the
  existing 20-second DOM polling wait. These are reads of the same DOM, not extra
  navigations. Each actual page visit is reserved once by the existing persistent
  budget before navigation. There is no automatic page-request retry.
- Deadline or repeated renderer failure raises `WebsiteDOMTimeout`, recorded as
  an ordinary failed check. WebDriver transport failures are also failures; neither
  supplies negative availability evidence.
- Exact negative status templates require two consistent DOM reads. Conflicting
  unavailable/ended/sold messages fail parsing. Seller titles and redirects cannot
  assert status. Unfamiliar unavailable templates still fail; these fixtures do
  not establish that actual removed ads use the tested wording.
- HTTP 403/429, browser challenges and the persistent pause remain authoritative.
  A refusal anywhere in a drained response batch takes priority over a preceding
  server error. Refusal checks run before extraction, during retries and after
  readiness. No challenge handling or access workaround is added.
- Detail extraction skips recommendation-card parsing. Setup failures and exit
  always attempt both browser quit and driver-service stop. Cleanup errors log
  exception class only, without masking the original collection failure.
- Successful check evidence adds only `browser.navigation_timeout`,
  `browser.script_retries` and `browser.elapsed_ms` to the existing JSON evidence.
  No schema, grant, run-summary or freshness change is required.

The existing two-consecutive-failure stop and 15-second inter-visit delay remain
unchanged. DOM polling does not spend another page visit; a future implementation
that actually retries navigation must reserve another visit through the existing
store and honor pauses/failure stops. The wait deadline does not replace systemd's
25-minute bound on a hung driver/process.

## Validation

The packaged VPS collector image ran 45 tests: **44 passed, one skipped**. The
skipped existing test requires a separate loopback PostgreSQL audit database and
checks direct-collector advisory locks; this repair does not change that code.
The passing tests cover navigation/renderer recovery, delayed price/labels, wrong
identity, script-retry limits, deadline failure, transient negative headings,
conflicting status, HTTP/challenge refusal priority, transport failures and cleanup.
Database-fixture tests verify that recovered DOM consumes exactly one persistent
visit and appends one observation, two failures consume two visits then stop,
previous listed status/history survive, refusals preserve the pause, and incomplete
discovery cannot advance the clean anchor.

Two tests used actual packaged Chromium with an HTTP server bound to loopback
inside a `--network none` container. A delayed local script reproduced navigation
timeout; the existing DOM was recovered with one fixture navigation. The configured
async timeout was exercised, extraction resumed, the local fixture was rejected as
real listing evidence, and the driver subprocess stopped. These tests make no OLX
requests and write no collector evidence. Mock fixtures reproduce unsafe paths;
they do not claim that a particular live timeout was resolved.

Installed runtime source commit: `1327757`.

## Installed release and preservation checks

Installed at **13 October, 18:53:13 UTC** (20:53 Sarajevo) after reading the finished
cutover handoff and confirming the collector service/container were stopped.
`.daily-delivery/direct-current` now atomically selects
`/opt/car-analyzer/.daily-delivery/releases/details-1327757`.
The prior `/opt/car-analyzer/.daily-delivery/releases/direct-20261013-final`
remains untouched for direct-write rollback. No service/timer configuration,
credentials, grants, database schema or active scheduler was modified.

The deployment held the existing `.daily-delivery/pipeline.lock`, rejected active
service/collector processes, verified SHA256 of all 18 prior release files against
cutover source `3bb5a10`, copied that complete release, and replaced only the browser
module in the new inactive directory. All final runtime hashes matched. The private
manifest is `.daily-delivery/detail-reliability-installation.json`; its
`source_commit` is `1327757`, its `file_sha256` maps the full combined runtime, and
its `live_validation` explicitly remains pending. All other files retain the
cutover's hashes. Tests/staging and local diagnostics live under permission-restricted
`.daily-delivery/staging/details-20261013` and this worktree's
`.private/detail-reliability/`; neither contains published credentials.

The direct runner's existing `--verify-only` operation used the collector/backup
roles, locks and real rolled-back operations without source visits or fabricated
listing evidence. The first preflight recorded `OperationalError` with no SQLSTATE
before a backup/verification result; installation stopped without changing the
release. One-shot checks of both role connections subsequently passed. Final
verification at **18:52:58–18:53:12 UTC** exited 0, and all 12 before/after full-table
checksums matched. The connection failure's precise cause was not retained; this
browser task did not alter connection/retry settings or credentials.

Final verified canonical counts remain **6,612 listings, 16,514 observations,
151 checks, 131 scraping logs and 15 website runs**. The 13 successful checks today
were made by the earlier cutover run, not this repair. The shared budget remains
**3/3 discovery and 18/20 direct**, with no website-budget pause. The old real
`latest-direct-attempt.json` remained byte-for-byte identical through installation.
Only isolated verification markers/backups advanced. Systemd retains the real
partial run's `ActiveState=failed`, `Result=exit-code`, exit status 1.

The timer remains enabled/active at **07:17 UTC daily**, with the next trigger
verified as **14 October, 09:17 Sarajevo**. No live OLX run followed installation:
today's real run already reached its required consecutive-failure stop, so the
remaining two attempts were left unused. **Zero successful real detail checks are
yet attributable to this follow-up repair.** The next scheduled day must establish
that result; offline tests alone cannot.

For rollback, acquire the same pipeline lock and confirm no collector/service is
running, then atomically point `direct-current` back to the retained
`direct-20261013-final` release. Keep the canonical Supabase credentials and timer.
Do not enable legacy local collection/sync or restore a database for a browser
rollback. Use the cutover handoff's broader recovery rules for any database issue.

## Independent next-run verification handoff

Main should provide this handoff to “Verify the next daily Supabase collection”.
That verifier's old baseline release path must be updated deliberately to
`details-1327757`, rather than treating this authorized code change as unexplained
drift. No new run or fresh evidence exists from installing it.

After the actual **14 October, 07:17 UTC** timer run finishes, compare the service
journal/result, `latest-direct-attempt.json`, Supabase run/check records and private
hosted overview. Count actual `listed` and recognized unavailable checks separately
from `check_failed`/`blocked`. A successful matching direct check from this release
will carry the three `browser` diagnostics in its JSON evidence; inspect them to
separate navigation-timeout recovery from script retries and ordinary reads.
Repeated renderer/DOM failure will record `WebsiteDOMTimeout`. Require observations
and checks to reconcile with the run's counts. Preserve the previous clean anchor
unless that real run qualifies as completed under the existing rules; partial runs
and source pauses still remain partial/paused.

Pay particular attention to source refusals, repeated missing prices/templates,
consecutive failures, daily counters, coverage backlog and orphaned containers.
The existing bounded sample cannot establish full-market coverage, sales or demand.
Follow the installed direct contract for collection, credentials and backups; do
not spend another visit or reset budgets just to turn a verification green.

Implementation uses [Selenium eager navigation](https://www.selenium.dev/documentation/webdriver/drivers/options/),
[explicit waits](https://www.selenium.dev/documentation/webdriver/waits/) and
[the async script timeout contract](https://www.selenium.dev/selenium/docs/api/py/selenium_webdriver_remote/selenium.webdriver.remote.webdriver.html).

## Shared integration boundary

Runtime edits are finished. The only changed runtime file is
`scrapers/olx_website_collector.py`. Other task files are
`tests/test_website_navigation.py`, `tests/test_browser_loopback.py` and this document.
`run_website_sync.py`, `database/website_tracking.py`, direct entry scripts,
selection/queue code, canonical archive/backup code and service/timer files were not
edited. The priority/coverage owner can now integrate at the collector selection
boundary, and the backup owner can extend the canonical pipeline, preserving the
new browser module when building the next complete immutable release. Use the
installed manifest to retain the exact combined runtime; this task holds no ongoing
shared file/service mutation.
