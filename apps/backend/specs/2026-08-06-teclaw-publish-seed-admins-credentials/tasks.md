# Tasks: Teclaw Service-Bot Publish — seed admins into the running `.credentials`

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: [ ] Verify preconditions (deps, ARCA-unaffected, binding stays draft)
- **Goal:** Confirm the three open assumptions before coding, so the fix shape is
  sound.
- **Files:** `core/bot_collaborator/services/collaborator_service.py` (grep
  `self._resolver_provider`, `self._device_fs_dispatcher_provider`);
  `core/service_bot/services/publish_flow/` + `baas_service.py` (does anything
  overwrite `ac_bots.binding_id` to the online binding post-publish?);
  `core/service_bot/repository/bot_publish_repository.py`
  (`get_latest_success_by_source_bot_id` + `ext.binding.online` shape); whether
  ARCA publish produces a divergent `ext.binding.online`.
- **Done when:**
  - [ ] Open Q2: `_resolver_provider` / `_device_fs_dispatcher_provider` are used
        only by `_sync_admins_to_credentials` (→ move to writer) OR another
        caller exists (→ keep, also inject writer).
  - [ ] Open Q3: ARCA has no `ext.binding.online` (→ fallback path unchanged) OR
        its online binding == `ac_bots.binding_id` (→ same target). Else scope the
        online-binding path to teclaw only.
  - [ ] Open Q5: confirm `ac_bots.binding_id` is NOT overwritten to the online
        binding after teclaw publish (so `resolve_for_bot` is definitively wrong
        for service bots). Document findings in plan.
- **Depends on:** —

## Task 2: [ ] Add `DeviceCredentialsAdminsWriter` (online-binding aware)
- **Goal:** Reusable `.credentials` ADMINS writer that resolves the **online**
  binding for service bots and reads-modify-writes the existing file (no create).
- **Files:** NEW `core/bot_collaborator/services/credentials_admins_writer.py`;
  move `_DEVICE_CREDENTIALS_PATH` + `_replace_admins_line` here (import back into
  `collaborator_service` if still referenced).
- **Done when:**
  - [ ] `__init__(collaborator_repo, bot_publish_repo, resolver_provider,
        device_fs_dispatcher_provider)`.
  - [ ] `seed_for_publish(binding_id, bot_id, owner_id)`: query
        `collaborator_repo.list_by_bot(role=ADMIN, env=current)` →
        `_write_for_binding(binding_id, bot_id, owner_id, admins)`.
  - [ ] `sync_on_change(bot_id, owner_id, admins)`: if
        `_resolve_online_binding_id(bot_id)` found → `_write_for_binding(online,
        …)`; else `_write_for_bot(bot_id, owner_id, admins)`.
  - [ ] `_resolve_online_binding_id(bot_id)`:
        `bot_publish_repo.get_latest_success_by_source_bot_id(bot_id, env)` →
        `(record.ext.get("binding") or {}).get("online")` (or None).
  - [ ] `_write_for_binding` → `resolver.resolve_for_binding(binding_id, owner_id,
        bot_id=bot_id)` → `_do_write`. `_write_for_bot` →
        `resolver.resolve_for_bot(bot_id, owner_id)` → `_do_write`.
  - [ ] `_do_write(ctx, admins)`: dispatch fs; `_rewrite_credentials_admins(fs,
        admins)`.
  - [ ] `_rewrite_credentials_admins(fs, admins)` (moved, **no create**):
        `read_file` None → log info, skip; else write
        `_replace_admins_line(raw.decode(), admins)`.
  - [ ] `DeviceNotBoundError` / `UnknownProviderError` → log info, skip. Transport
        error → raise out of `seed_for_publish` (handler Retry) / warn+swallow in
        `sync_on_change`.
- **Depends on:** Task 1

## Task 3: [ ] Route `on_collaboration_changed` through the writer
- **Goal:** Runtime collaborator-change sync resolves the online binding for
  service bots; non-service unchanged.
