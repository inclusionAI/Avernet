# Tasks: Lifecycle Apply Points (W8)

Spec: `spec.md` · Plan: `plan.md` · Issue #1476.

> **Revision 3.** Seven groups, twenty-two tasks. A is the seam and the
> vocabulary; B the artifact contract; C the teclaw store path; D creation;
> E `PUT`; F the alias view (withdrawn in review, rev 5); G docs and the sweep.
> A→C→D is the critical chain; B is needed by C's composer task; E and F are
> independent of everything but A.

Conventions every task assumes:

- Every apply goes through `start_apply`; nothing here runs the orchestrator,
  restarts a bot, republishes, or rebuilds a payload.
- A lifecycle apply that cannot start, or that ends `PARTIAL` / `FAILED`, never
  fails the lifecycle operation it rode on (§2.7).
- The switch is read only by the strategy factory.
- No test asserting today's behaviour on ARCA, or on teclaw with the switch
  off, is edited.

---

## Group A — The seam

## [x] Task 1: Trigger constants and the §2.12 pin
- **Files:** `apply/triggers.py` (new), `apply/outcomes.py`,
  `tests/community/core/bot_config_manifest/test_iteration1_ordering.py` (new)
- **Done when:**
  - [x] `EXPLICIT`, `PUT` constants; creation triggers referenced from
        `creation.py`; `ApplyReport.trigger` docstring lists the vocabulary.
  - [x] The ordering test pins ARCA's `PRE_CONTAINER == (script,)` and names
        #1508; a second case pins that `TeclawDelivery(switch on)` puts every
        non-script construct in `PRE_CONTAINER`.
- **Depends on:** Task 2 (for the teclaw case)

## [x] Task 2: `DeliveryStrategy`, both strategies, the factory, the switch
- **Files:** `apply/delivery.py` (new), `apply/order.py`,
  `core/bot_config_manifest/config.py` or wherever `BotConfigManifestConfig`
  lives, `di/modules/manifest_fetch_module.py`,
  `tests/community/core/bot_config_manifest/apply/test_delivery_strategy.py` (new)
- **Done when:**
  - [x] Protocol per plan K-1; `ArcaDelivery` reproduces `APPLY_ORDER`'s phases
        and `steps_for`; `TeclawDelivery(platform_managed=True)` maps every
        non-script step to `PRE_CONTAINER`, `(False)` to `ON_CONTAINER`.
  - [x] `CreationSequence.CREATE_BETWEEN_PHASES` for ARCA and teclaw-off,
        `RECORD_APPLY_PROVISION` for teclaw-on.
  - [x] `BotConfigManifestConfig.teclaw_platform_managed: bool = False` read
        from `user_config.bot_config_manifest`; the factory takes it and
        `is_teclaw`.
  - [x] `order.steps_for` delegates to `ArcaDelivery` so existing callers are
        unchanged.
  - [x] Tests cover the three phase tables, the sequence, and the factory's
        selection.
- **Depends on:** —

## [x] Task 3: The apply service runs through the strategy
- **Files:** `services/config_manifest_apply_service.py`,
  `apply/orchestrator.py` (only if it calls `steps_for` directly),
  `di/modules/bot_management_module.py`,
  `tests/community/core/bot_config_manifest/apply/test_apply_service_lifecycle.py`
- **Done when:**
  - [x] `_rebuild` / `run_apply_task` select the strategy for the bot, build
        materialisers from `strategy.ports()`, walk `strategy.steps_for(phases)`,
        and call `strategy.finish(ctx, report)` after the terminal record is
        written (a `finish` failure is a report note, never a raise).
  - [x] ARCA's ports are the providers the service takes today, so its
        behaviour is unchanged and every existing apply test passes unedited.
  - [x] A new test pins that no module under `apply/materialisers/` names an
        engine string.
- **Depends on:** Task 2

---

## Group B — The artifact contract

## [x] Task 4: `ownership` on the artifact
- **Files:** `kernel/bot_config/artifact.py`, `kernel/bot_config/artifact.schema.json`,
  `tests/community/kernel/test_bot_config_artifact.py`
