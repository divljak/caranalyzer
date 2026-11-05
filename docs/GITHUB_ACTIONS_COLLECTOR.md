# GitHub Actions collector migration

This migration replaces the DigitalOcean scheduler only after a verified shadow
period. Canonical data remains in Supabase. The portal remains on Vercel.

## Current safe stage

`.github/workflows/collector-verify.yml` builds the pinned image and runs unit tests
on relevant pull requests without receiving or using database credentials. Its
Supabase verification step is manual-only and always passes `--verify-only`. It
performs no source visits and no collection. The collector role's diagnostic writes
are inside a rolled-back transaction. Full before/after database snapshots and
checksums are compared in the runner's temporary directory. The validated after
snapshot is encrypted with `age` before the private temporary directory is deleted.
Only the encrypted file is uploaded as a 30-day GitHub artifact.

The workflow uses the same direct collector source as VPS release `80d50ca`. Python
and Python package versions are pinned to the runtime observed on the VPS on 5
November 2026. Chromium and ChromeDriver are pinned as a matching pair to Debian's
current buildable security release. Debian had already rotated the VPS's version 154
packages out of its live repositories, so the migration uses version 156 and requires
browser behavior to pass the shadow-run checks before cutover.

## Secrets Ogi must add

In GitHub, open **Settings → Secrets and variables → Actions** for the repository and
create these repository secrets:

- `COLLECTOR_DB_PASSWORD`: password for the existing restricted
  `car_analyzer_collector.ojmokmsjppkqwppmrzpv` login.
- `BACKUP_DB_PASSWORD`: password for the existing read-only
  `car_analyzer_backup.ojmokmsjppkqwppmrzpv` login.

Do not paste either password into an issue, pull request, chat, workflow input, or
committed file. The workflow constructs mode-0600 credential files at runtime from
fixed host, port, database, role, and TLS values. Passwords are not forwarded into
the collector container environment. The container runs as the GitHub runner's
UID/GID so it can read those private files and write only to its private ephemeral
state directory; the container root filesystem remains read-only.

## Encrypted recovery artifact

The repository contains only the Ed25519 public recipient at
`deploy/recovery/car-analyzer-backup-recipient.pub`. Its matching private key remains
on Ogi's Mac at `~/.ssh/id_ed25519` with mode 600 and is never sent to GitHub. The
encrypted artifact is safe to expose as ciphertext, but anyone can download artifacts
from this public repository. Never upload the unencrypted `.json.gz` snapshot.

The current recovery key has fingerprint
`SHA256:9uNuYNqbkKtVVhfd3BImsjJZi8wTVWtAEl1cAC1LjvQ`. Retain that private key after the
DigitalOcean VPS is removed; losing it makes these encrypted artifacts unrecoverable.

To verify a downloaded artifact locally after installing `age`:

```bash
age --decrypt --identity ~/.ssh/id_ed25519 \
  --output /private/temp/supabase-backup.json.gz \
  car-analyzer-recovery-RUN_ID.json.gz.age
python scripts/verify_delivery_backup.py /private/temp/supabase-backup.json.gz
```

Use a private mode-700 temporary directory and remove the plaintext immediately after
verification. A full recovery test still requires restoring into an empty loopback
`delivery_recovery_*` PostgreSQL database; never restore directly over Supabase.

## First verification

After this branch is reviewed and the two secrets exist, use the Actions page to run
**Verify collector migration** manually. A successful run must show:

- both expected database roles connected through the TLS session pooler;
- `verify_only: true`;
- `verification.rolled_back: true`;
- zero source visits and zero listing evidence inserted;
- identical before/after application checksums; and
- `backup_verified: true` with exit code zero.

This verification may consume database sequence values, just like the existing VPS
verification. It must not change application rows or daily source budgets.

## Not authorized by this stage

Do not add a `schedule` event, remove `--verify-only`, disable the VPS timer, rotate
database passwords, upload database snapshots, or cancel DigitalOcean yet. Those
steps require an approved backup replacement, an observed shadow run, and a deliberate
single-scheduler cutover.