- **Files:** `core/bot_collaborator/services/collaborator_service.py`.
- **Done when:**
  - [ ] `__init__` injects `credentials_admins_writer` (drop/keep resolver deps
        per Task 1).
  - [ ] `on_collaboration_changed` calls `writer.sync_on_change(...)` instead of
        inline `_sync_admins_to_credentials`.
  - [ ] removed `_sync_admins_to_credentials` / `_rewrite_credentials_admins`
        (moved).
  - [ ] existing `collaborator_service` tests green (non-service: read
        `.credentials`, preserve TOKEN, empty→`ADMINS=`).
- **Depends on:** Task 2

## Task 4: [ ] Seed admins in `TeclawPublishTaskHandler` post-publish
- **Goal:** teclaw publish SUCCESS writes `ADMINS=` into the **online** container;
  idempotent, replay-safe.
- **Files:** `core/bot_management/services/teclaw_publish_task_handler.py`.
- **Done when:**
  - [ ] `__init__` accepts `credentials_admins_writer`.
  - [ ] `_seed_admins(bot_id, owner_id, binding)`: call
        `writer.seed_for_publish(binding.id, bot_id, owner_id)`; DeviceNotBound/
        UnknownProvider → Complete; other exception → Retry.
  - [ ] post-publish flow runs token-then-seed after device-ready success (both
        the `len(updated) > 0` and `updated is None` branches); token
        Reschedule/Retry short-circuits seed.
  - [ ] crash-resume (ACTIVE re-enter) replays seed; token + seed both idempotent.
  - [ ] `TeclawPublishTaskLifecycle.bootstrap` forwards the writer.
- **Depends on:** Task 2, Task 3

## Task 5: [ ] DI wiring
- **Goal:** Bind the writer; inject into the lifecycle/handler/service.
- **Files:** `di/modules/bot_collaborator_module.py`,
  `di/modules/bot_management_module.py`.
- **Done when:**
  - [ ] `@provider @singleton` for `DeviceCredentialsAdminsWriter` (deps
        collaborator_repo, bot_publish_repo, resolver thunk, dispatcher thunk).
  - [ ] `teclaw_publish_task_lifecycle` injects + forwards the writer.
  - [ ] `CollaboratorService` provider injects the writer.
  - [ ] app boots; existing DI tests green.
- **Depends on:** Task 2, Task 3, Task 4

## Task 6: [ ] Tests & Verification (TDD, no mocks)
- **Goal:** Prove every acceptance criterion; write red first.
- **Files:** NEW
  `tests/community/core/bot_collaborator/test_credentials_admins_writer.py`;
  `tests/community/core/bot_management/services/test_teclaw_publish_task_handler.py`.
- **Done when:**
  - [ ] writer: `seed_for_publish` uses `resolve_for_binding`; service-bot
        `sync_on_change` uses the online binding (not `resolve_for_bot`); no-
        publish-record bot uses `resolve_for_bot`; `.credentials` present→preserve
        TOKEN/CLIENT_ID, change only `ADMINS=`; `admins=[]`→`ADMINS=`; missing
        file→skip (no create); no-device→skip; write-raise→seed propagates /
        sync swallows; missing `ext.binding.online`→fallback.
  - [ ] handler: SUCCESS+ready→seed once with online `binding.id`; seed
        raise→Retry→converges; ACTIVE resume→replay idempotent; no-egress→still
        seeds.
  - [ ] regression: collaborator_service + handler existing tests green; full
        `tests/community` green.
  - [ ] SAST + changed-line coverage pass pre-push.
- **Depends on:** Task 1–5

---

## Groups

- **Group A — Preconditions (Task 1):** confirm dep-narrowing, ARCA-unaffected,
  binding-stays-draft. De-risks the shape.
- **Group B — Writer (Task 2):** the reusable, online-binding-aware `.credentials`
  admins writer. Self-contained; no behavior change yet.
- **Group C — Wire-up (Tasks 3, 4, 5):** runtime sync + publish seed + DI.
  Behavior changes land here.
- **Group D — Verification (Task 6):** TDD coverage of every acceptance
  criterion.