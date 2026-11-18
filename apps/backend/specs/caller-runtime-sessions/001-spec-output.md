# Caller runtime session discovery

## Goal
List all actual sessions returned by the authenticated subject's dedicated Caller runtime, including ordinary manually created sessions, cron conversations and subagent sessions. Register discovered keys in the existing Backend ownership index so normal connect/detail/history authorization continues to work. Never synthesize a session key.

## Existing flow and boundaries
ExpertChat authorizes the Bot and session subject, resolves an exact dedicated Caller binding, and enriches Backend-owned sessions. Runtime-created sessions have no Backend ownership entry. Reuse the prior cron discovery implementation and live acceptance evidence, removing only its cron-specific restriction and updating necessary capability/log names. Do not modify shared-runtime ownership policy or engine adapters.

## Engine and stage matrix
| Runtime | Stages | Instance boundary | Discovery |
| --- | --- | --- | --- |
| OpenClaw dedicated Caller | Published online friend chat; legacy draft selector means online | Exact authenticated subject/Bot/owner Caller instance binding | Register all valid actual runtime session IDs |
| OpenClaw shared owner/service runtime | Existing stage policy | Shared runtime; not subject-owned | Existing index-only behavior |
| Teclaw Caller | draft/verify/online can share one physical instance, even with different bindings | Stage/binding names do not prove isolation; identities/ownership remain distinct | No unindexed runtime discovery; retain strategy-disabled behavior |
| Other engine strategies | Existing policy | No newly established isolation contract | Discovery disabled by default |

## Implementation plan and verification
1. Bring forward the two explicitly authorized cron feature/acceptance commits without committing; adapt them to latest dev's strategy-based Caller preparation.
2. RED: add ordinary unindexed and subagent key tests to the same dedicated Caller session list, including paging, exact key/favorites, duplicate/malformed IDs and cross-subject isolation.
3. GREEN: remove cron-key matching, rename the dedicated Caller runtime helper/capability/logs, and preserve successful runtime timestamps and existing Backend ownership registration. Missing/initializing Caller on an empty index must not provision/restart.
4. Update the owning ExpertChat README. Run focused service/strategy/architecture/conformance tests and the expanded live HTTP acceptance. Live acceptance must create actual runtime keys through Engine HTTP, then assert discovery and real ownership persistence; no direct core calls used as live coverage.
5. Keep shared and Teclaw discovery disabled and verify the authoritative engine overrides fallback connection engine metadata.

## Logs
Use the existing logger and bounded Caller-session request/success/failure events with Bot/user/owner/binding, route/method, page parameters, selected actual session IDs, status and elapsed time. Do not log authentication credentials or raw upstream exception text.

## Delivery and QA
Only Avernet sources/tests/docs; no OCB worktree/gitlink or deployment. Full Backend, live focused module coverage and remote CI are distinct gates. The orchestrator owns commit/rebase/PR and global log/OPTIMIZE updates.
