# Daily collection (GitHub Actions)

From 15 November 2026 the daily OLX collection runs on **GitHub Actions** and writes
directly to Supabase (`ojmokmsjppkqwppmrzpv`) with the restricted
`car_analyzer_collector` role. The DigitalOcean VPS no longer visits OLX and needs no
database. It only sends one API call each morning.

```
VPS  car-analyzer-dispatch.timer  08:15 Europe/Sarajevo
 └─ /usr/local/bin/car-analyzer-dispatch collect
     └─ POST /repos/divljak/caranalyzer/actions/workflows/daily-collect.yml/dispatches
GitHub Actions  .github/workflows/daily-collect.yml  (ubuntu-24.04, ≤40 min)
 ├─ build Dockerfile.collector (pinned Debian Chromium + chromedriver)
 ├─ loopback test of the PIK markup in that Chromium (no network)
 ├─ scripts/github_collector_verification.py --collect
 │   └─ container: scripts/direct_collector.py → run_website_sync.sync()
 │       3 search pages + 20 detail checks, ≥15 s apart → Supabase
 └─ artifacts: diagnostics (public, counts/timings only), encrypted recovery snapshot (age)
Fallback  schedule 09:40 UTC: exits at once if today's daily run already succeeded
```

## Secrets

| Where | Name | What |
| --- | --- | --- |
| GitHub repo secret | `COLLECTOR_DB_PASSWORD` | `car_analyzer_collector` password (session pooler) |
| GitHub repo secret | `BACKUP_DB_PASSWORD` | `car_analyzer_backup` read role, used for before/after snapshots |
| VPS `/etc/car-analyzer/github-dispatch-token` (root, 600) | fine-grained PAT | Repository access: **only** `divljak/caranalyzer`; permissions: **Actions: Read and write** (Metadata: read is added automatically). Nothing else. |

The repository is **public**. Workflow logs and the diagnostics artifact contain only
counts, timings and public OLX URLs. The recovery snapshot is age-encrypted to
`deploy/recovery/car-analyzer-backup-recipient.pub`.

## Running it by hand

```bash
gh workflow run daily-collect.yml -R divljak/caranalyzer -f reason=manual            # real collection
gh workflow run daily-collect.yml -R divljak/caranalyzer -f mode=verify -f reason=check  # no OLX visits
# on the VPS
sudo car-analyzer-dispatch verify     # tests the token + API without collecting
```

The daily budget is persistent in Supabase (`website_collection_budget`, UTC day): 3
search visits (+1 only for the page-1 retry) and 20 detail checks. A second run on
the same UTC day uses only what is left. When nothing is left, it records a
`budget_exhausted` run without starting Chromium.

## Failure handling

- **Partially hydrated search page: wait, don't reload.** A search page has 20 s to
  show its full contract: result cards, pagination and the "N oglasa" count. If the
  cards are already on screen at 20 s and only the count (or pagination) is missing,
  the collector keeps polling the **same loaded page** for up to 45 s in total
  (`WebsiteBrowser.SEARCH_HYDRATION_TIMEOUT`). This makes no new request and spends no
  budget. Every poll still checks for 403/429 and captcha text. A page without cards,
  and every detail page, keep the 20 s deadline. A page that never shows its count is
  still a `WebsiteDOMTimeout` and nothing from it is saved. (17 Nov 2026, run
  44037259559: 50 cards and pagination, HTTP 200, no count after 20 s, twice.)
- **One bounded, delayed retry.** If page 1 still times out (navigation or DOM) or the
  renderer fails, Chromium is restarted at once (which closes the stalled OLX tab). The
  collector then waits **3 minutes** (`PAGE1_RETRY_DELAY`) and reads page 1 once more.
  That is at most one extra page load per run, still counted and spaced ≥15 s.
  A 403/429/challenge is never retried: it pauses the source for 24 h. Neither is a
  parser rejection or a later page. In the worst case the delay and waits add about
  4 minutes to a run.
- **Telemetry.** Each failure is in `website_collection_runs.report → failures[]`:
  phase, url, page, attempt, exception, navigation_ms, dom_ms, final_url, title,
  ready_state, document_status, last_dom, parser_reason, dom_fingerprint,
  hydration_wait (the 45 s wait was used) and, on the attempt that was retried,
  retry_delay_s. For search pages `last_dom` also has `result_heading_present` and
  `priced_cards`. (`price_values` and `title_present` are detail-page fields and are
  always 0/false on search pages.) The same data is in the run's job summary and the
  `daily-collection-diagnostics-<run id>` artifact.
- **OLX markup changes.** OLX relaunched as PIK on 10 Nov 2026 and every run failed
  until the parser learned the new markup. If `failures[].parser_reason` is
  `search_cards_or_pagination_not_verified` with `document_status: 200`, the layout
  has probably changed again. Fetch one page, compare it with
  `tests/test_browser_loopback.py::PIK_SEARCH`, and update `EXTRACT_SCRIPT`.

## VPS trigger install

Already staged on the VPS (14 Nov 2026): `/usr/local/bin/car-analyzer-dispatch`,
`/etc/systemd/system/car-analyzer-dispatch.{service,timer}` and copies in
`/opt/car-analyzer-trigger/`. The old `car-analyzer-collector.timer` is disabled; its
units are kept. Remaining steps (see NEXT.md):

```bash
( umask 077; cat > /etc/car-analyzer/github-dispatch-token )   # paste the token, then Ctrl-D
car-analyzer-dispatch verify                                    # end-to-end check, no OLX visits
systemctl enable --now car-analyzer-dispatch.timer
systemctl list-timers car-analyzer-dispatch.timer               # next: 07:15 UTC = 08:15 CET
```

From a fresh checkout, `deploy/trigger/install-dispatch-trigger.sh [--disable-old]`
does all of this except creating the token.

GitHub disables `schedule` triggers after 60 days without repository activity. The
VPS dispatch does not depend on that.
