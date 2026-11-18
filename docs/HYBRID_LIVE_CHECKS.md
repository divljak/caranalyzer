# Hybrid model: daily collection plus capped live checks

Design note, 18 Nov 2026. **Proposal only: no code, schema, env, Vercel or VPS change is made by this
document, and no OLX page was visited to write it.**

## Decision

Ogi asked whether Garaža could query OLX live per question instead of keeping a database. Agreed answer:
keep the collector as the base (twice daily, 3 + 3 search pages, 20 detail pages per day, 15 s spacing,
24 h pause on 403/429/captcha) and add a **small, strictly capped live check for one listing a user is
looking at**. Live checks never crawl search and never replace history.

## 1. Which flows get a live check

| Flow (today) | Live? | Why |
|---|---|---|
| A listing the user is looking at: "je li još aktivan / još ta cijena?" (new **🔄 Provjeri uživo** button on a result row, a saved car from `c:`/shortlist, or a pasted `olx.ba/artikal/ID` link) | **Yes, one detail page** | The DB row says "last seen 9 days ago ⚠" (`_age`, `FRESH_DAYS = 14` in `telegram_bot.py`). One page answers the question exactly. |
| "Pokaži još" (5 rows per page, `Show more`) | **No automatic check** | 5 rows = 5 pages × 15 s ≈ 75 s and 5 budget units per tap. Keep the ⚠ marker and let the user tap Provjeri uživo on the one row they care about. |
| `/status`, `/latest`, `/trends`, "šta je novo" | DB only | They describe the collector's own history. |
| `compare`, `model_stats`, `liquidity`, trends, shortlist/`deals`, `price_check` (price fairness) | **DB only** | They are medians and counts over up to 90 days of listings and snapshots (`listing_snapshots`). A live read would need dozens of pages (impossible at 15 s spacing), gives one biased sample, and has no history. |
| Price fairness for **one** ad | Hybrid | Live-check the one ad's price, compare it to the DB median. The median stays DB-only. |
| "Is it sold?" | Never inferred | A single "unavailable" is only `unavailable_pending`; `removed_unconfirmed` needs 3 checks over 72 h (`record_check`). Copy says "nije dostupan", never "prodan". |

The Gemini layer is unchanged: it plans and phrases from DB answers. Live-check replies use fixed
`telegram_i18n.py` strings, not model text, so no model can invent a status or price.

## 2. Mechanics

- **One detail page per check, reusing the existing code.** `WebsiteBrowser.read(url, 'detail')` then
  `parse_detail`, with `https://olx.ba/artikal/{id}` for a numeric id already validated by
  `_valid_listing_id`. No search URL, no pagination, no "similar ads" parsing.
- **Shared budget in `website_collection_budget`** (single row, `reserve()` counts the attempt *before*
  navigation, failed visits count). Add a separate `live_visits` counter so live checks cannot eat the
  collector's 20 details (`remaining_details = detail_checks - detail_used` in `sync()`).

  | Limit | Proposed |
  |---|---|
  | Live checks per day, all users | **20** (raise to 25-30 only after a quiet week) |
  | Per user per day | **5** (per-listing: 1 per cache window) |
  | Combined detail pages (collector 20 + live 20) | 40, under the existing hard ceiling of 50 in `reserve()` |
  | Spacing between *any* two visits, collector or live | 15 s, via the shared `last_visit_at` (the existing floor; `min_spacing < 15` is rejected) |
  | Concurrency | One browser at a time. Live runs take the same advisory lock (`pg_try_advisory_lock(731400220)`); if the collector holds it, the answer is the DB fallback, not a wait. |

- **Cache in Supabase, so repeats do not re-hit OLX.** A live result is stored as a normal
  `website_listing_checks` row plus a snapshot (section 5). Before any visit the runner looks up the newest
  successful check for that listing: younger than **6 h** (price/active) is returned as "provjereno prije N h".
  Two people asking about the same ad cost one visit. Nothing new is needed for the cache itself.
