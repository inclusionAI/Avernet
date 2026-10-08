# AGENTS.md

This file provides Codex-specific guidance for work under `src/bcs`.

## Read Local Guidance First

Before changing BCS code, read `src/bcs/CLAUDE.md` and follow its module
architecture, layering, testing, and coding rules. If this file and
`CLAUDE.md` overlap, treat `CLAUDE.md` as the detailed local source of truth.

## No Global Formatting

Do not run `cargo fmt`, `cargo fmt --all`, or any global formatter in BCS.
Keep whitespace and style edits limited to the lines that must change for the
task. Avoid import reordering, line wrapping, or formatting churn in unrelated
code.

If formatting is accidentally applied beyond the intended files, stop and clean
the formatter-only diff before continuing.

## Database Access Cost

- For changes to request hot paths, loops, queues, recovery scans or retries,
  estimate database round trips and rows read/written per operation, including
  maximum batch size, concurrent work and sustained failures. Trace the actual
  store/cache implementation; a repository call is not necessarily a DB query.
- Avoid N+1 queries, fetching full rows just to count them, and repeated scans
  of growing queues. Prefer bounded pagination, aggregate queries and chunked
  batch writes within the existing consistency requirements.
- Check existing caches and their invalidation before adding another cache or
  removing a read. Preserve freshness, tenant isolation, CAS fencing and durable
  error handling when reducing database access.
- Bound work per scan/drain and back off repeated failures. Account for shared
  connection-pool usage and transaction/lock duration; batching reduces round
  trips but does not make an arbitrarily large transaction cheap. Avoid holding
  database transactions or locks across external network delivery.
- When a change materially affects database load, verify representative normal,
  maximum-size and failure paths with query-count/batch assertions or focused
  integration/load tests as appropriate. Describe the access-cost change and
  validation limits in the PR; do not infer scalability from small unit tests.

## Freeze Committed Migrations

- Before the first commit/release, group related schema changes that ship
  together into one migration per dialect. Keep independently deployable
  repairs separate; do not create a version for each development subtask.
- Once a numbered migration has been committed to Git, do not add columns to
  it or backfill later schema changes into its CREATE TABLE statements. This
  applies even when the migration has not yet been deployed.
- Add new columns in a new, uniquely numbered migration after the current
  maximum for that dialect. Do not modify `001_init_schema.sql` to mirror the
  latest schema; it is the starting point of the migration chain.
- Keep each column addition in its owning migration. Do not also add it to an
  earlier baseline and rely on `IF NOT EXISTS` to hide the duplicate. Repeated
  execution is controlled by migration version records.
- This also applies to historical SQLite migration bodies and bootstrap DDL
  embedded in Rust. A migration does not need a separate `.sql` file to be frozen.
- MySQL column additions must use supported `ADD COLUMN` syntax. Validate the
  complete migration chain on real MySQL; static file checks alone are insufficient.
- Preserve committed migration names, numbers and checksums. Historical
  corrections require an explicit, scoped instruction and a documented
  compatibility path; never rewrite deployed records to silence a mismatch.
- Treat a migration already recorded in a retained database as frozen even
  before its first Git commit. Inspect that history before consolidating draft
  versions; preserve deployed records and use a separate additive upgrade.
- A current-schema snapshot for fresh installations must be a separate
  artifact with an explicit covered migration version. Do not replay the
  migrations already represented by that snapshot.
