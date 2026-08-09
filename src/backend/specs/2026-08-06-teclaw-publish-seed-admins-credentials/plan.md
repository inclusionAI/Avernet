# Plan: Teclaw Service-Bot Publish — seed admins into the running container's `.credentials`

## Approach

Both gaps share one root: the admins write reaches the wrong (or no) binding for
a teclaw service bot. Close both by routing the write through the **online**
binding:

- **Publish-time seed.** `TeclawPublishTaskHandler` already polls the online
  teclaw binding (its `binding_id`). At publish SUCCESS, after the agentpass token
  is delivered, resolve that binding via `resolve_for_binding` and read-modify-
  write the `ADMINS=` line into the existing `.credentials`. Retried on failure,
  idempotent under replay.
- **Runtime sync.** `on_collaboration_changed` currently resolves the device via
  `resolve_for_bot` → `ac_bots.binding_id` (the DRAFT for service bots). Replace
  with: look up the online binding id from the publish record
  (`get_latest_success_by_source_bot_id` → `ext.binding.online`) and use
  `resolve_for_binding`; fall back to `resolve_for_bot` when there is no publish
  record (ARCA/personal/desktop, unchanged).

Extract the `.credentials` admins write into a reusable
`DeviceCredentialsAdminsWriter` that both paths call. Read-modify-write only
(file exists; engine rejects ADMINS-only) — **no `create_if_missing`**. The
engine hot-reloads `.credentials`, so runtime writes take effect immediately.

## Affected Components

- `core/bot_collaborator/services/credentials_admins_writer.py` (NEW) —
  `DeviceCredentialsAdminsWriter`.
- `core/bot_collaborator/services/collaborator_service.py` — replace inline
  `_sync_admins_to_credentials` / `_rewrite_credentials_admins` with the injected
  writer; move `_DEVICE_CREDENTIALS_PATH` + `_replace_admins_line` into the
  writer module (import back if still referenced).
- `core/bot_management/services/teclaw_publish_task_handler.py` — inject the
  writer; call `seed_for_publish` after token delivery.
- `di/modules/bot_collaborator_module.py` — bind
  `DeviceCredentialsAdminsWriter` (deps: collaborator_repo, bot_publish_repo,
  resolver thunk, dispatcher thunk).
- `di/modules/bot_management_module.py` — `teclaw_publish_task_lifecycle`
  injects the writer.
- `tests/community/core/bot_collaborator/test_credentials_admins_writer.py`
  (NEW).
- `tests/community/core/bot_management/services/test_teclaw_publish_task_handler.py`
  — extend.

Unchanged (deliberately): `BotConfigArtifact`, `create_teclaw_bot` /
`_build_teclaw_payload`, `BotBuildService.release` teclaw branch, the ARCA /
personal / desktop paths.

## API / Interface Changes

