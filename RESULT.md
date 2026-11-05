# Car Analyzer: current state and concrete next steps

Status checked on **5 November 2026**. No Telegram bot was created, no production
rows or credentials were changed, and the DigitalOcean collector remains enabled.

## Plain answer

The database is **not on Vercel**. Supabase project `ojmokmsjppkqwppmrzpv` is the
canonical PostgreSQL database. Vercel hosts the private Market / Compare / Following
application and reads Supabase through its transaction pooler with the restricted
`car_analyzer_app` role.

The browser collector still runs on the DigitalOcean VPS. It can be moved to GitHub
Actions so DigitalOcean can eventually be cancelled. The safe GitHub runtime and
database verification are now proven, but the collector has **not yet made a real
source visit from GitHub**. Do not cancel DigitalOcean until a controlled shadow run
and single-scheduler cutover are complete.

Telegram is currently **planning only**: there is no bot process, webhook, polling
loop, Telegram dependency, token handling, or digest sender in this repository.

## Current system

| Part | Current location | Verified state |
| --- | --- | --- |
| Canonical database | Supabase Postgres, project `ojmokmsjppkqwppmrzpv` | 7,355 listings and 18,030 listing observations in the latest verified snapshot |
| Private application | Vercel | `dealer_api.py` reads `DATABASE_URL` only through the Supabase transaction pooler using the restricted app role |
| Market / Compare / Following | Existing Vercel UI and deterministic API | Keep for deeper inspection; its queries already expose useful overview, listing, comparison, discovery, model-analysis, history, and following structures |
| Collector | DigitalOcean, `/opt/car-analyzer` | Still the only live scheduler; timer remains at 07:17 UTC |
| Candidate replacement | GitHub Actions | Image build, tests, Supabase roles, rolled-back writes, and encrypted recovery artifact all passed |
| Telegram | Nowhere yet | No implementation exists |

The hosted application is already database-grounded. `hosted_runtime.py` rejects any
host other than the Supabase transaction pooler, requires TLS, and requires the
restricted application role. `dealer_api.py` reads the canonical tables directly.
The bot should reuse the same evidence definitions, not the older generic analysis
code in `main.py` / `api/main.py` that treats `first_seen` as time on market.

## Freshness and data-quality finding

The most recent observed collector run was **5 November 2026, approximately 07:17–
07:21 UTC**. It was partial: 110 observations, 47 listing IDs first observed by our
collector, two completed detail checks, and five errors. The last fully clean
collection anchor remains **1 October 2026**.

Therefore a digest can truthfully say that observations were collected on 5 November,
but it must also label the run **partial**. It must not call the 47 IDs "newly posted";
they are only **first observed by us**. It must not infer that a missing listing was
sold. Trend statements must always include the time window, sample size, and data
freshness.

Useful Phase 1 inputs already exist:

- `website_collection_runs`: latest run status, start/finish, searches, detail checks,
  observations, errors, and structured report;
- `listing_snapshots`: observations and actual observed asking-price transitions;
- `car_listings`: current known listing attributes and `first_seen_at` / `last_verified_at`;
- `website_listing_states`: check outcome and last check time;
- `dealer_vehicle_labels` and `dealer_listing_details`: normalized make/model/category
  and enrichment used by the existing UI.

## Migration work completed

The collector migration changes are merged into `main` through PRs 12, 13, 15, 16,
and 17. The manual GitHub verification run succeeded:

- run: <https://github.com/divljak/caranalyzer/actions/runs/42075741228>
- connected as the intended collector and backup roles over TLS;
- made no source visits;
- rolled back its diagnostic writes;
- left all before/after table counts and checksums identical;
- created an encrypted 1,485,542-byte recovery artifact retained for 30 days;
- downloaded artifact was decrypted locally and all table checksums passed.

Only the public encryption recipient is committed. The matching private Ed25519 key
remains on Ogi's Mac and never entered GitHub. Retain that key after cancelling the
VPS. Supabase is on the Free plan, so these encrypted off-site snapshots currently
replace the managed daily-backup feature that the plan does not include.

The GitHub workflow is still deliberately manual and has no `schedule`. Safe
verification remains its default. A guarded `shadow_collect` mode is now implemented
and tested, but it has not been triggered: it requires a manual dispatch plus the
exact `COLLECT_ONCE` confirmation.

## Recommended next three concrete steps

### 1. Prove one real GitHub collector shadow run