- **Request ledger (needed for per-user limits).** The bot's `car_analyzer_app` role is SELECT-only
  except `dealer_model_searches` and shortlist, and serverless memory is not shared, so per-user counts
  cannot live in process. Add `live_check_requests (id, listing_id, requester_hash, requested_at, status,
  result_check_id)` with INSERT for the bot role (migration to be applied by Ogi, same pattern as
  `docs/proposed-migrations/`). `requester_hash` is a salted hash of the Telegram user id, not the id.
- **When the budget is exhausted, the source is paused, the lock is busy or the user is over their limit:**
  no visit; answer from the DB with the honest age, e.g. "Danas je limit provjera uživo potrošen. Zadnje
  viđen: prije 9 dana ⚠, 14.500 KM." The button stays, text says when it resets (next UTC day, or the
  `pause_until` time).

## 3. Block safety

Identical to the collector, because they share state:

- 403, 429, challenge or captcha (`WebsiteBlocked`, already detected in `check_responses`) → **no retry**,
  record `blocked`, `store.pause(now, 24)`.
- **The pause is global.** `pause_until` is one value, so a block triggered by a live check also stops the
  collector for 24 h. That is intended (a refusal is a signal about us, not about a code path), and it is the
  main reason for keeping live volume small and user-initiated only.
- A timeout or parse failure is `check_failed`: one attempt, no retry, DB answer with "provjera nije uspjela".
  Two consecutive `check_failed` in one runner invocation stop that invocation (existing rule); live checks
  additionally go quiet for 1 h after two in a row.
- Never: proxies, rotating IPs, UA/header changes, stealth plugins, captcha solving, login, or touching
  search pages. The live path must reuse `WebsiteBrowser` unchanged.

## 4. Where it runs

| Option | Fit |
|---|---|
| **Vercel function** (the bot) | **No.** Chromium + chromedriver exceed the Python bundle limit, `vercel.json` caps `maxDuration` at 60 s (a cold browser + 15 s spacing + 30 s navigation timeout does not fit), and a second browser path would skip the validated Docker image. The bot only *requests* and *replies*. |
| **GitHub Action** `live-check.yml` (`workflow_dispatch`, input `listing_id` + `request_id`), recommended first | Reuses the pinned `Dockerfile.collector` image, the same collector DB role, the existing `car-analyzer-collector` concurrency group and `alert-on-failure` pattern. Cost: the daily job builds the image in ~3 min; this one must pull a prebuilt image (GHCR) to be usable. Needs a fine-grained GitHub token on Vercel (Actions: write on this repo only), like the VPS dispatcher already has. |
| **VPS worker** (optional later) | Fastest (a warm Chromium, 20-40 s) but the VPS deliberately holds no DB credentials today. Only worth it if Action latency proves too slow. Ogi's call. |

**Latency.** GitHub Action: realistic **60-120 s**, worst case ~3 min (queue + image pull + Chromium start
+ up to 15 s spacing + page). Too slow to answer inline, so the flow is asynchronous: the bot replies at
once, the Action writes the result, and the bot edits that message (it already replaces its "Looking…"
placeholder) or sends a follow-up after a short DB poll. The runner only writes the check row; the bot does all
Telegram sending.

**Bosnian copy (draft, to go through `telegram_i18n.py`)**

- Start: `🔎 Provjeravam oglas na OLX-u… oko minutu, javim čim bude gotovo.`
- Same: `✅ Oglas je i dalje aktivan, cijena 15.000 KM. Provjereno upravo sad.`
- Changed: `💸 Cijena je promijenjena: bila 15.000 KM, sada 14.200 KM. Provjereno upravo sad.`
- Gone: `❌ Oglas trenutno nije dostupan na OLX-u. Ne znamo da li je prodan; provjerit ćemo ponovo.`
- Cached: `Provjereno prije 3 sata: aktivan, 15.000 KM.`
- Limit/pause: `Provjere uživo su danas iskorištene (ili pauzirane). Zadnji put viđen prije 9 dana ⚠, 14.500 KM.`
- Failed: `Provjera nije uspjela. Zadnje poznato stanje: prije 9 dana, 14.500 KM.`

