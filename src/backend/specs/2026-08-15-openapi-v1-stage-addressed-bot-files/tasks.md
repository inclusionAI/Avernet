# Tasks: Public API — Stage-Addressed Per-Bot Files

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> Base: `dev`, **after PR #1074 lands** — a hard ordering dependency. The five
> addresses below are its bot-first ones, and the two suites Group D extends are
> the ones it rewrites. Do not start Group C before it is in.
>
> PR #1073 has merged (`a5afd54`); Task B1 edits `engine_config.py` as it left
> it, through its `_read_config_bytes` helper. Nothing to rebase.
>
> Groups run in order. Within a group the tasks may be done together.

---

## Group A — Core: the stage seam and the refusal

### Task A1: A core error for a write addressed to a published runtime  `[x]`

- **Goal:** One named domain state for "that runtime does not take writes",
  distinct from "that runtime is not up".
- **Files:** `src/backend/src/agentclaw/community/core/engine_runtime/errors.py`
- **Done when:**
  - [x] `EngineStageReadOnlyError(EngineRuntimeError)` exists, docstring says
        why a published runtime is replaced rather than edited, and states
        explicitly that it is **not** `EngineStageNotLiveError` — it does not
        depend on liveness, so publishing a runtime would not make the write
        land.
  - [x] Listed in `__all__`, placed above the `EngineRuntimeError` base's own
        entry in nothing (this file has no ordering rule; `responses.py` does).
- **Depends on:** —

### Task A2: `BotFacts.from_record`, `require_stage_writable`, `resolve_published_device_context`  `[x]`

- **Goal:** A published stage → `DeviceContext`, in one place, for the surfaces
  that read a bot's files — so they cannot drift from the surfaces that forward
  to it. Carried on the bot-identity model the codebase already has.
- **Files:**
  `src/backend/src/agentclaw/community/core/engine_runtime/models.py`,
  `…/core/engine_runtime/stage.py`,
  `…/core/engine_runtime/relay.py`,
  `…/core/engine_runtime/connection.py` (its inline projection, same reason)
- **Done when:**
  - [x] `BotFacts.from_record(record, *, bot_id, owner_id)` classmethod, with the
        same `or`-fallbacks `relay.resolve_bot` applies today, and a docstring
        saying the record must come from an owner-scoped read (spec D6).
  - [x] `relay.resolve_bot`'s and `EngineConnectionService.build`'s inline
        `BotFacts(...)` constructions use it, so there is **one** projection of
        a bot row into engine-runtime facts. These are the only edits outside
        the feature and are reversible on their own. Nothing else in either
        file changes — an owner-argument inconsistency noticed in
        `_stage_binding_id` is left alone as pre-existing and out of scope.
  - [x] `require_stage_writable(stage)` raises `EngineStageReadOnlyError` for
        anything but the draft; docstring says why it is not conditional on bot
        type or liveness.
  - [x] `resolve_published_device_context(resolver, publish_repo, binding_repo,
        *, facts: BotFacts, stage: str)`: `require_stage_addressable(
        facts.bot_type, stage)` first, then `resolve_stage_bind_id(...)` keyed
        on `facts.bot_pk`, then `resolve_for_binding(bind_id, facts.owner_id,
        bot_id=facts.bot_id)`.
  - [x] Its docstring says **why published-only** — a draft leg would need facts
        the draft path must not read (spec D8) — and why `resolve_for_binding`
        rather than the relay's `…_invoke` (a filesystem needs the full conn
        info). It names `relay._resolve_published_device` as its sibling and
        `resolve_stage_bind_id` as the rule they share.
  - [x] Module docstring gains the *other question* paragraph: stage-keyed comes
        here, record-keyed goes to `select_stage_bind_id`, and why routing a
        `publish_id` through this rule would answer about the wrong release.
  - [x] `__all__` updated. `ruff check` clean.
- **Depends on:** A1

### Task A3: Map the refusal to 409  `[x]`

