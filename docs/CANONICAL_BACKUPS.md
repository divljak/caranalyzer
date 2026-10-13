# Canonical application backups and recovery

Installed on the existing VPS after the completed direct cutover (PR #4,
`a058c4d`). Supabase project `ojmokmsjppkqwppmrzpv` remains canonical. This
extends its existing backup schedule and read-only `car_analyzer_backup` login.
No second timer, collection run, new account, public object or paid storage was
created. The retained VPS database and rollback archives are not rotation targets.

## Installed contract

`/etc/systemd/system/car-analyzer-collector.service.d/zz-canonical-backup.conf`
adds full application exports before the existing collector command and after
its termination, including a partial/failed run. It loads after `delivery.conf`,
so the existing collector-container stop runs before the post-attempt archive.
The only daily schedule is still **07:17 UTC** (09:17 Sarajevo in daylight saving
time, 08:17 in standard time). The original before/after JSON snapshots remain
unchanged. A failed full pre-backup stops collection; post-backup/restore failure
fails the service without rolling back already committed observations.

The root-only wrapper is
`/opt/car-analyzer/.daily-delivery/backup-tools/run-canonical-backup.sh`.
It takes the dedicated `canonical-backup.lock` and the existing `pipeline.lock`;
it refuses overlap instead of retrying collection or changing source budgets.
`backup-tools/venv` contains pinned Python dependencies. The PostgreSQL 17 client
uses the already installed `postgres:17-alpine` image, matching the source's major
version. No database server or collector image was replaced.

Each post-attempt archive is also restored automatically into a fresh Docker
PostgreSQL 17 container with **network disabled**, **no published ports**, a
192 MiB tmpfs database, a 256 MiB memory limit and a unique name. The tool has no
argument for a live restore target. It waits for the final database process,
not the temporary initialization server. Scratch roles are NOLOGIN placeholders;
no Supabase temporary role/password is provisioned. Successful cleanup is checked.

## Coverage and privacy

An archive directory under `.daily-delivery/canonical-backups/` contains:

- `application.dump`: compressed PostgreSQL custom archive of the full `public`
  application schema and data, including columns/defaults, primary/foreign keys,
  indexes, sequences and their saved values, RLS/policies, table/column grants and
  default privileges. INSERT-form data permits restoring an RLS-enabled export.
- `snapshot.json.gz`: all twelve tables with canonical full-row SHA256 checksums.
  It and the database dump use the **same exported repeatable-read snapshot**.
- `catalog.json`: definitions used to check the restored schema, constraints,
  indexes, policies, table/column grants, default privileges and sequence properties.
- `roles.json` / `roles.sql`: original role attributes/settings and application
  memberships without passwords; restoration definitions keep every login disabled
  and create platform-role placeholders without privileged platform capabilities.
- `sequences.json`: counter values actually saved in the archive. PostgreSQL
  sequence values are nontransactional, so source reads can race; verification
  compares the archive's own state and does not reset live sequences.
- `manifest.json`: file SHA256 values, table counts/content checksums, database
  version, creation/stage, archive size and storage policy evidence.

The twelve tables cover historical listing observations/checks/run records,
collection budgets/states, derived vehicle labels, private Following searches
(including filters and original baselines), candidates, imported listing details
and source pauses. A changed table inventory or unsupported membership fails
closed until coverage is reviewed. It never quietly excludes a newly added table.

The existing backup role received **SELECT on public sequences only**, in addition
to its existing SELECT/policies on the twelve tables. It did not receive USAGE,
UPDATE, `pg_read_all_data`, BYPASSRLS, schema ownership or collector/app membership.
Its connection limit is three: two sessions for an exported-snapshot dump and
one for the independent health reader. This adds no mutation privilege.
The administrator grant/limit are recorded in `deploy/backup/sequence-read.sql`.
The app's evidence and baseline restrictions are unchanged.

Directories are mode **700**, artifacts and credentials **600**, wrapper **700**.
Raw Following searches are private. Credentials stay in the existing
`.daily-delivery/backup-secret.json`; a private temporary pgpass file passes them
through a mounted file, never process arguments, logs, source or browser assets.
Subprocess diagnostics and exceptions do not print private rows or connection
strings. The temporary pgpass file and incomplete exports are removed on errors.
The session connection retains the cutover's TLS `require` configuration.

## Storage bounds and status

Only this module's complete `canonical-*` directories are rotated. Rotation keeps
at least **14 archives** and removes other complete archives only after **30 days**.
At two archives/day this normally preserves 30 days (about 60 archives). There is
also a **256 MiB cap** for this archive family and a **512 MiB disk-free floor**.
A conservative 96 MiB export reserve is required before starting; pg_dump has a
64 MiB file-size limit; the compressed JSON snapshot is limited to 16 MiB. If policy-expired deletion cannot make room, backup fails
and leaves all recent/unexpired archives and every rollback copy untouched.

No retained local PostgreSQL dump, pre-cutover snapshot, prior release, old image
rollback archive or another application's data belongs to this cleanup policy.
The existing cutover's 30 JSON snapshots remain separately bounded (about 39 MiB
at current size); its pre-cutover archives remain retained. A full application
archive measured about **2.6 MB**, so 60 copies at this size require about **156 MB**,
below the cap. Growth beyond the cap needs an explicit capacity/retention decision.

