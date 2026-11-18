# Twice-daily collection

Approved by Ogi on 18 Nov 2026 from the coverage analysis: **two collection runs a day about 12 hours
apart**, 3 search pages each, 20 detail checks per day in total, at least 15 s between visits (at most
4 requests a minute), the 24 h pause on 403/429/captcha, and a hard ceiling of 10 search pages and
50 detail checks a day. The Telegram digest (Vercel cron 09:45) is unchanged.

## Budget rules (`database/collection_budget.py`)

| | Per run | Per UTC day | Hard max |
| --- | --- | --- | --- |
| Search pages | 3 | 6 | 10 |
| Detail checks | morning 10; evening = what is left of the day | 20 | 50 |
| Spacing | ≥ 15 s | | |

- Search: every run may read 3 pages, the day stops at 6. A morning run that fails after one page spends one,
  so the evening run still gets its own 3. The single page-1 retry is counted like any visit, so a morning retry
  can leave the evening with 2 pages (the cap is a cap).
- Details: morning 10, evening gets the leftover (10 normally, up to 20 if the morning run did little). The
  day total never exceeds 20.
- A manual or `auto` run may use whatever is left of the day, within the per-run limits.
- When a run starts with nothing left it logs a `budget_exhausted` run without starting Chromium.
- The persistent counter is `website_collection_budget` (UTC day). `WebsiteStore.reserve()` takes absolute
  day ceilings and refuses anything past 10 search / 50 detail visits, or spacing under 15 s.
- Settings (env, forwarded by `scripts/github_collector_verification.py` into the container, validated, never
  above the hard max): `COLLECTOR_SEARCH_PAGES_PER_RUN` (3), `COLLECTOR_SEARCH_DAY_CAP` (6),
  `COLLECTOR_DETAIL_DAY_CAP` (20), `COLLECTOR_DETAIL_MORNING` (10), `COLLECTOR_SLOT`
  (`morning`/`evening`/`auto`, set by the workflow). `run_website_sync.py` also takes `--search-day-cap`
  and `--detail-day-cap`.

## Slots in the workflow

- Slot windows split the UTC day at 12:00: **morning** 00:00-12:00, **evening** 12:00-24:00. The morning
  dispatch is 06:15/07:15 UTC, the evening one 18:15/19:15 UTC (CEST/CET).
- Slot is resolved by `scripts/collection_slot.py`: the `slot` input if set, else `evening` when the reason
  contains "evening" (the VPS sends `vps-timer-evening`), else the cron for the `schedule` event
  (`40 21 * * *` = evening), else morning.
- Fallbacks: `40 9 * * *` (morning) and `40 21 * * *` (evening). A fallback exits at once only if a real,
  non-verify run **succeeded inside its own slot's window today**. Dispatches always run (the concurrency
  group and the budget keep a duplicate harmless). Verify runs and failed runs never count.
- A manual run after 12:00 UTC counts as the evening run.
- Health: `collection-health.yml` checks each slot (10:30 UTC morning, 23:15 UTC evening) with
  `collection_alert.py stale --slot ...`; `scripts/collector_health.py` judges `schedule_missed` per slot.

## VPS change (prepared, NOT applied)

Apply after this PR is merged to `main` (the evening reason is harmless on the old workflow, it just runs as
a normal collection, but the budget caps only change once the workflow image is built from the new code).
`deploy/trigger/` in the repo already contains the new files.

Timer diff (`car-analyzer-dispatch.timer`):

```diff
-Description=Daily 08:15 Europe/Sarajevo trigger for the GitHub Actions collection
+Description=Twice-daily (08:15 and 20:15 Europe/Sarajevo) trigger for the GitHub Actions collection
 [Timer]
 OnCalendar=*-*-* 08:15:00 Europe/Sarajevo
+OnCalendar=*-*-* 20:15:00 Europe/Sarajevo
```

Service diff (`car-analyzer-dispatch.service`):

```diff
-ExecStart=/usr/local/bin/car-analyzer-dispatch collect
+ExecStart=/usr/local/bin/car-analyzer-dispatch collect auto
```

Script (`/usr/local/bin/car-analyzer-dispatch`): new optional 2nd argument `auto|morning|evening`. `auto`
uses the Europe/Sarajevo clock (before 14:00 morning, reason `vps-timer`; from 14:00 evening, reason
`vps-timer-evening`). It sends no new workflow input.

Commands on the VPS, as root:

```bash
# from a checkout (or copy of deploy/trigger/) taken from the merged main:
deploy/trigger/install-dispatch-trigger.sh   # installs script, service, timer; daemon-reload; enable --now
systemctl list-timers car-analyzer-dispatch.timer --no-pager   # expect 08:15 and 20:15 Sarajevo
car-analyzer-dispatch verify                                   # token + API check, no OLX visits
```

Roll back: restore the previous timer/service from git history and re-run the installer, or
`systemctl disable --now car-analyzer-dispatch.timer`. The schedule fallbacks (09:40 and 21:40 UTC) keep both
slots covered even if the VPS does nothing.

## Tests

`python3 -m unittest tests.test_collection_budget tests.test_collection_slot tests.test_website_sync
tests.test_github_collector_verification tests.test_collector_health tests.test_collection_alert`.