## 5. Data written back

A successful live check does what a collector detail check already does, so history gains a point for free:

- `db.upsert_verified_listing(result['listing'], observed_at=now)` → new `listing_snapshots` row
  (price, `price_status`, `is_active`), `car_listings.last_verified_at`, `previous_price`,
  `price_change_count`, `last_price_change_at`. `/trends` and `price_check` see it unchanged.
- `store.record_check(...)` → `website_listing_checks` row (evidence JSON, `source_url`) and
  `website_listing_states` update (`last_success_at`, `unavailable_*`, attributes, `renewed_at`).
  Because it sets `next_check_at = now + 24 h`, the collector will not re-check the same ad that day.
- `website_collection_runs` needs a `run_id` (NOT NULL FK). Create one row per runner invocation with
  `source_query = 'live-check'`, `search_limit = 0`. It must **not** create a `scraping_logs` row, and
  `/status`, `collector_health.py`, `collection_alert.py` and the efficiency/coverage reports must ignore
  `live-check` runs, otherwise a live run could mask a failed daily run ("latest run completed").
  Check the freshness note in `telegram_bot.py` (`max(observed_at)` over snapshots) too: detail snapshots
  already count there, so a stalled collector with busy live checks would look fresh.
- Live checks are never treated as sale evidence and never set `is_active = false` on a single miss.

## 6. Risks, open questions, rollout

**Risks**

- A block from a live check pauses the collector for 24 h (shared pause). Mitigated by tiny caps, explicit
  user taps only, no retry.
- The budget table assumes one day and per-run ceilings (`search_limit <= 3`). Twice-daily 3 + 3 needs the
  collector change from Ogi's side; live accounting must follow whichever semantics (per UTC day) he picks.
- PIK/OLX markup changed on 10 Nov; a live check is another consumer of `parse_detail` and fails the same
  way (safely, as `check_failed`).
- Added surface: a GitHub token on Vercel, a bot-writable ledger table, cost of Action minutes.
- Abuse via pasted links: allowlisted users only, per-user cap, id must be numeric, one page, no crawling.

**Open questions for Ogi**

1. Is 20 live checks/day, 5 per user, 6 h cache the right start? Raise toward 25-30 later?
2. May Vercel hold a GitHub token limited to dispatching one workflow (and apply the ledger migration)?
3. Is a 1-2 min asynchronous answer acceptable, or is a VPS worker (credentials on the VPS) worth it?
4. Should a pasted link to an ad *not* in the DB be live-checked and added, or answered "nema u bazi"?
5. Should the global 24 h pause also stop live checks (proposed yes) and vice versa?

**Rollout**

| Phase | Scope | Effort |
|---|---|---|
| 0 | Ogi answers the questions; decide budget semantics for the twice-daily collector | 0.5 h |
| 1 | `live_visits` + ledger table + migration, `live_check.py` runner (reuse `WebsiteBrowser`, `reserve`, `record_check`), tests with injected browser (no OLX), exclusion of `live-check` runs from status/health | 1-1.5 days |
| 2 | `live-check.yml` + prebuilt image, dry-run in `verify` mode (rolled-back writes), then 3-5 real checks by Ogi | 0.5-1 day |
| 3 | Bot: button, callback, i18n strings, async reply, DB fallback, pasted-link flow, tests in `tests/test_telegram_*.py` | 1-1.5 days |
| 4 | One week on the 20/day cap, review blocks, latency and usage in `bot_interactions`; then decide on 25-30 or a VPS worker | 0.5 day + a week of watching |

Total build: about 3-4 working days, plus the observation week.