- **Done when:**
  - [x] `ownership: dict[str, str] | None = None`; omitted by `to_dict` when
        `None`; read by `from_dict`; values restricted to `platform` / `engine`
        in the schema with the semantics in the description.
  - [x] Tests: unset leaves the key off the wire (byte-identical to before);
        set round-trips; schema accepts both; `SCHEMA_VERSION` stays 4 and the
        existing guard test is unedited.
- **Depends on:** —

## [x] Task 5: The contract addendum
- **Files:** `docs/bot-config-manifest/engine-convergence-contract.zh-CN.md`
- **Done when:**
  - [x] A new §9 "平台管理的类目：`ownership`" with: the map and its three
        states; the per-category area it applies to (pointing at §5); file
        refs in a redeliver to a running container; the store-backed local
        `SkillRef` (files under the local-skills layout plus a `SkillRef`
        naming the package prefix); one example artifact; an acceptance
        checklist; and the statement that `schema_version` stays 4 under A5.
  - [x] §7's status table gains a row for the map, marked pending the teclaw
        owner.
- **Depends on:** Task 4

## [x] Task 6: The composer emits the map and reads the index for teclaw
- **Files:** `core/config_compose/protocols.py` (`skill_files` on
  `ManagedFilesReader`), `core/config_compose/services/config_composer.py`,
  `core/config_compose/services/collector.py`,
  `core/bot_config_manifest/managed_files/reader.py`,
  `di/modules/service_bot_module.py`,
  `tests/community/core/config_compose/test_ownership.py` (new)
