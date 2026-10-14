# Combined integration review — 14 October 2026

This branch normally merges the frozen PR 2–10 heads into `main` at
`076f47d8b48fd8ffdbb597c902588c6d69e28f70`. Every frozen head is an ancestor;
there were no merge conflicts, squash operations or collector/browser fixes.
The integration merge checkpoint is `bdf15244428ee6ac52942b4073707cc1e68f4ab0`.
The additional source completion described below follows that checkpoint.
**Stop before main merges: Main reviews this concrete combined tree first.**

| PR | Frozen head |
| --- | --- |
| 2 — private dealer app | `29d09fcb19700c32dbf7e5a15bb1dfe677fba264` |
| 3 — daily delivery | `21925fc9598a85919572a8edeee74e4ceed949cd` |
| 4 — direct canonical collector | `a058c4dd7a4745dc1df7b2cccc5938af6058763e` |
| 5 — collector health | `49999c868944ccfc2ff1a4e9d7a4982e87aef3d3` |
| 6 — detail reliability | `0afe6fd2ae531d5f3e06e78353ab259294e7bd45` |
| 7 — all-model priority | `30245a245062337f60a7abff0b484b4ffdcb6e11` |
| 8 — canonical backup and stable Mac copy | `60f946daced8bdc1946006e4ef8b722da9930e81` |
| 9 — scheduled verification | `015b9fc47cf2271c819363d3144e7226ad66552b` |
| 10 — collector priority reads | `0d9f51979b49a713a2990b94402c4d39b1db1d5c` |

## Source completion

The imported baseline and index are now tracked under their actual applied
versions. `supabase/applied-history.json` records the read-only ledger inventory
and statement/source hashes. The active migration directory contains exactly
the four recorded versions; the two unchanged management-SQL grant scripts are
archived outside its default queue. They must not be replayed as pending grants,
especially after retirement of the old synchronization role.

`supabase/README.md` explains the historical prerequisites, separately installed
backup sequence grant, current permission boundaries and review required before
any future live reconciliation. This source completion performs no live SQL or
ledger repair. The approved import dump matched both private import manifests
(`3149c72568e22d9f4cc7bf3a53fbb1121d92bead8289f4bf6e8bc96f7806aef6`).

The guarded bootstrap explicitly orders baseline/index, historical application
and delivery prerequisites, direct cutover, priority repair and backup sequence
read grants on a fresh disposable loopback cluster. It reads no production
credentials, refuses a nonempty public schema and requires a disposable database
name. Its final role tests use nonempty priority fixtures and actual role changes.
The default migration queue alone is not a blank-database bootstrap recipe.

The approved all-model coverage plan and read-only audit SQL are included. Their
dated baseline measurements remain historical; no buying filters or budgets are
changed. The budget stays three discovery requests and twenty detail attempts.

## Combined verification

- A fresh PostgreSQL 17 container on `127.0.0.1:55434`, with temporary storage,
  successfully executed the ordered bootstrap. The full Python suite ran 137
  tests: 135 passed locally and two packaged-browser tests were reserved for the
  network-isolated Chromium container. PostgreSQL advisory-lock concurrency ran.
- Positive collector SELECTs and `load_priority_inputs` consumed nonempty rows
  from all three priority tables. Backup sequence reads succeeded. App evidence,
  filters and baseline writes; collector private/immutable history writes;
  backup/retired-sync mutations; and anonymous/authenticated reads were denied
  with SQLSTATE `42501`. The retired role remained NOLOGIN.
- Both real Chromium tests passed using the installed collector's exact image
  `sha256:7d51ac14987e3a9d17decc711146db2191d741f2fa5ca31bf677170c0ee8884d`.
  Docker `--network none` permitted only the container's loopback HTTP fixture.
  The tests verified renderer-timeout DOM recovery without a duplicate request,
  async script timeout configuration, rejection of local fixture identities as
  source evidence and driver cleanup. Source visits: zero.
- The first Chromium harness used a read-only filesystem and restrictive CPU/
  memory settings: startup failed, then one recovery case timed out. Both cases
  passed with an ephemeral writable root, 640 MiB memory, 256 MiB shared memory
  and no CPU quota. No readiness or timeout assertion, source test or browser
  implementation was weakened. The initial local route check also required
  building the frontend before the complete suite could pass.
- Hosted frontend build, dealer TypeScript check, scoped ESLint and all nine
  evidence tests passed. All nine deployment/backup shell scripts passed syntax
  validation. Existing unrelated dependency advisories are not addressed here.
- App/frontend/hosting files are unchanged from PR 2's reviewed head. Browser
  code matches `1327757`; direct collector/priority code matches `b6e94ff`;
  health code matches `7f77901`; backup/copy code matches PR 8's updated head.
  Thus the test harness introduced no collector source/runtime divergence.
- Shared-workspace tracked and untracked status is unchanged. Only this released
  managed worktree was used for assembly. Private logs, source archives, database
  contents and credentials are excluded from Git. Temporary QA resources are
  removed after verification; installed jobs and retained backups are untouched.

## Deployment and operational limits

The existing owning-account Vercel preview and protection remain in place. The
database secret is Preview-only, the project's production ignored-build rule
skips main production builds, and the hosted runtime rejects non-preview use.
No production promotion, environment/protection edit, source run, budget reset,
VPS release or schedule change, heartbeat restart or migration push is part of
this branch. Publishing the draft PR may produce its own protected preview.

The actual 14 October run remains **partial**, with zero detail attempts after
the priority-read permission failure. The later 39/39 capability checks and this
isolated suite do not turn it into a successful run. The installed priority read
repair and real website/browser behavior await the next authorized scheduled
run on 15 October at 07:17 UTC (09:17 Sarajevo).

The stable Mac copy job was verified separately against a restored current
canonical backup, including the priority grant repair. Copies are hourly while
the Mac is available; offline lag is unbounded. There is no always-on independent
remote backup destination. Main is public and lacks branch protection/CI gates;
the review checkpoint is procedural and main must remain unmerged until release.
