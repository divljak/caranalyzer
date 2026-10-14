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

The existing Mac now receives recurring private copies through a user LaunchAgent,
`com.caranalyzer.backup-copy`, every hour at minute **45**, plus login/load catch-up.
The destination is `~/Library/Application Support/CarAnalyzerBackups/archives`,
outside repository worktrees. No new account, paid service or public backup object
is used. The original one-time copies and rollback image archives remain retained.

The job uses its own private SSH key restricted by the existing VPS's
`authorized_keys` forced command. The server helper offers only a fixed backup
inventory and named archive fetch; arbitrary commands/paths, symlinks, PTYs,
forwarding and credential-file access are denied. Scheduled access cannot execute
administrator commands despite authenticating through the existing root account.
The copy endpoint opens only the fixed eight recovery files. It never opens the
collector/database credential files, queries Supabase or visits OLX.

Successful isolated restores now write a per-bundle `restore-verified.json`, bound
to the archive manifest SHA256 and table counts, only after scratch cleanup. The
copier offers only bundles with a matching successful restore proof, selects the
newest per UTC day, and fetches the newest first before catching up up to seven
missing daily copies per invocation. The job verifies all six payload file hashes
and every table's full-row content checksum, including private filters/baselines,
before atomically publishing a copy. An interrupted, malformed or altered transfer
is discarded; previous complete copies remain intact. Daily sync needs only SSH
and the bundled standard-library Python script, without Codex or Docker running.

Mac retention is **90 days**, minimum **14 verified copies**, a **512 MiB archive
cap**, and a **5 GiB free-space floor** with 96 MiB staging reserve. Only completed
owned copies with matching manifests/receipts are candidates for expiry. Unexpired
copies and existing rollback backups are never deleted to make room; the job fails
safely at the cap/floor. The two safe job logs are bounded to about 1 MiB each.
Directories are 700, key/data/status/plist files 600. The Mac's FileVault encryption
is enabled. No raw saved-search values, passwords or SSH key material enter logs.

Private local markers are `status.json` (latest attempt/failure, with the previous
last success retained), `latest-copy.json` (last verified sync), per-bundle
`copy-verified.json`, and `installation.json` under the destination's parent.
Main can read these locally without contacting a source or restarting a heartbeat.
Compare both last sync time and copied source-archive time; an old source archive
is not made current merely by a recent transfer. The main checker continues to
read canonical JSON snapshots; this independent copy status does not overwrite it.

The hourly calendar event catches up after sleep, but the Mac must be powered on,
logged into this user session and online. Offline attempts record failure and retry
at the next hourly event; the job does not wake the Mac or change power settings.
Logged-out/shut-down periods defer work until login. Thus off-VPS recovery-point
lag is approximately up to an hour while available and **unbounded while the Mac
is unavailable**. Offline periods longer than VPS retention can lose older
point-in-time versions, even though the newest full archive still carries current
application history. No confirmed always-on off-VPS facility was found. The
existing same-VPS MinIO and unrelated notes Git sync are not independent backup
storage; no new external destination was silently chosen.

`deploy/install-off-vps-backup.py` installs this single user job, the restricted
key, copier and stable `recovery-tools` (full restore script, checksum helper,
labels dependency, pinned private venv and this recovery guide). None depends on
retaining the development worktree. Disable only this job with
`launchctl bootout gui/$(id -u)/com.caranalyzer.backup-copy`; preserve the destination
and revoke only its marked restricted SSH key when intentionally retiring copying.
Custom-role passwords, platform configuration and the exclusions documented above
remain separate recovery requirements. Copied schema/grants/policies include the
applied collector-priority SELECT repair when the archive was made after that
migration; older archives require it to be reapplied before priority verification.

Installation is reproducible with `deploy/install-canonical-backup.sh SOURCE_DIR`
after applying the sequence SELECT grant. It refuses an active collector, uses
atomic source/override replacement, reloads systemd, and keeps the existing timer.
To disable the extension, remove only `zz-canonical-backup.conf` and reload
systemd; retain its archive directories. This returns to the cutover's original
JSON snapshots, not to local collection or synchronization.