- **Goal:** The new error leaves the envelope as a fixed-message 409, not a 500.
- **Files:** `src/backend/src/agentclaw/community/adapters/http/openapi_v1/responses.py`
- **Done when:**
  - [x] `EngineStageReadOnlyError: (409, "The requested stage is read-only")`,
        placed inside the engine-runtime block **above** the
        `EngineRuntimeError` base (the block's stated ordering rule).
  - [x] Comment says why it is a separate answer from
        `EngineStageNotLiveError` and from a `200` with a no-op flag.
- **Depends on:** A1

---

## Group B — Core services

### Task B1: `EngineConfigService` reads a stage, writes only the draft  `[x]`

- **Goal:** The engine-config read reaches all three runtimes; the write reaches
  one and refuses the rest before touching a device.
- **Files:**
  `src/backend/src/agentclaw/community/core/services/engine_config.py`,
  `src/backend/src/agentclaw/community/api/engine_config_service.py`
- **Done when:**
  - [x] Constructor takes `publish_repo: BotPublishRepositoryProtocol` and
        `binding_repo: DeviceBindingRepository`, with a comment that both exist
        only for the stage-addressed read.
  - [x] `_bot_facts(bot_id, owner_id)` builds `BotFacts` from
        `self._bot_repo.get_by_id_and_owner`, raising `BotNotFoundError` on a
        miss. No new constructor dependency — the service already holds the
        repository.
  - [x] `_bot_config_device_fs(..., stage)` branches: draft → the existing
        `resolve_for_bot(bot_id, owner_id)` **unchanged and with no row read**;
        published → `_bot_facts` then `resolve_published_device_context`.
  - [x] `read_bot_config(..., stage: str)` — required, not defaulted.
  - [x] `write_bot_config(..., stage: str)` calls `require_stage_writable(stage)`
        as its **first** statement, then takes the draft branch. It must never
        reach `_bot_facts`.
  - [x] `read_publish_config` unchanged in behaviour, with a comment at the
        `select_stage_bind_id` call saying why it is deliberately not the stage
        rule (spec D3).
  - [x] `EngineConfigServiceProtocol` mirrors both signatures exactly; its
        class docstring records why `stage` is required rather than defaulted —
        `EngineRuntimeRelayProtocol` states the same convention, and a default
        would also need a runtime `api → core.engine_runtime` import, which hits
        a partially-initialised `bot_service`.
  - [x] Module and class docstrings updated to describe three addresses, two of
        them read-only.
  - [x] `tests/community/architecture/test_service_api_conformance.py` passes.
- **Depends on:** A2

### Task B2: `IdentityService` reads a stage, writes only the draft  `[x]`

- **Goal:** Same contract for the identity files, threaded through the layers
  that actually reach the device.
- **Files:** `src/backend/src/agentclaw/community/core/services/identity.py`
- **Done when:**
  - [x] Constructor takes `binding_repo: DeviceBindingRepository`.
  - [x] `_bot_facts(bot_id, owner_id)` as in B1 — same shape, same reason.
  - [x] `_identity_device_fs`, `_device_read`, `read_identity_file` take
        `stage: str = STAGE_DRAFT`; `_identity_device_fs` carries the same
        draft/published branch B1 describes.
  - [x] `get_bot_file` and `list_bot_files` take keyword-only
        `stage: str = STAGE_DRAFT` and pass it down; `list_bot_files` probes all
        16 files against the **one** addressed runtime, resolving the facts once
        rather than per file.
  - [x] `get_bot_file`'s docstring states the precedence: `publish_id` (a
        record) wins over `stage`, because it names one exact release, and
        because it is the older internal contract.
  - [x] `update_bot_file` takes keyword-only `stage: str = STAGE_DRAFT` and
        calls `require_stage_writable(stage)` first; the write path below it is
        unchanged and never resolves facts.
  - [x] `_read_from_publish_device` unchanged in behaviour, with the same
        record-keyed-vs-stage-keyed comment as B1.
  - [x] `write_identity_file` / `sync_agents_md` untouched — draft-only by
        construction.
