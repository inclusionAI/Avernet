# Delivery record

## Implementation status

Implemented in the Avernet worktree and pushed for review:

- Repository: `git@github.com:inclusionAI/Avernet.git`
- Worktree: `/Users/shenlifang/work/codes/datamarket/teamclaw/Avernet/.worktree/agentcard-fuse-sync-spec`
- Branch: `codex/agentcard-fuse-sync-spec`
- Target: `dev`
- Implementation commit: `74f3e8baa8cbc27762a8981df20a8cd5f32bcbc9`

The OCB implementation remains in the separately requested worktree:
`/Users/shenlifang/work/codes/datamarket/teamclaw/ocb/.worktree/bcs-sofa-rpc-rs-upgrade`.
Its unrelated dirty files were preserved, and its `submodules/avernet` gitlink was
not committed or updated as part of this Avernet change.

## Changes delivered

- A2A registration and mutation Cores accept an optional existing `CatalogChangePort`.
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
- OCB Triple suite: 9 tests passed, including the real Triple -> public Core/SQLite
  -> shared queue -> Fuse stub integration.
- OCB `bcs-internal` `cargo check` passed with the Rustup stable toolchain.
- Changed Rust source files were checked for the 1,000-line limit; none exceeded it.
- `git diff --check` passed in public, OCB and the pinned public submodule.
- Avernet pre-push hook passed in lint-only mode; the plaintext-secret scan passed.
- Focused post-fix Fuse observation test passed: 2 tests.

NOT_PASS / NOT_RUN:

- Full public architecture gate did not pass on this baseline because it reports
  many pre-existing dependency/import/trait/conformance violations unrelated to
  this change. Its R25 workspace discovery then started a large full-workspace
  build; it was stopped to bound disk growth. This is not a clean architecture gate.
- No full workspace Cargo test, no OCB `check-p4.sh`, no deployment and no live
  Fuse/Observ validation were performed.

## Remaining integration step

OCB integration should consume the pushed Avernet commit and update its
`submodules/avernet` gitlink in a separate OCB change. This Avernet PR does not
include OCB changes, deployment, or live Fuse evidence.
