# Recorded schema history, not an automatic production push

This source completion changes files only. It does not apply SQL, reset schema,
repair the live migration ledger or reprovision credentials.

`migrations/` contains only the four versions recorded in the canonical project's
ledger on 14 October 2026. `applied-history.json` records their names and hashes.
The imported 12-table baseline matches the applied statement byte-for-byte. The
index differs only in comments/whitespace; its actual definition is valid in
Supabase. The retained source dump matches both approved import manifests.

| Prepared import filename | Recorded applied version |
| --- | --- |
| `20261013132419_car_analyzer_baseline.sql` | `20261013132620` |
| `20261013133418_website_checks_run_index.sql` | `20261013133615` |

The original prepared files remain in the shared workspace/private import evidence.
The source copies here use the actual applied versions, so they do not look like
new pending imports. Neither file contains business records or credentials.

## Manually applied grants

The private application setup (`20261013134056`) and former VPS-to-Supabase delivery
setup (`20261013164856`) were applied through management SQL, not the migration
ledger. Their unchanged source is preserved under `manual-history/`, outside the
CLI's default migration queue. Leaving the former delivery grants pending there
could regrant writes to the retired synchronization role after cutover.

These scripts describe historical transitions, not the current permission state.
The recorded direct cutover retires `car_analyzer_sync` with NOLOGIN and revoked
evidence/sequence writes. The recorded priority repair adds collector-only SELECT
policies on three private tables. The canonical backup extension's separately
installed `deploy/backup/sequence-read.sql` gives the backup role sequence SELECT
and connection limit 3; it is not a recorded migration either.

Do not blindly run `supabase db push`, `migration repair`, a historical grant script
or a fresh bootstrap against this live project. Before any future schema change:
obtain a new read-only ledger/catalog inventory, compare it to this manifest,
review both source and live grants/RLS, and coordinate an explicit reconciliation
plan. Archive/recording is not permission to replay an operation. Do not add the
two manual scripts back to the default queue to make a migration tool happy.

This change deliberately leaves the live ledger untouched. There is no migration
push in the application, collector startup, Vercel build or repository workflow.
Live schema/access restoration uses a verified full canonical archive and its
separate credential/platform requirements, not replay of this directory alone.
An older archive may require the priority read repair after an independently
reviewed restore. Preserve the final direct/backup/app boundaries and legacy
NOLOGIN state; never restore historical sync write access as a shortcut.

## Disposable testing only

The recorded cutover expects earlier roles, so this directory is not a standalone
blank-database provisioning recipe. `scripts/bootstrap_integration_test_database.py`
explicitly reconstructs those historical stages, completes retirement/priority
repair and backup read grants, then adds one synthetic clean-run fixture. It is
restricted to loopback and a database name starting `car_analyzer_integration`.
Use it only with a newly created disposable PostgreSQL container/cluster. It reads
no production environment or credentials. Never substitute a live connection.