- **Depends on:** A2

### Task B3: Fix up direct service construction in tests  `[x]`

- **Goal:** The new constructor dependencies do not silently break unrelated
  suites.
- **Files:**
  `tests/community/unit/harness/test_patch_engine.py`,
  `tests/community/unit/harness/test_bot_profile.py`,
  `tests/community/core/services/test_identity_provider_blind.py` (×2),
  `tests/community/core/services/test_identity_service_coverage.py`,
  `tests/community/core/services/test_identity_uses_resolver.py`,
  `tests/community/adapters/http/test_http_adapters_use_resolver.py`,
  `tests/community/adapters/http/test_identity_tenant_indirect_isolation.py`,
  `tests/community/core/services/test_engine_config_service.py` (×2)
- **Done when:**
  - [x] Every direct `IdentityService(...)` passes `binding_repo`; every direct
        `EngineConfigService(...)` passes `publish_repo` and `binding_repo`.
  - [x] `read_bot_config` / `write_bot_config` call sites in
        `test_engine_config_service.py` pass `stage=STAGE_DRAFT`.
  - [x] Those suites pass unchanged otherwise.
- **Depends on:** B1, B2

---

## Group C — Adapters

### Task C1: The write-side parameter  `[ ]`

- **Goal:** One parameter, two published descriptions — the write's must not
  advertise verify/online as addressable.
- **Files:** `src/backend/src/agentclaw/community/adapters/http/openapi_v1/engine_runtime/params.py`
- **Done when:**
  - [ ] `WRITE_STAGE_DESCRIPTION` states that only the draft accepts writes,
        that a published runtime is replaced by publishing again, and that
        naming one is a 409 with nothing written.
  - [ ] `WriteStageQuery` alongside `StageQuery`; both exported.
  - [ ] Module docstring records that `stage` is now imported outside this
        group (the per-bot file operations) and why it still lives here.
- **Depends on:** —

### Task C2: `stage` on the two engine-config operations  `[ ]`

- **Goal:** `GET /openapi/v1/bots/{bot_id}/engine/config` serves three runtimes;
  `PUT` of the same address refuses two.
- **Files:** the engine-config router #1074's Task 15 mounts at
  `/openapi/v1/bots/{bot_id}/engine` — it moves the two handlers out of
  `adapters/http/openapi_v1/bots/router.py` unchanged, so
  `_engine_config_target` travels with them. **Start by locating them**; if Task
  15 shipped after #1074 rather than in it, they are still in `bots/router.py`
  at `/{bot_id}/engine-config` and the edit is the same apart from the file.