Private status markers:

- `canonical-backups/latest.json`: latest completed export, manifest and path.
- `latest-attempt.json`: safe success/failure and timing for the latest export.
- `latest-restore.json`: last successful isolated restoration, with data/schema/
  access/sequence checks and confirmed scratch cleanup.
- `latest-restore-attempt.json`: success/failure for the latest restore attempt.

A stale successful restore marker is not proof that a newer archive passed.
Compare its bundle path/timestamp with the latest export and attempt marker, and
check the systemd journal/result. Backup installation preserves the collector's
real partial/failed status rather than clearing it.

## Recovery procedure

1. Stop collection and hosted mutations deliberately for a real recovery, retain
   the current database/snapshot for investigation, and choose a known verified
   archive. **Do not restore over live Supabase.** Work on a fresh isolated target.
2. Check file/content SHA256 values and run the built-in isolated restore check:

   ```bash
   cd /opt/car-analyzer
   .daily-delivery/backup-tools/venv/bin/python \
     .daily-delivery/backup-tools/scripts/canonical_backup.py verify-restore \
     /absolute/path/to/canonical-ARCHIVE
   ```

   This creates/restores/removes only its own network-disabled scratch container.
   All table checksums, schema/access definitions and sequence state must match.
3. For an independently managed empty PostgreSQL 17 recovery database, create
   role placeholders from `roles.sql`, remove only that new database's empty
   default public schema, then `pg_restore --single-transaction --exit-on-error
   --no-owner` the application dump. Keep ACLs enabled; skipping privileges/RLS
   is not the verified recovery. On a new Supabase project, platform roles already
   exist: reuse them, create only missing `car_analyzer_*` roles, and avoid
   recreating platform/system roles. Review default privileges/ownership for the
   managed target rather than blindly executing the scratch bootstrap.
4. `--no-owner` makes the restoring administrator own application objects.
   Source platform ownership/privileged role capabilities are not automatically
   reconstructed. Re-establish intended schema ownership, explicit CONNECT grants
   on the replacement database, and application role settings/memberships after
   review. Custom runtime roles must stay NOSUPERUSER/NOCREATEDB/NOCREATEROLE/
   NOBYPASSRLS; retired sync stays NOLOGIN. The recorded role manifest distinguishes
   original properties from deliberately restricted scratch placeholders.
5. **Passwords are absent.** Provision/reset new app, collector and backup login
   passwords privately through the established SCRAM/secure-file procedure. Update
   the Vercel Preview secret and VPS credential JSON with the new session/transaction
   pooler hosts/user suffixes for the target project. Never put passwords in shell
   arguments or committed SQL. Enable each intended login only after verification.
6. Verify count/full-row checksums, RLS/policies, role denials, orphan constraints,
   sequences and preserved Following filters/baselines/candidates/enrichment.
   Verify hosted reads against direct aggregates. Preserve source budgets, pause
   state and the unchanged clean-evidence anchor. Restore the same existing timer
   only after the canonical target and private UI are reconciled.

The bundle is a **complete current application schema/data recovery archive**,
not a full Supabase platform disaster-recovery image. It excludes `auth`, `storage`,
Vault and platform internal schemas; Storage object bytes; project configuration,
JWT/API keys, OAuth/SMTP settings, functions outside public, Edge Functions, DNS,
Vercel deployment protection/env secrets, and the collector's operating system/
browser/source release. The current application does not use Supabase Auth or
Storage and restored without extra extensions on standard PostgreSQL 17. Maintain
those platform settings and source releases separately. A future feature using
additional schemas/storage must extend this contract before claiming coverage.

## Off-VPS copies and operational handoff

A verified full bundle is stored privately on the user's existing Mac under this
worktree's `.private/canonical-backups/`, and restored there with matching checks.
This is independent of both Supabase and the VPS. It is a **one-time copy**:
recurring automatic off-VPS copying is **not configured**. The existing VPS
`vault-sync` pushes an unrelated notes repository; it was not used for raw private
saved-search data. No authorized recurring independent backup destination/account
was configured, and no new third-party account or billing choice was introduced.
VPS backups remain exposed to total VPS loss; the Mac copy will become stale.

Installation is reproducible with `deploy/install-canonical-backup.sh SOURCE_DIR`
after applying the sequence SELECT grant. It refuses an active collector, uses
atomic source/override replacement, reloads systemd, and keeps the existing timer.
To disable the extension, remove only `zz-canonical-backup.conf` and reload
systemd; retain its archive directories. This returns to the cutover's original
JSON snapshots, not to local collection or synchronization.

Current guidance: [Supabase backup/restore](https://supabase.com/docs/guides/platform/migrating-within-supabase/backup-restore),
[read-only export and RLS](https://supabase.com/docs/guides/troubleshooting/pg_dump-fails-with-query-would-be-affected-by-row-level-security-policy-1d5783),
[PostgreSQL snapshot/RLS export](https://www.postgresql.org/docs/17/app-pgdump.html).
