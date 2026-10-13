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

The deployment report and browser/API verification evidence are appended after
the hosted checks finish.
