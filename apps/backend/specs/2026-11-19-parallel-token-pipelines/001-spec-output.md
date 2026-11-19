# Concurrent IAM token pipelines

## Requirement and scope

Run the existing Caller credential pipeline and the independent outbound token
plugin pipeline concurrently for IAM requests. Preserve input forwarding,
authenticated actor resolution, target validation, Caller result/error semantics,
and plugin failure isolation. Non-IAM credentials continue to skip plugins.
Do not change Caller target selection, plugin order, provider APIs, credentials,
or persistence contracts. The source PR targets `inclusionAI/Avernet` branch
`REL20261119`. No OCB worktree or gitlink-only change is required.

## Implementation plan

Use structured asyncio concurrency at the orchestrator boundary. Catch ordinary
Caller errors inside its branch, wait for both branches, then return the original
Caller outcome or raise its original error. Keep plugin/context error isolation.
Propagate request cancellation and await child cleanup. Offload the synchronous
plugin context Bot lookup and outbound append so neither blocks the event loop.
Keep individual plugins and Caller targets in their existing order. Add only
credential-free orchestration status and timing logs.

## Verification

- Event barriers prove Caller and plugin work overlap and the response awaits both.
- Caller errors do not cancel independent plugins and retain exception identity.
- Plugin/context failures retain the original Caller result.
- Cancellation terminates both coroutine branches without background tasks.
- Blocking Bot reads and outbound appends leave the event loop available.
- Existing actor, friendship, non-IAM, router, plugin and Caller tests pass.
- Run applicable lint/security and architecture checks; rebase onto the refreshed
  remote release branch, inspect the exact diff, and create a source PR.
- Review current-head remote checks and review comments; report pending checks
  separately from passed verification. No live-service latency claim without a
  deployed measurement; in-flight thread-backed I/O cannot be forcibly cancelled.

## Execution

Implemented with `asyncio.TaskGroup`; blocking Bot reads and appends use the
existing `asyncio.to_thread` pattern. Focused Caller, plugin, actor, API and
contract tests: 340 passed. Independent source review: PASS. Full Backend CI
and remote gates remain pending. Concurrent atomic append on the deployment's
provider is not established by local unit tests; this change preserves the
existing append contract and does not claim live provider validation.
