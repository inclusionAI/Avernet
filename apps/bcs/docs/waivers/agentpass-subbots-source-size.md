rule: AGENTS.md File Size
owner: BCS runtime and tooling maintainers
reason: All listed files already exceeded 1000 lines at the pinned base. This identity change needs narrow edits in existing composition, trait implementations and caller adapters. Splitting their unrelated lifecycle, routing and test machinery in this branch would substantially expand the authentication review and regression surface.
risk: Existing large files remain harder to review and maintain; this is a temporary scoped size exception, not a clean size-gate claim.
compensating_controls: New identity, lookup, credential, retry and Principal-state responsibilities use separate modules below 1000 lines. Existing files receive narrow edits, no global formatting. Changed-file line counts and affected-module regressions are checked.
expiry: 2026-11-12
removal_plan: Split legacy bootstrap composition, store implementations, CLI commands and large test fixtures by responsibility in a dedicated refactor with unchanged contracts. Remove each entry once it is at most 1000 lines. This exception does not allow new oversized source files.

# Scoped source-size exception

The following is the allowlist for this change. The current CI entrypoint has
no source-line-count checker; its waiver-format checker validates this record.
Do not describe the branch as passing an independently enforced size gate.

| File (relative to apps/bcs) | Base lines | Current lines |
| --- | ---: | ---: |
| crates/adapters/http/bcs-http/src/routes/providers.rs | 1149 | 1171 |
| crates/adapters/http/bcs-http/src/routes/sessions.rs | 2126 | 2126 |
| crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs | 2682 | 2688 |
| crates/adapters/ws/bcs-ws/tests/frame_compat.rs | 3415 | 3434 |
| crates/bootstrap/bcs/src/server.rs | 6976 | 6988 |
| crates/services/bcs-bot-store/src/lib.rs | 3936 | 3945 |
| crates/services/bcs-bot-store/src/memory.rs | 2859 | 2874 |
| crates/services/bcs-bot/src/application/bot.rs | 1608 | 1628 |
| crates/services/bcs-bot/src/application/onboarding.rs | 1605 | 1605 |
| crates/services/bcs-bot/src/core/provider_core.rs | 1398 | 1439 |
| crates/services/bcs-bot/tests/bot_use_cases.rs | 2082 | 2097 |
| crates/services/bcs-organization/tests/management.rs | 1293 | 1294 |
| crates/tools/bcs-bridge/core/src/engine/claude_code.rs | 1391 | 1392 |
| crates/tools/bcs-bridge/core/src/run.rs | 1065 | 1066 |
| crates/tools/bcs-cli/src/client.rs | 4547 | 4594 |
| crates/tools/bcs-cli/src/main.rs | 7301 | 7294 |
| scripts/provider_downlink_console.py | 1260 | 1263 |
