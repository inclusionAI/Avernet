# Delivery record

## Implementation status

Implemented locally in two existing working areas, without commit/push/deploy:

- Avernet implementation worktree:
  `/Users/shenlifang/work/codes/datamarket/teamclaw/Avernet/.worktree/agentcard-fuse-sync-spec`
  branch `codex/agentcard-fuse-sync-spec`, based on fetched `origin/dev`.
- OCB implementation worktree requested by the user:
  `/Users/shenlifang/work/codes/datamarket/teamclaw/ocb/.worktree/bcs-sofa-rpc-rs-upgrade`
  branch `codex/bcs-sofa-rpc-rs-upgrade`, existing unrelated dirty files preserved.

The OCB public submodule received the same public production source patch locally
for integration testing; its gitlink was not committed or updated. The OCB branch
advanced during the work from `06408dc5...` to `da768d9076...` through an existing
branch commit; no reset or history rewrite was performed.

## Changes delivered

- A2A registration and mutation Cores accept optional existing `CatalogChangePort`.
- Registration, replacement and successful CAS commit notify once after persistence;
  failed validation, fetch, transaction and CAS paths do not notify.
- Core call-site observation logs use available Bot/provider/ref/revision fields and
  the existing request-ID observation value; no event model or queue signature change.
- Public bootstrap can create/share one Fuse V2 `CardChangeQueue` through
  `BcsServerExtensions` and `run_with_resolved_config_and_extensions`.
- OCB Triple assembly accepts the same port; Triple handler/proto/mapping/business
  policy were not changed.
- Fuse Card PUT/DELETE now log allowlisted started/succeeded/failed summaries under
  `bcs_observation`, including validated HTTP status/ack and PUT `card_version`.
  Full Card content, credentials, raw response/error text and secret-bearing URLs
  are excluded.

## Validation evidence

PASS:

- Public Fuse client focused suite: existing Card contract tests plus new observation
  tests for PUT/DELETE success, non-2xx, timeout, malformed JSON and negative ack.
- Public A2A application focused suite: 128 tests passed in the selected package run;
  includes registration/update/refresh notification and failure assertions.
- Public V1 REST route suite: 8 tests passed, including real SQLite/Core/queue/Fuse
  stub integration for register, visibility update, Card update and refresh; rejected
  duplicate/empty updates produced no extra Fuse write.
- Public legacy REST provider suite: 27 tests passed.
- Public Bot registration suite: 41 tests passed.
- Public bootstrap queue-selection unit test: 1 passed.
- Public observation routing unit test: 1 passed; the existing INFO file target
  includes `bcs_observation`.
- OCB Triple suite: 9 tests passed, including all existing tests and the new real
  Triple -> public Core/SQLite -> shared queue -> Fuse stub integration.
- OCB `bcs-internal` `cargo check` passed with the Rustup stable 1.98 toolchain.
- Changed Rust source files were checked for the 1,000-line limit; none exceeded it.
- `git diff --check` passed in public, OCB and the pinned public submodule.

NOT_PASS / NOT_RUN:

- Full public architecture gate did not pass on this baseline because it reports
  many pre-existing dependency/import/trait/conformance violations unrelated to
  this change. Its R25 workspace discovery then started a large full-workspace
  build; it was stopped to bound disk growth. This is not a clean architecture gate.
- No full workspace Cargo test, no OCB `check-p4.sh`, no pre-push hook, no commit,
  no push, no deployment and no live Fuse/Observ validation were performed.
- One duplicate default-profile architecture build was stopped; no user worktree or
  cache was deleted. Disk was rechecked throughout; final observed free space was
  approximately 29 GiB.

## Remaining integration step

Before delivery to a branch/PR, commit the public Avernet changes first, make the
public commit reachable from the configured OCB integration source, update the OCB
`submodules/avernet` gitlink to that exact commit, then run the OCB focused suite
again. Keep public commit, OCB commit, gitlink availability, deployment and live
Fuse evidence as separate facts.
