# Private Supabase workspace on Vercel

The existing React/Vite UI and FastAPI evidence API run together in Vercel's
FastAPI runtime. Deploy from the repository root, using `vercel.json` and the
`deploy.vercel_app:app` entrypoint. The build creates `frontend/dist`; FastAPI
serves the UI, assets and four deep-link routes along with `/api/*` on the same
origin. No localhost API URL or Supabase secret is included in the browser.

## Access and target

The project is `car-analyzer` (`prj_HMP6rQiBrGFYzTnFCaVIocPNFv9x`) in
`ogilud-gmailcoms-projects` (`team_L2yDR103llbvqx5iPqb0IiLI`), on its existing
Hobby plan. Standard Vercel Authentication protects preview URLs. Use a protected
preview URL and sign into the owning Vercel account. The public production alias
is deliberately not deployed: Hobby Standard Protection excludes that alias.
The application refuses to serve any Vercel environment other than `preview`,
and its database secret is configured only for Preview. No paid services were added.

`DATABASE_URL` is a Vercel Secret, available only to the server. It uses the
Supabase shared transaction pooler at port 6543, TLS and SQLAlchemy `NullPool`.
The `car_analyzer_app` role is restricted by grants and RLS: it can read evidence
tables and insert/delete Following searches. Its only update permission is the
saved-search fingerprint column for idempotent saves. It cannot modify collector
evidence, baselines, schema or roles. Direct `anon` and `authenticated` database
access remains closed. The runtime access SQL is recorded in the corresponding
`private_vercel_runtime` migration. The login secret was provisioned separately,
with no plaintext password in SQL or source.

The API skips startup DDL and automatic label refresh in hosted mode. Candidate
editing and local background OLX attribute jobs are disabled there. Imported
gearbox details remain readable. Those jobs continue to belong to the local
collector workspace. Hosted mutation requests must supply the same HTTPS origin;
cross-site requests and unexpected hosts are rejected. API responses are uncached,
and database failures return a generic retryable error without connection details.

## Evidence and freshness

All four pages use the real same-origin API by default. `?demo=1` explicitly
selects labeled illustrative data and separate browser-only saved searches.
An API failure never silently falls back to illustrative prices.

Supabase currently contains the imported snapshot: 6,559 listings, 16,433 price
observations, 133 direct-page checks and 108 local gearbox records. The latest
clean collection is 1 October 2026 at 17:31:22 UTC; later partial/failed runs stay
separate from that freshness anchor. The UI displays the stale-evidence notice.
Asking prices, observed price cuts and disappeared ads do not establish sales,
demand or selling speed.

The existing VPS collector still writes to its PostgreSQL database on the VPS.
It does not write to Supabase, and this snapshot does not automatically receive
future collections. No collector schedule, VPS service or backup was changed by
this deployment. See `SUPABASE_IMPORT.md` for import reconciliation and provenance.

## Checks

Run the frontend build, active TypeScript/lint checks, evidence tests, dealer API
tests against a separate audit database, and `test_hosted_runtime.py`. Test hosted
reads with the restricted role, compare response values with direct database
aggregates, and verify Follow → reload → Following → Unfollow against Supabase.
Remove only the verification searches created by that flow. Unauthenticated HTML,
API and asset requests must encounter Vercel Authentication.

## Deployment verification — 13 October 2026

The [private workspace](https://car-analyzer-git-dealer-eviden-1dda87-ogilud-gmailcoms-projects.vercel.app/market)
is available on the branch preview alias. Vercel deploys the committed branch
`dealer-evidence-prototype`; the implementation commit is `fa6a3d0` in PR #2.
The initial verified deployment is `dpl_4PEMtReo8Kw6YNpeUhsDtfLKmBuc`, READY,
API target `staging` (Vercel's preview environment), with the FastAPI function in
Stockholm (`arn1`). The first automatic Git build selected production and was
canceled before release. A project ignored-build rule now skips production builds;
explicit preview builds proceed. The production environment has no database secret.

Verified against the deployed browser, same-origin API and Supabase:

- Market displayed actual filtered distributions, including Peugeot 3008's
  14 priced ads/34,700 KM median and Tiguan's 7 priced ads/45,900 KM median in
  the default 2020–23 SUV/diesel/automatic/50k–150k km scope.
- Model analysis rendered per-year and monthly evidence, comparable ad rows,
  real OLX links and recorded individual asking-price histories. Browser testing
  found and fixed the missing SELECT permission for the candidate-status join.
- Compare rendered both models with the same filters and no ceiling; the API
  preserved `confirmed_sales: null` and the insufficient-evidence resale verdict.
- Follow, full page reload, Following and Unfollow worked. Direct Supabase SQL
  confirmed the exact saved filters, 7-ad/45,900 KM baseline and original collection
  timestamp. The verification searches were removed; the table returned to zero rows.
- Live loading and empty states were observed. An invalid live API request showed
  the error and Retry control without substituting fixture prices. A simulated
  unavailable database returned a generic uncached 503 in the hosted runtime test.
- Anonymous HTML, API and branch-alias requests returned Vercel sign-in redirects.
  Authenticated cross-origin POST returned 403. Unexpected hosts, source jobs and
  production were rejected in hosted runtime checks. Database tests confirmed the
  role cannot change collector evidence, saved baselines or schema. `anon` and
  `authenticated` table access remains denied.
- Build, active TypeScript/ESLint checks, 16 dealer API integration tests, 6 frontend
  evidence tests, 3 parser tests and 3 new hosted-runtime tests passed. Browser assets
  were checked for database credentials. Verification captures and request evidence
  are retained in `.private/vercel-runtime/`, excluded from Git and deployment.

The original frontend lockfile still reports dependency audit advisories (23 in
the production dependency graph, including 19 high). No broad dependency upgrade
was included in this database/deployment change. Runtime Python dependencies are
pinned separately and the frontend build continues to use its existing lockfile.

Reference configuration follows [Vercel's FastAPI runtime](https://vercel.com/docs/frameworks/backend/fastapi),
[Vercel Authentication](https://vercel.com/docs/deployment-protection/methods-to-protect-deployments/vercel-authentication)
and [Supabase's SQLAlchemy pooler guidance](https://supabase.com/docs/guides/troubleshooting/using-sqlalchemy-with-supabase-FUqebT).

## Collection-status follow-up

The UI now labels the analysis timestamp as the last clean collection, alongside
the latest website run's date, status and recorded observation count. If a later
attempt fails with no observations, it also retains the latest run that recorded
observations. Partial/failed/running attempts do not advance the clean analysis
anchor or imply that every listing was freshly checked. This status is shared by
all four pages, including an empty Following page. The API adds observation counts
and the latest run with observations to its existing overview; it does not alter
collection records or schedules. Daily delivery and collector changes are owned
by the separate delivery task; the historical snapshot-only description above
describes the initial deployment.