- NEW `DeviceCredentialsAdminsWriter`:
  - `seed_for_publish(binding_id: int, bot_id: str, owner_id: str) -> None` —
    query admins, then `_write_for_binding(binding_id, ...)`.
  - `sync_on_change(bot_id: str, owner_id: str, admins: list[str]) -> None` —
    resolve online binding id from the publish record; if found, `_write_for_binding`;
    else `_write_for_bot` (today's behavior).
  - private `_write_for_binding(binding_id, bot_id, owner_id, admins)` —
    `resolver.resolve_for_binding(binding_id, owner_id, bot_id=bot_id)` →
    `_do_write`.
  - private `_write_for_bot(bot_id, owner_id, admins)` —
    `resolver.resolve_for_bot(bot_id, owner_id)` → `_do_write`.
  - private `_resolve_online_binding_id(bot_id) -> int | None` —
    `bot_publish_repo.get_latest_success_by_source_bot_id(bot_id, env)` →
    `(record.ext.get("binding") or {}).get("online")`.
  - private `_do_write(ctx, admins)` — dispatch fs; read-modify-write `ADMINS=`.

No changes to existing protocols. The writer is a new internal collaborator.

## Key Files & Functions

- `credentials_admins_writer.py` (NEW):
  - module constants: `_DEVICE_CREDENTIALS_PATH = "/home/admin/.credentials"`,
    `_replace_admins_line(content, admins)` (moved, unchanged — line-preserving
    replace/append of `ADMINS=`).
  - `__init__(collaborator_repo, bot_publish_repo, resolver_provider,
    device_fs_dispatcher_provider)`.
  - `_do_write(ctx, admins)`:
    ```
    fs = device_fs_dispatcher_provider().dispatch(ctx)
    _run_coro_blocking(_rewrite_credentials_admins(fs, admins))
    ```
  - `_rewrite_credentials_admins(fs, admins)` (moved, **no `create_if_missing`**):
    `raw = await fs.read_file(_DEVICE_CREDENTIALS_PATH)`; if `raw is None` → log
    info, skip (file should exist; defensive); else
    `await fs.write_file(..., _replace_admins_line(raw.decode(), admins))`.
  - `DeviceNotBoundError` / `UnknownProviderError` → log info, skip (no raise).
    `write_file` / transport error → **raise** (publish handler turns into Retry);
    `sync_on_change` warns and swallows (never break the collab-change flow).
- `collaborator_service.py`:
  - `__init__`: drop `resolver_provider` / `device_fs_dispatcher_provider` **iff**
    Task 1 / Open Q2 confirms no other caller; accept `credentials_admins_writer`.
  - `on_collaboration_changed` (`:767`): replace
    `self._sync_admins_to_credentials(...)` with
    `self._creds_writer.sync_on_change(bot_id, owner_id_from_bot, admins)`.
  - delete `_sync_admins_to_credentials` and `_rewrite_credentials_admins`
    (moved); import `_replace_admins_line` / `_DEVICE_CREDENTIALS_PATH` back only
    if still referenced.
- `teclaw_publish_task_handler.py`:
  - `__init__` (`:96`): add `credentials_admins_writer`.
  - `_seed_admins(bot_id, owner_id, binding) -> TaskOutcome`:
    ```
    try:
        self._writer.seed_for_publish(binding.id, bot_id, owner_id)
    except (DeviceNotBoundError, UnknownProviderError):
        return Complete()          # nothing to write to
    except Exception as exc:
        return Retry(f"seed admins failed: {exc}")
    return Complete()
    ```
  - Wire it so it runs **after** the container is ACTIVE and the device is
    reachable. Concretely: in `_persist_terminal` (`:181`) on
    `applied and status == ACTIVE`, and in the crash-resume path (`:143-148`,
    ACTIVE re-enter), both routes go through `_deliver_outbound_rule`; the seed
    fires in its **device-ready success tail** (the `len(updated) > 0` branch,
    `:326`) — i.e. the same device-readiness signal as the token delivery. The
    `updated is None` (no-egress-provider) branch completes without seeding: it
    is unreachable for standard teclaw (the agentpass rule is always built) and
    does not confirm device readiness. Token Reschedule / Retry short-circuits
    seed. Seed failure → Retry (re-enter ACTIVE, replay token [idempotent
    REPLACE] + seed [idempotent read-modify-write]). `binding` is the record
    loaded at `:126`; `binding.id` is the online teclaw binding;
    `owner_id = binding.entity_id`.
  - `TeclawPublishTaskLifecycle.bootstrap` (`:352`): forward the writer into the
    handler.
- DI:
  - `bot_collaborator_module.py`: `@provider @singleton` for
    `DeviceCredentialsAdminsWriter` (deps collaborator_repo, bot_publish_repo,
    `Callable[[], DeviceContextResolver]`, `Callable[[], DeviceFilesystemDispatcher]`).
  - `CollaboratorService` provider: inject the writer (drop resolver / dispatcher
    thunks per Task 1).
  - `bot_management_module.py:389` `teclaw_publish_task_lifecycle`: inject +
    forward the writer.

## Dependencies

None new. Reuses `DeviceContextResolver.resolve_for_binding`,
`DeviceFilesystemDispatcher`, `TeclawDeviceFileSystem`,
`BotCollaboratorRepository.list_by_bot`,
`BotPublishRepository.get_latest_success_by_source_bot_id`, the durable task
queue.

## Risks & Mitigations

- **Risk:** "Prefer online binding" changes ARCA behavior. **Mitigation:** ARCA
  has no publish record → falls back to `resolve_for_bot` (unchanged). Verify in
  Task 1 (Open Q3); if an ARCA publish also yields a divergent `ext.binding.online`,
  scope the online path to teclaw.
- **Risk:** `get_latest_success_by_source_bot_id` returns a stale online binding
  after a re-publish/rollback. **Mitigation:** it returns the **latest** success
  record; re-publish writes a new `ext.binding.online`. Confirm the binding id is
  current for the running container (Task 1 / Task 6 test).
- **Risk:** Seed runs before the container file API is reachable → noisy Retries.
  **Mitigation:** gate on the same device-readiness signal as token delivery;
  transient failures Retry within the task deadline.
- **Risk:** Folding seed into the replay path breaks token-delivery idempotency.
  **Mitigation:** seed is a read-modify-write (idempotent); token push is a
  REPLACE (idempotent). Replaying both converges. Test crash-resume explicitly.
- **Risk:** Moving `_resolver_provider` / `_device_fs_dispatcher_provider` off
  `CollaboratorService` breaks another caller. **Mitigation:** grep-verify (Task
  1); keep as-is if any other caller exists.

## Alternatives Considered

- **Carry admins in `BotConfigArtifact` (SCHEMA_VERSION 5).** Rejected: the
  engine reads admins from `.credentials`, not the artifact; a contract bump +
  engine-side change is unnecessary scope.
- **Create `.credentials` with only `ADMINS=`.** Rejected: engine owner confirms
  the engine rejects an ADMINS-only file; the file already exists anyway, so
  read-modify-write is both sufficient and required.
- **Keep `resolve_for_bot` at runtime and only add publish-seed.** Rejected: the
  runtime path resolves the DRAFT binding for service bots, so post-publish
  collaborator changes would never reach the running container — the user's
  follow-up concern. The runtime resolver fix is necessary for a complete fix.
- **Inject `CollaboratorService` directly into the handler** instead of a narrow
  writer. Rejected: fat dep, wider test surface, drags the collab-change path's
  concerns into the publish handler. The narrow writer keeps the seam testable
  and matches the repo's composition-seam discipline.
- **Seed inline at `BotBuildService.release` teclaw branch.** Rejected: the
  container is not ready at that instant (BaaS create is async; device not yet
  provisioned) — `TeclawPublishTaskHandler` exists precisely because post-publish
  delivery must wait for readiness.

## Rollout

- No schema migration, no feature flag. The fix activates for teclaw service bots
  on next publish. Already-published teclaw containers self-heal on the next
  collaborator change (the runtime `on_collaboration_changed` fix now resolves the
  online binding); a manual re-publish also seeds.
- ARCA / personal / desktop byte-for-byte unchanged.
- Backward compat: the writer's non-service branch is identical to today's
  `_sync_admins_to_credentials` (`resolve_for_bot` + read-modify-write +
  skip-if-missing).

## Test Strategy

Unit (`tests/community/core/bot_collaborator/test_credentials_admins_writer.py`,
in-memory fake `DeviceFileSystem` + fake collaborator repo + fake publish repo):
- `seed_for_publish(binding_id, …)` → `resolve_for_binding` used (not
  `resolve_for_bot`); admins = repo `role=ADMIN` for current env; `.credentials`
  read-modify-write preserves TOKEN/CLIENT_ID, changes only `ADMINS=`.
- `sync_on_change` for a service bot (publish repo returns a record with
  `ext.binding.online`) → `_write_for_binding(online)`; the running container's
  `.credentials` `ADMINS=` updated; `resolve_for_bot` NOT used.
- `sync_on_change` for a bot with no publish record → `_write_for_bot` (today's
  behavior); ARCA-style `.credentials` read-modify-write.
- `.credentials` present with TOKEN/CLIENT_ID → only `ADMINS=` changed.
- `admins=[]` → writes `ADMINS=` (clears); other lines preserved.
- `.credentials` missing → skip (no create), no raise (defensive).
- no device bound (`DeviceNotBoundError`) → skip silently.
- `write_file` raises → `seed_for_publish` propagates (handler Retry);
  `sync_on_change` swallows (warn).
- online binding lookup stale-missing (`ext.binding.online` absent) → falls back
  to `_write_for_bot`.

Handler (`test_teclaw_publish_task_handler.py`, no mocks — real fakes):
- publish SUCCESS + device ready → `seed_for_publish` called once with the online
  `binding.id`; task Completes.
- `seed_for_publish` raises a transient error → Retry; next attempt replays
  token + seed; converges.
- crash-resume (binding already ACTIVE) → replays seed; idempotent (read-modify-
  write, no clobber).
- `updated is None` (no egress rule provider) → still seeds admins; Completes.

Regression:
- existing `collaborator_service` tests green (non-service sync unchanged);
  existing handler tests green (token delivery unchanged); full `tests/community`
  green.