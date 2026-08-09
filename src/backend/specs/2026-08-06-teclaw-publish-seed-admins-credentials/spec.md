# Teclaw Service-Bot Publish: seed collaborator admins into the running container's `.credentials`

## Summary

After a service bot (a **teclaw** container) is published, the running container
has no collaborator-admins information. The teclaw engine reads admins from
`/home/admin/.credentials` (the `ADMINS=` line). The file **already exists** in a
freshly published teclaw container (engine-created at boot, containing
TOKEN/CLIENT_ID/etc.) — only the `ADMINS=` line is missing. Two backend gaps
leave it missing forever:

1. **No publish-time seed.** The teclaw publish path never writes `ADMINS=` after
   the container is up. ARCA seeds via `start_service.sh --admins` at boot;
   teclaw has no such step.
2. **Runtime sync resolves the wrong binding.** `on_collaboration_changed` →
   `_sync_admins_to_credentials` resolves the device via `resolve_for_bot`, which
   looks up `ac_bots.binding_id` (`get_active_by_bot_and_owner`). For a service
   bot that is the **DRAFT** binding, not the online teclaw container (whose
   binding id lives in the publish record's `ext.binding.online`). So even adding
   a collaborator after publish writes to the draft (or no-ops) — it never
   reaches the running teclaw container.

Fix: resolve the **online** binding for both paths and read-modify-write the
`ADMINS=` line into the existing `.credentials` (never create a bare file —
confirmed the engine rejects an ADMINS-only file). At publish SUCCESS, the
`TeclawPublishTaskHandler` already holds the online `binding_id` — use
`resolve_for_binding`. At runtime, resolve the online binding id from
`ext.binding.online` (`get_latest_success_by_source_bot_id`) and use
`resolve_for_binding`; fall back to `resolve_for_bot` for bots with no publish
record (ARCA/personal/desktop, unchanged). The engine hot-reloads `.credentials`
(confirmed), so runtime collaborator changes take effect immediately — no
restart/re-publish needed.

## Motivation

- `bot_service.py:4479` states the intent verbatim: *"服务型 bot 实例化时，复用
  source bot 的 admin 协作者作为沙箱启动 admins."* ARCA honors it
  (`apply_device(admins=...)` → `--admins` at `baas_container_init.py:156`).
  The teclaw publish branch (`bot_build_service.py:517-534` → `create_teclaw_bot`)
  does not — `create_teclaw_bot` / `_build_teclaw_payload`
  (`baas_service.py:893-1038`) have no admins field.
- The teclaw container boots only from `config_artifact` (no NAS, no
  `start_service.sh`, no boot hooks; `baas_service.py:904-910`). Its only
  post-publish reach-in today is the agentpass token egress rule
  (`teclaw_publish_task_handler.py:213-334`). Admins are neither in the artifact
  nor delivered otherwise.
- `CollaboratorService._sync_admins_to_credentials` already does the right
  read-modify-write on `.credentials` (`collaborator_service.py:775-842`) and the
  plumbing reaches teclaw (`DeviceContextResolver` routes teclaw via binding;
  `TeclawDeviceFileSystem.read_file`/`write_file` work). But it resolves the
  device with `resolve_for_bot` → `get_active_by_bot_and_owner`, which JOINs
  `ac_bots.binding_id` (`device_repository.py:220-248`). For a service bot that
  is the draft binding; `resolve_for_binding`'s own docstring
  (`device_context_resolver.py:113-115`) calls out that service bots must use
  `ext.binding.online`, not the by-bot entry. `get_latest_success_by_source_bot_id`
  (`bot_publish_repository.py:181-200`) exists precisely to read that online
  binding id.

## Confirmed engine contract (from the engine owner)

- **`.credentials` already exists** in a freshly published teclaw container, with
  TOKEN/CLIENT_ID/etc.; only `ADMINS=` is missing.
- **The engine rejects an ADMINS-only `.credentials`** → the backend must
  read-modify-write the existing file, never create a bare one.
- **The engine hot-reloads `.credentials`** → runtime writes take effect
  immediately; no restart/re-publish required for collaborator changes.

## User Stories

- As a service-bot owner, after I publish, the running container must know my
  admin collaborators so admin-gated actions work in-session.
- As a collaborator admin added to a service bot *after* it is already published,
  my admin role must reach the running container immediately, without a
  re-publish or restart.
- As an operator, if the publish pod dies between token delivery and the admins
  seed, the durable task must replay both and converge — no container left with a
  token but no admins.

## Acceptance Criteria

- [ ] After a teclaw service-bot first publish reaches SUCCESS and the container
      is ready, `/home/admin/.credentials` in the **online** container contains an
      `ADMINS=` line listing the bot's current admin collaborators (worknos,
      comma-joined), sourced from `ac_bot_collaborator` (`role=admin`) for the
      current env. The seed resolves the online binding (the one the
      `TeclawPublishTaskHandler` is polling), not the draft.
