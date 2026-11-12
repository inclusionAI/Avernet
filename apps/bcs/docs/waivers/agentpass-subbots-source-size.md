rule: AGENTS.md File Size
owner: BCS runtime maintainers
reason: All listed files already exceeded 1000 lines at the latest dev base. This identity change needs narrow edits in existing composition, trait implementations and caller adapters. Splitting their unrelated lifecycle, routing and test machinery in this branch would substantially expand the authentication review and regression surface.
risk: Existing large files remain harder to review and maintain; this is a temporary scoped size exception, not a clean size-gate claim.
compensating_controls: New identity, lookup, credential and Principal-state responsibilities use separate modules below 1000 lines. Existing files receive narrow edits, no global formatting. Changed-file line counts and affected-module regressions are checked.
expiry: 2026-11-12
removal_plan: Split legacy bootstrap composition, store implementations, HTTP adapters and large test fixtures by responsibility in a dedicated refactor with unchanged contracts. Remove each entry once it is at most 1000 lines. This exception does not allow new oversized source files.

# Scoped A1 server source-size exception

Only server files changed by A1 are covered. Client files belong to a separate PR.
The current CI entrypoint validates waiver format; this record is not a claim
that an independent source-size gate passes. No newly oversized file is allowed.

Base: `1f2014f6cc17cf99eabc45a19afae25a6d2d9af5` (origin/dev, 2026-10-19).

| File (relative to apps/bcs) | Base lines | Current lines |
| --- | ---: | ---: |
| crates/adapters/http/bcs-http/src/routes/providers.rs | 1149 | 1161 |
| crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs | 2707 | 2713 |
| crates/bootstrap/bcs/src/server.rs | 7059 | 7071 |
| crates/services/bcs-bot-store/src/lib.rs | 3936 | 3945 |
| crates/services/bcs-bot-store/src/memory.rs | 2859 | 2874 |
| crates/services/bcs-bot/src/application/onboarding.rs | 1605 | 1605 |
| crates/services/bcs-bot/src/core/provider_core.rs | 1398 | 1426 |
| scripts/provider_downlink_console.py | 1260 | 1263 |

## Completed entries (split 2026-11-12)

The following entries are no longer an active exception. They were removed from
the table above after the dedicated responsibility split
(`apps/bcs/specs/2026-11-12-bcs-large-file-split/`); the recorded base/current
counts stay in history here, not as a claim about the files now.

| File (relative to apps/bcs) | Lines at removal | Split commit |
| --- | ---: | --- |
| crates/adapters/http/bcs-http/src/routes/sessions.rs | 245 | e811eac9cadb62b1224e093ab93efcd82d7deee5 |
| crates/adapters/ws/bcs-ws/tests/frame_compat.rs | 809 | 50e537ae03 |
| crates/services/bcs-bot/tests/bot_use_cases.rs | 450 | a5cfe9ce45 |
| crates/services/bcs-organization/tests/management.rs | 190 | 5268a2dae7 |

`crates/adapters/ws/bcs-ws/tests/web_frame_compat.rs` was split in the same
change (624 lines) but had no row in this exception; it is listed only for
traceability. This does not close the whole waiver: the rows above and the
client-side record remain in force.