The manual-only collection mode is prepared in the existing workflow. It preserves
the same advisory lock, source budget, pause state, pre/post backup, and error
reporting as the VPS. Before running it, confirm the VPS service is stopped for that
window so there cannot be two collectors. Run it once, compare its Supabase run record
and counts with the existing UI, and keep DigitalOcean available for rollback.

This is the next implementation task. The first real run will visit OLX and commit
collector evidence, so it requires Ogi's explicit approval immediately before it is
triggered.

### 2. Perform a single-scheduler cutover, then cancel DigitalOcean

After the shadow run is accepted, add one daily GitHub schedule at the desired morning
time, observe at least one scheduled success, disable the VPS timer, and confirm only
one run occurred. Preserve the encrypted backup key and one final VPS recovery bundle.
Only then destroy/cancel the DigitalOcean service.

GitHub Actions is a better fit than Vercel Cron for this browser collector: the job
builds/runs a pinned Chromium container and can take several minutes. Vercel remains a
good home for the short Telegram webhook/API later; Supabase remains the database.

### 3. Build the Telegram MVP on top of the proven data path

Create the bot with BotFather only when Ogi is ready. Store secrets in GitHub/Vercel,
never in source. Phase 1 should be one deterministic morning message sent after a
successful collection, for example:

> Car Analyzer · 5 Nov, 08:25 Sarajevo  
> Partial collection: 110 observations · 47 first observed by us · 5 errors  
> 3 observed price drops in the last 7 days (sample: 214 listings)  
> Freshness: observations to 07:20 UTC; last clean run 1 Oct  
> [Open Market] [Compare]

The exact digest query should return a compact structured object with:

1. latest run/freshness and quality label;
2. observations and IDs first observed by us since the prior successful attempt;
3. price changes based only on successive `listing_snapshots` observations;
4. two or three notable listings selected by explicit, testable rules; and
5. the time window and sample size for every trend.

Start with GitHub Actions sending the post-run digest through Telegram's HTTPS API.
For Phase 2, put a webhook on Vercel and answer a small set of commands/buttons with
structured database queries (`/budget`, compare, trends, listing details). Add an LLM
only after retrieval and citations are enforced; it may phrase verified results but
must not invent market advice. Keep long inspection flows in the current UI.

## Credentials and private-group setup needed later

No values should be pasted into chat or committed. The minimum secret names are:

- `TELEGRAM_BOT_TOKEN` — created through BotFather;
- `TELEGRAM_CHAT_ID` — the private Ogi + friend + bot group;
- `TELEGRAM_ALLOWED_USER_IDS` — needed before Phase 2 interactions;
- a dedicated least-privilege bot/digest database credential, preferably exposed as
  `BOT_DATABASE_URL`, rather than giving the bot the broad backup role.

For Phase 1, create a private group, add the bot, send one bootstrap command, capture
and verify the negative group chat ID privately, then allow outbound messages only to
that exact ID. Telegram privacy mode can remain enabled for the one-way digest. Phase
2 can use slash commands, button callbacks, mentions, and replies without making the
bot listen to every group message.

## Blockers and decisions for Ogi

- **Collector health:** today's run is fresh but partial, and the clean anchor is old.
  The Telegram MVP should not launch as though collection were fully healthy.
- **Production approval:** the first GitHub shadow collection will consume source
  budget and write evidence to Supabase; it has not been run yet.
- **Telegram token and chat:** BotFather bot, token, private group, and chat/user IDs do
  not exist yet.
- **Bot database access:** creating a dedicated read-only bot role/password is a
  production credential/schema action and needs approval.
- **Credits/cost:** no LLM is needed for Phase 1. Phase 2 needs a model/provider and a
  small usage budget only if Ogi chooses natural-language phrasing beyond deterministic
  commands. Standard GitHub-hosted runners for this public repository do not consume
  paid Actions minutes, but artifact storage is limited; the 30-day encrypted artifact
  retention is intentionally bounded.
- **DigitalOcean cancellation:** blocked until a real GitHub run, scheduled cutover,
  and final recovery handoff pass.

## Open questions for Ogi

1. What Sarajevo delivery time should the digest target: immediately after the current
   morning run, or a fixed later time such as 09:00?
2. Should the first digest cover the whole observed market or a small watchlist of
   makes/models/budgets?
3. Are slash commands and buttons sufficient for the first interactive release, or is
   free-text conversation a launch requirement?
4. Should both group members be allowed to query everything, including Following, or
   should private saved searches remain visible only to Ogi?
5. After the manual shadow run succeeds, how many scheduled GitHub successes should be
   observed before DigitalOcean is cancelled (recommendation: at least two)?