Current guidance: [Supabase backup/restore](https://supabase.com/docs/guides/platform/migrating-within-supabase/backup-restore),
[read-only export and RLS](https://supabase.com/docs/guides/troubleshooting/pg_dump-fails-with-query-would-be-affected-by-row-level-security-policy-1d5783),
[PostgreSQL snapshot/RLS export](https://www.postgresql.org/docs/17/app-pgdump.html).

## Measured completion — 13 October 2026

Installed runtime source: **`e61e59d`**, focused branch
`codex/canonical-backup-recovery`, draft [PR #8](https://github.com/divljak/caranalyzer/pull/8)
stacked on PR #4. The five installed runtime/override files match their source
SHA256 values, recorded in `canonical-backups/installation.json` on the VPS.
This extension does not select or overwrite `direct-current`; later browser/
coverage releases can use the existing collector wrapper independently.

The final tested post-attempt bundle is
`canonical-20261013T190410286398-after`, exported at **19:04:10 UTC** and
successfully restored on VPS at **19:04:50 UTC**. It covers **30,611 rows**:
6,612 listings, 16,514 observations, 151 direct checks, 131 scraping logs,
15 collection runs, 672 tracked states, one budget, 6,406 labels, 108 listing
details, one source pause and zero current Following/candidate records. It
includes the final read-only role's three-connection setting. The same bundle
was copied privately to the Mac and passed the identical restoration checks.
All data/schema/access/sequence checks matched and scratch containers were removed.
Five focused safety/tampering tests passed, including preservation of nonempty
private filter/baseline content checksums and rejection of altered baselines.
Shell/Python checks and installed pre/post wrappers passed. No OLX request or
live evidence restore occurred.

Installed full hooks were exercised directly without starting the collector;
**14 October, 07:17 UTC** remains their first complete scheduled collector cycle.
The actual collector's prior `partial` / exit-1 status was retained. Existing
cutover JSON snapshots and `.collector-health/current.json` remain intact; the
health checker currently verifies those JSON snapshots, while full schema/data
restore evidence is in the separate markers documented above. Main can consume
both contracts without changing health thresholds.

Disk reserve improved from approximately **1.37 GiB to 2.77 GiB**. Exact unused
image `sha256:73055a5ec834f5cf6be8b4aff6b22a0abfba2b4cc8f6500b6ef710ff8786fc03`
(July `car-analyzer-app:latest`, not the running app's image or the collector's
image) was streamed to a private Mac archive, with every OCI blob and all nine
layer content hashes verified before its VPS image reference was removed without
force. Its compressed archive is 366,973,196 bytes, SHA256
`44d842cc6f9add00fdfa5fa1c8674183a04b56931256eb64fb2d03a463851ce5`, at
`.private/canonical-backups/rollbacks/car-analyzer-unused-app-73055a5ec834.tar.gz`
in this worktree. Recover it with `gzip -dc ARCHIVE | docker image load` and
explicitly restore the original tag if needed. Keep this worktree's ignored
private files when archiving/moving the worktree.

Package/pip download caches were cleared; no installed package, running container,
local database, rollback backup or active collector image was removed. Existing
OS package dependency inconsistencies were left untouched; the Python runtime
uses a private venv bootstrapped by the existing pip. The legacy running app's
old `c0b81fe...` image was already absent from Docker's image registry before this
work; its running container/layers were preserved. Do not confuse the archived
unused July image with a verified current legacy-app disaster recovery image.

The unchanged health checker reported `disk_pressure` **recovered** at
**19:02:40.514311 UTC**, available bytes **2,973,618,176**. Its stale-clean,
partial-collection and detail-failure warnings remain honest and unchanged.
No broad Docker prune, backup deletion, storage account or paid upgrade was used.

## Recurring off-VPS completion — 14 October 2026

Installed user job: **`com.caranalyzer.backup-copy`**, plist
`/Users/ognjendivljak/Library/LaunchAgents/com.caranalyzer.backup-copy.plist`.
It runs hourly at minute 45 and at login/load. Its interpreter is the existing
Homebrew Python 3.13; its script, restricted SSH key, archives, recovery tools,
private logs and status are under
`/Users/ognjendivljak/Library/Application Support/CarAnalyzerBackups`.
The job works independently of Codex and development worktree availability.
A successful LaunchAgent execution copied the first archive at **16:06:28 UTC**;
a repeat installation/load at **16:10:00 UTC** reverified the same copy and fetched
zero duplicate archives. Calendar registration was confirmed; no claim is made
that the next hourly wall-clock event has already occurred.

Fresh manual backup `canonical-20261014T160441957720-manual` was created at
**16:04:41.957720 UTC**, after applied migration **20261014155031** from PR #10.
It restored on VPS at **16:05:21.212928 UTC**, then the Mac copy restored independently
at **16:08:08.900257 UTC** using the stable bundled recovery tools and private venv.
The Mac restore used a temporary clone so the published downloaded artifacts stayed
immutable; the clone and network-disabled scratch container were removed afterward.
`latest-local-restore.json` records this independent proof against the copied bundle.

The manifest SHA256 is
`c415e88dec5cb598bef656d6b43c9dede2d836cda2597da58ec08d0e25ecf2e4`.
All **30,932 rows across 12 tables**, file hashes, full-row content checksums,
schema/access definitions and sequence states matched. The copied catalog explicitly
contains all three `collector_priority_read` SELECT policies and collector SELECT
grants for listing details, candidates and Following searches. Thus this recovery
point includes the actual priority-access repair, rather than depending on an older
permission image. The business table counts/checksums match the earlier 14 October
scheduled post-run archive exactly: this work changed no evidence, private state,
source budget, pause, sequence, real collector marker or clean anchor.

Copied evidence includes **6,674 listings, 16,618 observations, 151 checks**, 6,468
labels, 763 tracked states, 132 scraping logs, 16 collection runs, 108 listing details,
one budget and one source pause; Following and candidates are currently empty.
First copy storage measured **2,625,266 bytes**; available Mac space measured about
**27 GiB**, above the unchanged 5 GiB floor. The existing local database and old
rollback backups/image archives were retained. The active source release remains
`priority-b6e94ff`, service remains honestly failed/exit-1 from its partial real run,
and the existing next collection remains **15 October, 07:17 UTC / 09:17 Sarajevo**.
No extra source run, fake verification observation, heartbeat restart or external
message was performed.

Eight focused copier tests passed: shell/credential/traversal denial, wrong/failed
restore-proof exclusion, malicious tar-member/link rejection, private-baseline
checksum preservation/tampering refusal, expired-only retention, cap/floor refusal
and network-failure preservation. Five original canonical-backup tests passed.
Actual restricted-key inventory succeeded; shell and credential-path commands were
rejected with empty data output. Mode 700 directories and mode 600 key/status/data
files, registered calendar and successful launchd exit were checked.

For independent verification, read local `status.json`, `latest-copy.json`,
`latest-local-restore.json` and `installation.json`; check the copied manifest SHA
above and the per-bundle copy/restore receipts. The root-side endpoint is
`/opt/car-analyzer/.daily-delivery/backup-tools/off_vps_backup.py` and is reachable
only through the marked forced-command SSH key. Global VPS/admin keys remain intact.
Old full exports lacking per-bundle restoration proof are deliberately not offered
by this endpoint; older one-time Mac copies remain retained in their existing places.

The prior recurring-copy gap is closed for this existing Mac facility. An always-on
off-VPS facility is still unconfirmed: sleep/offline/logged-out recovery-point lag
and separate Supabase platform settings/custom-role-password requirements remain.