- **Done when:**
  - [x] The collector holds the managed-files reader (optional) and answers
        `platform_managed(req)` once per compose (memoized on the request);
        the producer and the device-sync service are untouched — no
        `ComposeRequest` field was needed, since the collector already sees
        the request. The teclaw branches for identity, resources and skills
        read the index when the category is `platform`; a platform skill is
        emitted only while the bot has it active, and its files ride as
        resources refs beside the `SkillRef`.
  - [x] The composer sets `ownership` on teclaw requests: `mcp: platform`;
        `identity_files` / `resources` / `skills`: `platform` when asserted,
        else `engine` (`cli_tools` is not written — absent keeps its own
        contract's rule). ARCA requests carry no map.
  - [x] Tests: ARCA compose unchanged; teclaw compose without a manifest emits
        `engine` for the file categories and today's empty lists; teclaw
        compose with declared identity emits `platform` and the index refs;
        each ref resolves against the configured `bot-data` store.
- **Depends on:** Task 4, Task 8 (reader implementation)

## [x] Task 7: The managed-files index
- **Superseded in review (plan rev 4):** the table, its repository, protocol,
  DDL and tests were removed; the store lists the object prefix instead.
- **Files:** `core/bot_config_manifest/repository/managed_files_models.py` (new),
  `core/repository/protocols/bot/managed_files.py` (new),
  `core/repository/implementations/bot/managed_files.py` (new),
  `core/bot_config_manifest/sql/2026_09_02_bot_config_managed_files.sql` (new),
  tenant-guard registration, `tests/community/repository/bot/test_managed_files_repository.py` (new)
- **Done when:**
  - [x] Table per plan K-2; protocol as an abstract base; implementation with
        `upsert`, `delete`, `list_by_category`, `purge_bot`.
  - [x] Repository tests on SQLite; the tenant column is in the unique key.
- **Depends on:** —

## [x] Task 8: `ManagedFilesStore` and the reader
- **Files:** `core/bot_config_manifest/managed_files/{__init__,store,reader}.py` (new),
  `core/bot_config_manifest/managed_files/README.md` (new),
  `di/modules/manifest_fetch_module.py`,
  `tests/community/core/bot_config_manifest/managed_files/test_store.py` (new)
- **Done when:**
  - [x] `put` writes the object then the row and returns the ref; `delete`
        removes both; `list`; `purge` removes every row and object for a bot.
  - [x] Keys follow the promotion layout with a `_manifest` segment.
  - [x] The reader implements `ManagedFilesReader` and
        `PlatformOwnershipReader` (rev 5: the platform owns a manifest apply's
        redeliver and a manifest bot's first artifact when the switch is on;
        nothing else).
  - [x] Tests against a fake `ObjectStoragePlugin` and SQLite.
- **Depends on:** Task 7

## [x] Task 9: Store-backed identity and resource ports
- **Files:** `core/bot_config_manifest/managed_files/ports.py` (new),
  `tests/community/core/bot_config_manifest/managed_files/test_store_ports.py` (new)
- **Done when:**
  - [x] `StoreIdentityPort` and `StoreResourcePort` implement the existing port
        protocols over the store.
  - [x] Driving the real `IdentityMaterialiser` and `ResourcesMaterialiser`
        with these ports yields `created` / `updated` / `unchanged` and
        removals purely from the index, with no device involved; a directory
        entry replaces its tree in the index.
- **Depends on:** Task 8

## [x] Task 10: Record-only activation
- **Files:** `core/skill_center/services/direct_activation_service.py`,
  the activation protocol under `api/`,
  `tests/community/core/skill_center/test_direct_activation_service.py`
- **Done when:**
  - [x] `project: bool = True` on the four activate/deactivate methods; `False`
        passes `runtime_required=False`; audit still written.
  - [x] Tests: `project=False` never touches the projector and records the
        mutation on a non-ACTIVE bot; `project=True` unchanged.
- **Depends on:** —

## [x] Task 11: Store-backed skill package port
- **Files:** `core/bot_config_manifest/managed_files/ports.py`,
  `tests/community/core/bot_config_manifest/managed_files/test_skill_port.py` (new)
- **Done when:**
  - [x] `upload_local_skill` validates through the same package validator,
        unpacks into the store under `workspace/skills-local/<name>/…`, indexes
        every file under category `skills` with the skill name, creates the
        skill row with a `local://` locator, and returns the shape the
        materialiser expects; `installed_package_digest` answers from the index.
  - [x] The collector's teclaw `skills` branch emits a `SkillRef` per indexed
        skill (`scope="user"` — the schema's word for a per-bot skill —,
        `store="bot-data"`, `path=<package prefix>`) in addition to the files
        riding as resources refs (`ManagedFilesComposeReader.skills`, Task 10;
        the collector reads it in Task 6).
  - [x] Driving the real `SkillsMaterialiser` with this port and the
        record-only activation converges a manifest skill with no device.
- **Depends on:** Task 8, Task 10, Task 6

## [x] Task 12: `TeclawDelivery` ports and the closing redeliver
- **Files:** `apply/delivery.py`, `apply/record_only_activation.py` (new),
  `apply/redeliver.py` (new), `apply/context.py` (`current_apply_id`),
  `di/modules/manifest_fetch_module.py` (`TeclawPlatformBindings` provider; the
  creation-config provider moved here from `bot_management_module`, which is at
  its size cap), `di/modules/bot_management_module.py`,
  `tests/community/core/bot_config_manifest/apply/test_teclaw_delivery.py` (new)
- **Done when:**
  - [x] `TeclawDelivery(platform_managed=True).ports()` returns the store
        ports and the record-only activation wrapper; `(False)` returns
        ARCA's ports.
  - [x] `finish` with the switch on: one `dispatch(ctx).sync_symlinks([])`
        when the bot has a live binding, nothing otherwise; a failure becomes a
        report note. With the switch off: no-op.
  - [x] Tests for all four combinations.
- **Depends on:** Task 9, Task 11

---

## Group D — Creation

## [x] Task 13: `create_bot(provision=False)` and `provision_bot`
- **Files:** `core/bot_management/services/bot_service.py`,
  `core/bot_management/create_flow.py`,
  `core/bot_management/bot_service_protocol.py` (`provision_bot`; `api/bot_service.py`
  re-exports it),
  `tests/community/core/bot_management/services/test_create_bot_deferred_provision.py` (new)
- **Done when:**
  - [x] `create_bot(..., provision: bool = True)`; `False` returns after
        step 1 with status `PENDING`, no binding; `provision_bot(bot_id, user_id, nick_name, *, template_type=None,
        template_config=None, cookie=None)` runs step 2 and the binding-dependent tail for such a record and is
        idempotent on a record that already has a binding.
  - [x] `complete_bot_authorization` / `complete_manifest_creation` take
        `provision` and pass it through.
  - [x] A golden test: `create_bot()` equals `create_bot(provision=False)` +
        `provision_bot()` in the record written and the collaborator calls
        made, in order; the whole existing creation suite passes unedited.
- **Depends on:** —

## [x] Task 14: The job runs the strategy's sequence
- **Files:** `core/bot_config_manifest/create_job.py`, `creation.py`
  (`apply_pre_container(bot=…)`, `discard(owner_id=…)` purging the store),
  `managed_files/store.py` (`purge_owner_bot`), `di/modules/bot_management_module.py`,
  `tests/community/core/bot_config_manifest/creation/test_create_job.py`,
  `…/test_creation_ordering_teclaw.py` (new; the ARCA ordering suite is unedited)
- **Done when:**
  - [x] The handler asks the strategy for the sequence. Under
        `RECORD_APPLY_PROVISION`: authorized → `complete(provision=False)`;
        record without binding and no terminal pre-container record → start
        the phase; record terminal and no binding → `provision_bot`; binding
        present → wait `ACTIVE` → `Complete`. Never starts phase B.
  - [x] Under `CREATE_BETWEEN_PHASES` the handler is today's, and its tests are
        unedited.
  - [x] Ordering tests: provision only after the phase is terminal; a second
        invocation at every step is a no-op; a failed phase still provisions
        (§2.7); a creation that ends without a bot purges the store as well as
        the manifest and script row.
- **Depends on:** Task 2, Task 12, Task 13

## [x] Task 15: The poll is sequence-aware, the refusal is lifted
- **Files:** `adapters/http/openapi_v1/bots/create_with_manifest.py`,
  `core/bot_config_manifest/creation.py`, `di/modules/bot_management_module.py`,
  `tests/community/core/bot_config_manifest/creation/test_creation_preflight.py`,
  `tests/community/endpoints/test_openapi_create_with_manifest.py`,
  `tests/community/adapters/http/openapi_v1/test_create_with_manifest_routes.py`
- **Done when:**
  - [x] `_TECLAW_REFUSAL`, the engine violation and `is_teclaw` are gone from
        the preflight and the seam; `script` on teclaw is still
        `unsupported_script`.
  - [x] `_creation_state` takes the sequence; under `RECORD_APPLY_PROVISION` the
        pre-container record is the terminal one (`READY` / `APPLY_FAILED` /
        `APPLYING`), with `CREATING` between the phase and `ACTIVE`.
  - [x] Endpoint: `202` on teclaw; the poll walks
        `AWAITING_AUTHORIZATION → CREATING → APPLYING → CREATING → READY` with
        the report from the single phase (the assembled app, the real apply
        service over the store-backed ports, creation and provisioning stood
        in); ARCA scenarios unedited; `script` on teclaw is still a `422`.
- **Depends on:** Task 14

## [x] Task 16: The provisioner's first artifact carries the manifest
- **Files:** `tests/community/core/bot_config_manifest/creation/test_first_artifact.py` (new;
  the provisioner's own unit suite needed no change — it already asserts the
  produced artifact reaches `create_teclaw_bot`)
- **Done when:**
  - [x] An end-to-end test: stored manifest declaring identity, a resource
        and a skill; deferred create; the phase writes to the store; the
        provisioner's composed artifact (real composer, fake collectors for
        the DB categories, real reader over the index) carries the refs, the
        `SkillRef`, and `ownership` with `platform` for the three; the
        `create_teclaw_bot` call receives it.
