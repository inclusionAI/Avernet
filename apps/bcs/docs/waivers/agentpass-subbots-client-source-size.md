rule: AGENTS.md File Size
owner: BCS client maintainers
reason: These five legacy client files already exceed 1000 lines on the A1 base. A2 makes narrow caller-context and subprocess-environment edits; unrelated file splitting would expand this registration change.
risk: Existing large client files remain difficult to review.
compensating_controls: New caller-context and pending-registration modules stay below 1000 lines. Bridge, CLI, subprocess and HTTP regression suites cover changed behavior. No global formatting.
expiry: 2026-11-12
removal_plan: Split existing CLI workflows and engine drivers by responsibility in a separate refactor, removing each entry once it is at most 1000 lines. No newly oversized file is permitted.

# A2 existing client source sizes

Base: A1 `bf15ac4792`. Paths are relative to `apps/bcs`.

| File | Base lines | Current lines |
| --- | ---: | ---: |
| crates/tools/bcs-bridge/core/src/engine/claude_code.rs | 2330 | 2331 |
| crates/tools/bcs-bridge/core/src/engine/codex_app_server.rs | 1378 | 1379 |
| crates/tools/bcs-bridge/core/src/run.rs | 1088 | 1089 |
| crates/tools/bcs-cli/src/client.rs | 4912 | 4954 |
| crates/tools/bcs-cli/src/main.rs | 7913 | 7906 |
