# Legacy friend cutover repair implementation plan

Goal: close the four reproducible review regressions without changing HTTP contracts.
Architecture: retain ConnectService as mutation owner, repair its legacy mirror,
propagate repo failures, and perform versioned offline migration before activation.
Tech stack: Rust, async-trait, SQLite/MySQL, Cargo conformance tests.

- [x] Add regression tests in edge-permission `tests/friend_cutover.rs` and
  db-migrations `migrations/friend_cutover_tests.rs`; run them red against PR head.
- [x] Resolve actual request kind/receiver in the compatibility facade before
  decisions; verify accept/reject cannot mutate Rules/Profile/Revoke requests.
- [x] Extend Human writeback, including retry and revoke, through the injected
  FriendshipWritebackPort and real relation service; verify legacy listing.
- [x] Change inbox/outbox return values to ServiceResult, update all three repo
  implementations, ConnectService and conformance/test callers; inject SQL and
  malformed-row failures and verify no success or mutation is reported.
- [x] Split touched oversized files by responsibility without global formatting:
  connect lifecycle/helpers, SQL edge/profile/request/mapping, and memory tests.
- [x] Add SQLite 044 / MySQL 046 legacy request cutover SQL, preserving IDs and
  original timestamps. Register SQLite migration and test upgrade from 043,
  new decisions, env isolation, and reruns. Gate durable bootstrap on coverage.
- [x] Update domain contracts and migration rollout/verification documentation.
- [x] Run `cargo test -j 2 -p bcs-edge-permission -p bcs-edge-permission-store
  -p bcs-app-invitation -p bcs-friend -p bcs-db-migrations`, affected HTTP tests,
  workspace checks and applicable architecture gates; report gaps separately.

Implementation stays in the current PR branch. Commit and push were deferred
until the user explicitly requested them after reviewing the repair results.