- **Depends on:** Task 15

---

## Group E — `PUT` takes effect

## [x] Task 17: `declares_script`, the `apply` field, the warnings
- **Files:** `bot_config_manifest_service_protocol.py`, `services/config_manifest_service.py`,
  `adapters/http/openapi_v1/bots/{config_manifest.py,config_manifest_support.py,schemas.py}`,
  `tests/community/endpoints/test_openapi_config_manifest.py`,
  `tests/community/core/bot_config_manifest/test_iteration1_ordering.py` (the
  no-restart pin), `tests/community/endpoints/test_openapi_create_with_manifest.py`
  (the old two-call path now sees the PUT's own apply first)
- **Done when:**
  - [x] As revision 2's Tasks 3 and 4, with the not-ACTIVE warning emitted
        only when the bot's strategy has `ON_CONTAINER` constructs.
  - [x] Endpoint tests: `RUNNING` with a readable id; lock held →
        `NOT_STARTED` and stored; `script` → delivery note; `PENDING` ARCA →
        not-ACTIVE note; `PENDING` teclaw with the switch on → no such note;
        `DELETE` unchanged.
- **Depends on:** Task 1, Task 2

---

## Group F — The alias view (withdrawn, rev 5)

> Landed as below, then withdrawn in review of inclusionAI/Avernet#1836: the
> manifest is a layer the startup script does not know about. The three tasks
> are kept for the record; their code and tests are removed, and the legacy
> routes are byte-for-byte what they were before W8.

## [x] Task 18: The splice helper (withdrawn)
- **Files:** `schema/splice.py` (new), `tests/community/core/bot_config_manifest/test_script_splice.py` (new)
- **Done when:** as revision 2's Task 6 — with one deviation: `|+` is never
  rendered (its trailing blank lines are indistinguishable from the document's
  own spacing); a body with more than one trailing newline, an empty body and
  a CRLF body take the JSON-quoted form, which reads back identically.
- **Depends on:** —

## [x] Task 19: `write_through_script` and `script_body` (withdrawn)
- **Files:** `services/config_manifest_service.py`, its protocol (`api/` re-exports
  the protocol unchanged; DI resolves the lazy script-service provider the
  bot-management module already binds),
  `tests/community/core/bot_config_manifest/test_write_through_script.py` (new)
- **Done when:** as revision 2's Task 7.
- **Depends on:** Task 18

## [x] Task 20: The three startup-script routes (withdrawn)
- **Files:** `adapters/http/openapi_v1/bots/{router.py,startup_script_support.py}`,
  `tests/community/endpoints/test_openapi_startup_script.py`,
  `tests/community/adapters/http/openapi_v1/test_bots_endpoints.py`
- **Done when:** as revision 2's Task 8 — done: `PUT` / `DELETE` go through
  `write_through_script` and fall back to the legacy path on `None`; `GET`
  answers the declared body; the withdraw guard runs on both `PUT` arms; the
  legacy cases pass unedited (the unit suite only gained a manifest-service
  stand-in in its injector).
- **Depends on:** Task 19

---

## Group G — Docs and the sweep

## [x] Task 21: Docs
- **Files:** `core/bot_config_manifest/README.md` (its boundary rows landed with
  Tasks 8–12; `config_compose` and `skill_center` needed none — the compose side
  reaches the reader through a Protocol), `managed_files/README.md` (Task 8),
  `docs/bot-config-manifest/user-manual.zh-CN.md`,
  `docs/bot-config-manifest/{work-items,work-items.zh-CN}.md`,
  the config reference for the switch (`configs/application.yaml`)
- **Done when:**
  - [x] README: "Lifecycle apply points and the delivery seam (W8)" — the
        strategy, the two families side by side, the switch and when to flip
        it, the store, the closing redeliver, ownership by operation, the
        trigger vocabulary, the deferrals.
  - [x] User manual §4.6, §5.5, §7, and a new teclaw subsection (first
        artifact, whole-artifact convergence, the switch).
  - [x] Work-items W8 (both languages): a progress block — what landed per
        criterion, the seam, the switch's default and its condition, the
        deferrals (restart/republish, publish gather, health surface, ARCA
        pre-binding port).
- **Depends on:** Tasks 3, 6, 12, 16, 17, 20

## [x] Task 22: Regression sweep
- **Files:** —
- **Done when:**
  - [x] `uv run pytest tests/community/core/bot_config_manifest tests/community/core/config_compose tests/community/core/skill_center tests/community/core/bot_management tests/community/core/service_bot tests/community/kernel tests/community/repository tests/community/endpoints tests/community/adapters/http/openapi_v1 tests/community/architecture` passes — 4883 + 3254 passed locally (the two
        `rsync`-dependent build tests are deselected: the tool is absent in this
        container); `tests/community/config` goldens regenerated for the new key.
  - [x] `test_no_script_is_byte_identical_to_the_bare_chain` unmodified.
  - [x] Lint (`ruff`) and the oversized-module gate pass.
- **Depends on:** Task 21
