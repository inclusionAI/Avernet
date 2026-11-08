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
| crates/adapters/http/bcs-http/src/routes/sessions.rs | 2126 | 2126 |
| crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs | 2707 | 2713 |
| crates/adapters/ws/bcs-ws/tests/frame_compat.rs | 3418 | 3437 |
| crates/bootstrap/bcs/src/server.rs | 7059 | 7071 |
| crates/services/bcs-bot-store/src/lib.rs | 3936 | 3945 |
| crates/services/bcs-bot-store/src/memory.rs | 2859 | 2874 |
| crates/services/bcs-bot/src/application/onboarding.rs | 1605 | 1605 |
| crates/services/bcs-bot/src/core/provider_core.rs | 1398 | 1426 |
| crates/services/bcs-bot/tests/bot_use_cases.rs | 2082 | 2097 |
| crates/services/bcs-organization/tests/management.rs | 1293 | 1294 |
| scripts/provider_downlink_console.py | 1260 | 1263 |