- [ ] The seed is read-modify-write only: it preserves TOKEN/CLIENT_ID/etc. and
      changes only the `ADMINS=` line. It never creates a bare `.credentials`
      (engine rejects ADMINS-only). Empty admins → writes `ADMINS=` (clears),
      never deletes the line.
- [ ] The seed is part of the `teclaw.create.publish_poll` durable task, gated on
      the same device-readiness signal as the agentpass token delivery, retried on
      transient failure up to the task deadline. A crash between token delivery
      and seed replay-converges: re-entering the ACTIVE binding replays both, and
      both are idempotent (token = REPLACE; `.credentials` = read-modify-write).
- [ ] When a collaborator admin is added/removed on an already-published teclaw
      service bot, `on_collaboration_changed` resolves the bot's **online**
      binding (`get_latest_success_by_source_bot_id` → `ext.binding.online` →
      `resolve_for_binding`) and updates the running container's `.credentials`
      `ADMINS=` line. Because the engine hot-reloads `.credentials`, the change
      takes effect immediately — no restart/re-publish.
- [ ] Bots with no publish record (ARCA / personal / desktop) keep today's
      `resolve_for_bot` resolution and are byte-for-byte unchanged.
- [ ] No mocking in the new/extended tests (per commit `da9bf087`); use real
      fakes. Full `tests/community` suite green.

## In Scope

- `TeclawPublishTaskHandler` post-publish step: seed `ADMINS=` via the online
  binding (`resolve_for_binding`).
- `on_collaboration_changed` runtime sync: resolve the online binding for service
  bots (via the publish record's `ext.binding.online`) and write via
  `resolve_for_binding`; fall back to `resolve_for_bot` for bots without a
  publish record.
- Extract the `.credentials` admins write into a reusable
  `DeviceCredentialsAdminsWriter` used by both paths (needs `bot_publish_repo`
  for the online-binding lookup).
- DI wiring for the writer into `TeclawPublishTaskHandler` and
  `CollaboratorService`.

## Out of Scope

- Changing the `BotConfigArtifact` contract (admins do not ride in the artifact).
- Creating `.credentials` from scratch / `create_if_missing` (engine rejects
  ADMINS-only; the file already exists).
- ARCA/baas/personal/desktop paths (unchanged — they have no `ext.binding.online`
  and keep `resolve_for_bot`).
- Version-scoping collaborators. `ac_bot_collaborator` has no `version`/`status`
  field — a source bot has a single collaborator set shared across all its
  publish versions. So editing collaborators (in draft or anytime) takes effect
  on the **currently online** instance immediately (matching ARCA's existing
  `on_collaboration_changed` behavior); there is no "draft-only collaborators
  that apply only when the new version publishes." If that isolation is ever
  required, it is a separate model change (version column + migration + UI) and
  stays out of this fix.
- Re-publish/upgrade re-seed: covered automatically if it routes through the same
  publish-poll task; verify, do not special-case unless needed.
- BaaS-server-side changes.
- Backfilling already-published teclaw containers (the runtime-sync fix
  self-heals them on the next collaborator edit; a manual re-publish also seeds).

## Open Questions

1. ~~`.credentials` minimal-file tolerance.~~ **Resolved (engine owner):** cannot
   tolerate ADMINS-only → read-modify-write the existing file, never create a
   bare one. (Confirmed the file already exists, engine-created.)
2. ~~**`CollaboratorService` dep narrowing.**~~ **Resolved (Task 1):** confirmed
   `_resolver_provider` / `_device_fs_dispatcher_provider` are used only in
   `_sync_admins_to_credentials` (`collaborator_service.py:800,809`). Move both
   onto the writer.
3. ~~**ARCA-unaffected verification.**~~ **Resolved (Task 1):** non-service bots
   (ARCA/personal/desktop) produce no publish record, so
   `get_latest_success_by_source_bot_id` returns `None` → the writer falls back to
   `resolve_for_bot` (unchanged). Desktop additionally sets `ac_bots.binding_id`
   to the active binding itself. The "prefer online binding" rule only activates
   for bots with a success publish record (service bots), so no teclaw-only
   scoping is needed.
4. ~~Engine hot-reload of `.credentials`.~~ **Resolved (engine owner):** yes,
   runtime writes take effect immediately.
5. ~~**`ac_bots.binding_id` stays draft for service bots.**~~ **Resolved (Task 1):**
   the service/teclaw publish flow never writes the online binding into
   `ac_bots.binding_id` (only desktop / bot creation writes that column;
   `caller_identity`'s `binding_id` is a caller-credential runtime update, not an
   `ac_bots` write; publish-flow `update_by_owner` touches `bot_type` etc., not
   `binding_id`). So `ac_bots.binding_id` stays the draft for service bots and
   `resolve_for_bot` mis-resolves — the online-binding fix is necessary.