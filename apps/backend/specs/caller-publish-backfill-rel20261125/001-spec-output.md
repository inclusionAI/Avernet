# Minimal Caller publication progress reconciliation

## Scope

Restore publication progress advancement for already allocated independent Caller session lists and connections. The user explicitly requests simplification of the initial patch. Remove its repository transaction/guard extension, standalone progress mixin, provider logging option and their dedicated tests. Reuse the existing instance-service progress query and writeback implementation instead. No OCB change, new worktree, deployment, merge or new PR is in scope.

Base: GitHub inclusionAI/Avernet REL20261125. Keep one final task commit. Existing PR4377 remains open; updating its rewritten topic head requires the separately coordinated push authorization.

## Minimal implementation

1. Extract the existing `get_caller_connection` publication progress block into one private method in `expert_chat_instance_service.py`. Reuse its BaaS query, binding-status mapping, identity initialization order, instance update and existing diagnostic logs; call it from the original lifecycle path as well.
2. Add only the existing-instance entrypoint required by the session service and its Protocol. It reads the exact `(user_id, bot_id, owner_id)` record, and calls the shared method only for `init` with registered UUID, binding and publication IDs. Missing/incomplete/terminal rows never create, upgrade, restart or poll a replacement.
3. Keep the necessary async list/connect preparation wiring. Pending init connections return `need_poll`; terminal failed connections retain their existing unavailable error. Ready sessions are still verified on the subject's fixed runtime.
4. Preserve the necessary actor/subject IAM guard: an inspecting owner's IAM is not installed as the friend's Caller. Shared Teclaw, status-only APIs and Backend session-index policy remain unchanged.
5. Reuse the existing repository `update_status`/`update_instance` methods and their existing expiry semantics. Do not add a repository API, CAS, transaction/lock framework, schema, scheduler or endpoint. This fix does not expand the pre-existing concurrency contract.
6. Restore the BaaS adapter to the target base: the extracted operation uses its established query and diagnostics without a new logging subsystem or logging-option API. Do not add new raw payload/credential logs.

## Models and compatibility

Reuse the existing Caller ledger, allocation identifiers, publication snapshot and binding state. Publication status maps to existing `init/success/failed` and `PENDING/ACTIVE/FAILED`. Existing Caller identity initialization behavior, including missing-IAM skip and its historical error policy, is retained. No serialization or database migration is added.

## Verification and review

- Demonstrate real list and existing-session connect progressing from provider ACTIVE to SUCCESS, including both binding and instance writeback through existing repositories.
- Cover missing/incomplete allocation, terminal failure/success without restart or repeated polling, and same-actor versus inspecting-owner IAM behavior.
- Existing lifecycle unit tests must exercise the same shared method, showing that extraction preserves prior behavior.
- Keep necessary runtime diagnostics and verify no newly added credential dump. Run focused expert-chat/related adapter tests, static/SAST/secret checks and diff review first, then one full Backend CI pass on the frozen candidate.
- Independent review checks that removed files/APIs are absent from the final base diff, runtime session authorization remains intact, and only task-required code remains. Remote PR checks are separate from local evidence.

## Delivery

Back up the former local head and preserve prior reports. Rebase the simplified task onto the latest GitHub REL20261125, retain one commit, and keep PR4377. Do not push a history rewrite or create a duplicate PR before the main agent completes the required authorization step. Report final diff size and accurate test/coverage results; do not deploy or merge.