- **Done when:**
  - [ ] `get_bot_engine_config(..., stage: StageQuery = RuntimeStage.DRAFT)`
        passes `stage=stage.value`. No adapter-side helper and no extra
        lookup — the service resolves its own facts (spec D8).
  - [ ] `update_bot_engine_config(..., stage: WriteStageQuery =
        RuntimeStage.DRAFT)` passes `stage=stage.value`.
  - [ ] Both docstrings are caller-facing prose only (they are published
        verbatim); rationale stays in `#` comments.
  - [ ] The **deprecated** `/{bot_id}/engine-config` shim is left alone: it
        declares no `stage`, and its handler keeps calling the service with
        `stage=STAGE_DRAFT` (#1074 froze its contract).
- **Depends on:** B1, C1, and #1074

### Task C3: `stage` on the three identity operations  `[ ]`

- **Goal:** Same contract on `/openapi/v1/bots/{bot_id}/identity` and
  `…/identity/{file_type}`, without changing the draft path.
- **Files:** `src/backend/src/agentclaw/community/adapters/http/openapi_v1/identity/router.py`
  (already mounted bot-first by #1074's Task 4)
- **Done when:**
  - [ ] `list_bot_identity_files` and `get_bot_identity_file` take
        `stage: StageQuery = RuntimeStage.DRAFT` and pass `stage=stage.value`.
  - [ ] `update_bot_identity_file` takes `stage: WriteStageQuery =
        RuntimeStage.DRAFT` and passes `stage=stage.value`.
  - [ ] **No `BotRepository` injection and no bot lookup anywhere in this
        router** — the earlier draft had a conditional one; resolving the facts
        in the service removed the need. The draft path still reads no bot row,
        so its failure mode is unchanged (409, not 404).
  - [ ] The stale "Reads address the bot's draft runtime." line in
        `get_bot_identity_file`'s published docstring is corrected.
  - [ ] The direct-invocation tests in
        `tests/…/openapi_v1/identity/test_identity_handlers.py` are updated for
        the new handler parameters (their stub gains `address`/`stage`).
  - [ ] The **deprecated** `…/bots/identity/{bot_id}` shims are left alone —
        no `stage`, draft only.
- **Depends on:** B2, C1, and #1074

### Task C4: The internal routes say `draft` out loud  `[x]`

- **Goal:** The two now-required arguments are supplied, and the internal
  console's draft-only scope is stated rather than inherited.
- **Files:** `src/backend/src/agentclaw/community/adapters/http/bot_management/router.py`
- **Done when:**
  - [x] `read_bot_config(..., stage=STAGE_DRAFT)` and
        `write_bot_config(..., stage=STAGE_DRAFT)`, with a comment that naming a
        runtime is the public surface's parameter, not this route's.
  - [x] `tests/community/api/bot_management/test_router.py` passes.
- **Note:** landed inside B1's commit rather than its own. Making `stage`
  required breaks these two call sites the moment the service changes, so
  splitting them would have left one commit that does not run.
- **Depends on:** B1

---

## Group D — Tests

### Task D1: Core stage seam  `[ ]`

- **Files:** `tests/community/core/engine_runtime/test_stage.py`
- **Done when:**
  - [ ] `require_stage_writable`: draft passes; verify and online raise
        `EngineStageReadOnlyError`.
  - [ ] `resolve_published_device_context`: verify/online resolve the publish
        record's binding and call `resolve_for_binding` with `facts.owner_id`
        and `facts.bot_id`; a published stage on a `personal` bot raises
        `EngineStageNotLiveError` **before** any resolver call; a draft stage
        raises `ValueError` naming the call to make instead. (Round-1 review:
        falling through to `resolve_stage_bind_id`'s generic "not a published
        stage" left the caller without the fix. The guard is local so the
        message can name `resolve_for_bot`.)
  - [ ] `BotFacts.from_record` applies the same fallbacks the relay applied
        inline, and `relay.resolve_bot` still returns identical facts — the
        existing relay tests are the check.
- **Depends on:** A2

### Task D2: Service-level behaviour  `[ ]`

- **Files:**
  `tests/community/core/services/test_engine_config_service.py`,
  new `tests/community/core/services/test_identity_stage_addressing.py`
- **Done when:**
  - [ ] Engine-config: a verify/online read resolves through the publish
        record's binding and reads the same canonical `config/teclaw.json`
        path.
  - [ ] Engine-config: a verify/online write raises
        `EngineStageReadOnlyError` and the dispatcher is **never** called —
        the "nothing is written" claim, asserted rather than described.
  - [ ] Identity: the same two pins for `get_bot_file`, plus `list_bot_files`
        probing the addressed runtime, plus the write refusal.
  - [ ] Identity: `get_bot_file` with both `publish_id` and a published
        `address` takes the record-keyed branch (the documented precedence).
- **Depends on:** B1, B2

### Task D3: HTTP behaviour  `[ ]`

- **Files:** new `tests/community/adapters/http/openapi_v1/test_stage_addressed_bot_files.py`
- **Done when:**
  - [ ] Each of the three reads — `GET …/{bot_id}/engine/config`,
        `GET …/{bot_id}/identity`, `GET …/{bot_id}/identity/{file_type}` —
        with `stage=draft|verify|online` hands the service the matching
        stage string.
  - [ ] No parameter → `stage="draft"`, and the draft path reads **no bot
        row** — assert the repository was not touched (the byte-for-byte pin).
  - [ ] Both writes with `stage=verify|online` → `409` and the body message
        `"The requested stage is read-only"`; with no parameter → unchanged
        `200`.
  - [ ] A published stage on a personal bot read → `409 "No live runtime at the
        requested stage"`.
  - [ ] `stage=eval` → `422`, no handler reached.
  - [ ] The deprecated twins still read the draft when `?stage=online` is sent
        at them — FastAPI ignores the undeclared parameter, and this pins that
        the shim was not wired to the new code path by accident.
- **Depends on:** C2, C3

### Task D4: The published document  `[ ]`

- **Files:** `tests/community/adapters/http/openapi_v1/engine_runtime/test_stage_addressing.py`
- **Done when:**
  - [ ] Build on #1074's `_engine_runtime_paths()` — membership in the mounted
        route set, not a segment match (spec **Open Questions**, resolved). The
        five operations here are not in that set, so they need no change to the
        predicate; do not add a second classification.
  - [ ] A `_STAGE_ADDRESSED_ELSEWHERE` set names the five bot-first operations,
        mirroring the existing `_OWNER_ADDRESSED_ELSEWHERE`, so
        `test_owner_id_and_stage_are_on_exactly_the_engine_runtime_operations`
        stays an **exact** assertion (16 + 5) rather than being loosened.
  - [ ] `stage` is still optional on every operation carrying it, and still
        never a body field or a path segment.
  - [ ] **None of the five retiring twins carries `stage`** — and note the
        distinction: the retiring *engine-runtime* addresses do carry it and
        must keep doing so (they always had it, and #1074's parity promise is
        that they keep their contract). The claim is about these five only.
  - [ ] The module docstring is updated: "sixteen … and nowhere else" is no
        longer true.
- **Depends on:** C2, C3

---

## Group E — Documentation

### Task E1: `docs/openapi-v1/README.md`  `[ ]`

- **Goal:** The operator-facing description matches the surface.
- **Files:** `src/backend/docs/openapi-v1/README.md`
- **Done when:**
  - [ ] The `?owner_id=`/`?stage=` section says `stage` reaches the five
        bot-first per-bot file operations too, and that on the two writes only
        the draft is writable. Its example URLs use the bot-first form #1074's
        Task 32 leaves behind (`/openapi/v1/bots/{bot_id}/engine/status?stage=`,
        not `/openapi/v1/bots/engine/{bot_id}/status?stage=`).
  - [ ] It states that the deprecated addresses do not take `stage`, in the
        deprecation section #1074's Task 32 adds rather than as a new one.
  - [ ] The startup-script finding is recorded where a reader looking for it
        would land: those operations are storage-keyed, not runtime-keyed, and
        deliberately take no stage.
  - [ ] Resources / skills / routines are named as the remaining draft-only
        device surfaces, with the reason each is deferred (spec Out of Scope).
  - [ ] MCP is named too, and correctly: its six operations address no bot, but
        its config write fans out to every bot's draft device. Draft-only is
        the right answer there for the same reason the writes here refuse a
        published stage — say so, so a later reader does not file it as a gap.
- **Depends on:** C2, C3

---

## Verification

- [ ] `ruff check` clean on every changed file.
- [ ] `tests/community/architecture` — passes (conformance + boundary gates).
- [ ] `tests/community/core/engine_runtime`, `tests/community/core/services`,
      `tests/community/adapters/http/openapi_v1`,
      `tests/community/api/bot_management` — pass.
- [ ] `scripts/ci/pre_push.sh` per the AGENTS.md contract (lint-only by
      default; `OCB_PRE_PUSH_RUN_CI=1` for the full module gates).
- [ ] PR titled `feat(backend): address published runtimes on the per-bot file
      endpoints`, body using the Problem / Solution / Validation template.
