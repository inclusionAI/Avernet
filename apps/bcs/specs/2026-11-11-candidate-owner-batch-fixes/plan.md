# Candidate owner batch fixes implementation plan

**Goal:** Fix PR #3893 review findings without changing public response semantics.
**Architecture:** Deduplicate SQL inputs, retain ordered projection, add indexed
prefilters with binary residuals, and mechanically split a failing test hook.
**Tech stack:** Rust, SQLite, MySQL, Cargo integration/contract tests.

## Tasks (execute inline)

- [x] Add duplicate inputs across 128-ID boundaries to
  `bot_authority_contract_ownerships.rs` and query-count assertions in
  `authority_batch_cost.rs`. Run the existing `authority_reads` and
  `authority_batch_cost` integration targets and observe the regression fail.
- [x] Add `authority_ownerships_mysql.rs`: use the shared full migration-chain
  fixture; capture actual store statements; assert EXPLAIN uses identity key
  parts on a populated database; assert case/trailing-space aliases are absent
  on both current and legacy collations. Run ignored test against a disposable
  database and observe the unindexed-plan failure.
- [x] Deduplicate `id_refs` in `reads_ownerships.rs`; introduce the bounded
  raw-IN plus binary-IN predicate/parameter helper there. Use it in that file's
  owner read and `reads.rs` state/legacy creator reads. Rerun both regressions.
- [x] Move `FailingAuthorityHook` and its factory out of `bot_authority.rs` into
  `bot_authority/failing.rs`, re-exporting both names. Run authority consumer tests.
- [x] Update the store context with query costs and identity semantics, then
  run SQL/memory authority, application/HTTP tests and workspace compilation.
  Check changed file sizes, whitespace, documentation links and secrets.
- [x] Prepare publication: record actual verification and gaps. The approved
  delivery command is `git push --no-verify origin HEAD:feat/bcs-candidates-owner-id`
  after a normal commit, without force or persistent hook changes. Commit and
  remote SHA verification are the delivery evidence, not this checklist.
