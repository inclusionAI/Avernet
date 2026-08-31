# Tasks: the Manifest Apply Engine

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

Groups run in order. Within a group, tasks are independent.

Two invariants hold in every task, and a violation of either is a finding rather
than something to work around:

- **No existing test is edited.** This change adds operations; it does not alter
  one. The single planned exception is the `mcp`-entry key-set test in Task 3,
  which the spec records as a deliberate schema narrowing.
- **Nothing existing is destroyed by a failure.** Every path that cannot
  complete leaves the bot exactly as it was. If a task's implementation makes
  that untrue, the task is wrong, not the criterion.

---

## Group A — Foundations: the vocabulary, the tables, the schema fix

## [x] Task 1: Outcome and report vocabulary
- **Goal:** The types every later task speaks, in a leaf module that imports
  nothing else from the feature.
- **Files:** `src/backend/.../core/bot_config_manifest/apply/outcomes.py`,
  `apply/__init__.py`
- **Done when:**
  - [x] `EntryOutcome` is exactly `created` / `updated` / `unchanged` /
        `skipped` / `failed`. Its docstring records that `skipped` means "not
        written because its category was aborted" and that the author-facing
        `on_fetch_failure: skip` it used to mean no longer exists.
  - [x] `ApplyStatus` is `RUNNING` plus the three terminal values
        `SUCCEEDED` / `PARTIAL` / `FAILED`, with a docstring stating the
        terminal three are derived and that nothing in the engine branches on
        one. `RUNNING` exists because apply is started, not awaited.
  - [x] `EntryResult` carries the construct, the entry's identity, its outcome,
        and an optional reason. `CategoryResult` carries its entry results **and
        a separate `removals` field** — removals are not `EntryOutcome` values,
        because the five classify declared entries and a removal has none.
  - [x] `ApplyReport` matches design §7's shape: `apply_id`, `bot_id`,
        `trigger`, `started_at`, `finished_at`, `result`, `sources`, `entries`.
        `sources` is empty in this wave and its docstring says W5 fills it.
  - [x] `ApplyReport.as_payload()` is the one place the wire shape is defined,
        and it is structurally incapable of emitting a credential value: it
        serialises named fields, never a passthrough of a declared entry.
  - [x] `apply/__init__.py` holds a docstring only, for the cycle reason
        `bot_config_surface/__init__.py` records.
- **Depends on:** —

## [x] Task 2: The two tables
- **Goal:** Persistence for the apply record and for the serialization lock.
- **Files:** `.../core/bot_config_manifest/repository/apply_models.py`,
  `.../core/bot_config_manifest/sql/2026_08_31_bot_config_manifest_apply.sql`,
  `.../core/repository/protocols/bot/config_manifest_apply.py`,
  `.../core/repository/implementations/bot/config_manifest_apply.py`,
  `.../core/repository/protocols/bot/__init__.py`, `.../core/schema.py`
- **Done when:**
  - [x] `ac_bot_config_manifest_apply` carries the columns the plan lists, keyed
        `(avernet_tenant, env, entity_id, bot_id)` with the same 256-char widths
        and the same index-budget reasoning `ac_bot_config_manifest` records.
  - [x] Two indexes, one per read, each with a comment naming its read:
        `(…, id DESC)` for `last-apply`, and `(…, apply_id)` for the poll by id.
        The second carries the bot key rather than being a bare `apply_id`
        lookup — the id is not the authorization.
  - [x] `status` and `finished_at` support the two-write lifecycle: `RUNNING`
        with a null `finished_at` on insert, terminal values on completion.
  - [x] **No `dry_run` column.** A dry run mints no id and writes no row, so
        there is nothing to mark.
  - [x] `report` is `Text().with_variant(mysql.MEDIUMTEXT(), "mysql")`, for the
        reason the manifest's `document` column records.
  - [x] `ac_bot_config_manifest_apply_lock` mirrors `ac_bot_restart_lock`:
        `UNIQUE(avernet_tenant, env, entity_id, bot_id)` **is** the lock,
        `acquire` inserts and treats `IntegrityError` as held, `release`
        compares `lock_token` before deleting, `get_if_stale` reads both
        timestamps from the **database clock**.
  - [x] Both models call `register_avernet_tenant_guard`.
  - [x] Both protocols are `@abstractmethod` throughout and the implementations
        inherit them, matching `BotConfigManifestRepositoryProtocol`.
  - [x] `core/schema.py` imports the models so `create_all` emits both tables.
  - [x] The DDL file carries the tenancy and index-budget reasoning in comments,
        as its sibling does — a reader must not have to rediscover why
        `entity_id` is 256.
- **Depends on:** —

## [x] Task 3: Narrow the `mcp` entry, and fix the schema document
- **Goal:** Close the account-scoped-config hazard at the vocabulary, so no
  materialiser has to defend against it (spec *Decisions* 11).
- **Files:** `.../core/bot_config_manifest/schema/entries.py`,
  `docs/bot-config-manifest/manifest-schema.zh-CN.md`,
  `docs/bot-config-manifest/work-items.md` + `work-items.zh-CN.md`,
  `.../core/bot_config_manifest/README.md`,
  `src/backend/tests/community/core/bot_config_manifest/test_manifest_schema.py`
- **Done when:**
  - [x] `CATEGORY_ENTRY_KEYS[ManifestCategory.MCP]` is `{"server_code"}`.
  - [x] `validate_mcp_entry` drops its `config` branch; `config` is refused by
        the existing `unknown_field` path, exactly as retired `entrypoints` is.
  - [x] A named test pins the refusal, mirroring
        `test_the_retired_entrypoints_field_is_refused_rather_than_ignored`.
        **This is the one new test in an existing file**, and it is an addition,
        not an edit of an existing case.
  - [x] `manifest-schema.zh-CN.md` §3.1 is rewritten: an `mcp` entry is a bare
        `server_code`; credentials, headers, endpoint env and transport are
        configured through `GET`/`PUT
        /openapi/v1/bots/mcp/servers/{server_code}/config`, which is
        account-scoped and always was.
  - [x] The rewrite states **why**, not just what: `ac_user_mcp_config` is keyed
        `(user_id, server_code)` and its write fans out via
        `sync_mcp_detail_to_all_bots`, so a per-bot manifest could not own it;
        and design §4.5 forbids a credential in a manifest regardless.
  - [x] Both work-items files' `mcp` descriptions agree with the schema
        document. A divergence between them is what Rule 16 exists to prevent.
  - [x] The module README's "known gaps" records the finding and that
        `ac_bot_mcp_call_config` (`call_type`) is the additive follow-up.
- **Depends on:** —

---

## Group B — The engine

## [x] Task 4: The ordering table and the registry
- **Goal:** Ordering is a complete, inspectable contract; materialiser presence
  is a separate, sparse fact.
- **Files:** `apply/order.py`, `apply/registry.py`, `apply/context.py`
- **Done when:**
  - [x] `APPLY_ORDER` names **all six** constructs plus `script`, with
        `script` alone in `PRE_CONTAINER` at position 0 and
        `identity → resources → skills → mcp` in `ON_CONTAINER` in that order.
  - [x] Its docstring states that this **reverses design §3.4** and why
        (work-items §2.12): `script` needs no container and must precede start-
        command composition; phase B resolves a device and raises if unbound.
  - [x] `MATERIALISERS` maps `script` and `mcp` only. Its docstring says a
        missing key is an expected state that W5/W6 close, not a gap.
  - [x] `ApplyContext` carries the identity and coordinates one apply runs
        under, built from a bot record via **W10's seam** — never re-derived.
  - [x] A test asserts every `MATERIALISERS` key has an `APPLY_ORDER` row, and
        that `APPLY_ORDER` covers every construct the vocabulary defines. The
        reverse containment is deliberately **not** asserted.
- **Depends on:** Task 1

## [x] Task 5: The materialiser contract
- **Goal:** `resolve` → `plan` → `write`, with the boundaries the criteria need.
- **Files:** `apply/registry.py`
- **Done when:**
  - [x] The `Materialiser` protocol declares the three stages with the plan's
        signatures, and each docstring names the criterion its boundary serves.
  - [x] **Every stage is `@abstractmethod` and each materialiser inherits the
        Protocol**, the shape the repository and service contracts already use
        here. A missing stage then fails at construction naming it, rather than
        as an `AttributeError` the first time a category reaches that stage —
        which for `write` would be mid-apply on a real bot.
  - [x] `ResolveResult` carries `intents` and `failures` keyed by entry
        identity, so the orchestrator emits one `EntryResult` per declared entry
        without a materialiser knowing what a report is.
  - [x] `CategoryPlan` carries classified intents **and** `removals`.
  - [x] `plan` is documented as read-only, and that `dry_run` is "stop after
        this" — a missing call, not a discipline.
- **Depends on:** Task 4

## [x] Task 6: The orchestrator
- **Goal:** Every category-level rule, implemented once, for every category.
- **Files:** `apply/orchestrator.py`
- **Done when:**
  - [x] It walks `APPLY_ORDER` in position order, filtered to the requested
        phases, and handles the five cases the plan lists in that order.
  - [x] **Undeclared ⇒ untouched and unreported.** Absence is not a
        declaration.
  - [x] **Declared with no materialiser ⇒ every entry `failed`** with a reason
        naming the construct, and the category aborted.
  - [x] **Any `resolve` failure ⇒ those entries `failed`, the rest `skipped`,
        no `plan` and no `write` call.** The absence of the calls is what the
        test asserts.
  - [x] One category's abort never affects another's.
  - [x] `dry_run` returns after `plan` for every category, and writes no report
        row.
  - [x] `ApplyStatus` is tallied **after** every decision is made.
  - [x] The lock is taken once around the whole call and released in a
        `finally`, `dry_run` included; a held lock raises
        `ManifestApplyInProgressError`.
  - [x] **Nothing is written to the bot record on any path** — no status, no
        activation, no readiness gate — and no code branches on whether this is
        a first boot.
  - [x] A structural test asserts the orchestrator module names no category
        (`skills`, `identity`, `resources`) anywhere. If it does, the registry
        has stopped meaning anything.
- **Depends on:** Tasks 1, 4, 5

## [x] Task 7: The `script` materialiser
- **Files:** `apply/materialisers/script.py`
- **Done when:**
  - [x] `resolve` substitutes `${BOT_*}` via the existing
        `schema/placeholders.py::resolve` — one whitelist, one resolver, no
        second copy.
  - [x] `resolve` re-asks the capability resolver whether `script` is supported
        for this bot, because a bot's engine can change between `PUT` and apply.
  - [x] `plan` compares against the **substituted** body, never the raw document
        text — the convergence criterion depends on this and nothing else.
  - [x] `write` calls `BotStartupScriptService.put` / `.delete` and does nothing
        else. No payload composition, no restart, no republish, no
        start-command touching — **apply never triggers the script's
        execution**, it only delivers the row, and the existing #926 machinery
        picks it up. A structural test asserts this module reaches no
        provisioning or restart path.
  - [x] The result records that the script is **delivered now, executed at the
        bot's next device provisioning** — a field, not something a caller
        infers, and phrased in the terms the mechanism has. It is re-read from
        `ac_bot_startup_script` by `_build_create_bot_payload` on every payload
        (create, restart, republish), and never re-executed inside a container
        already running. A test pins the wording against that behaviour so the
        API cannot promise a timing the platform does not have.
- **Depends on:** Tasks 5, 6

## [x] Task 8: The `mcp` materialiser
- **Files:** `apply/materialisers/mcp.py`
- **Done when:**
  - [x] `resolve` runs the **existing** tenant permission check per
        `server_code`; a server the tenant may not enable is a `failed` entry.
  - [x] A `server_code` governed by a Set or platform-Default policy — where
        `DirectActivationService` is not legal — resolves to a `failed` entry
        with a readable reason, never an exception escaping the orchestrator.
  - [x] `plan` reads `list_installed_mcps` and classifies: declared − current ⇒
        `created`, intersection ⇒ `unchanged`, current − declared ⇒ removals.
  - [x] `write` calls `activate_mcp` / `deactivate_mcp` only.
  - [x] A structural test asserts this module cannot reach
        `update_user_unified_config`, `write_unified_config` or
        `sync_mcp_detail_to_all_bots` — the account-scoped write whose fan-out
        Task 3 removed from the vocabulary.
  - [x] The route docstring says plainly that a declared `mcp` category
        deactivates servers activated through the UI. The first person surprised
        by that will be a real user.
- **Depends on:** Tasks 3, 5, 6

---

## Group C — The service and the public surface

## [x] Task 9: The Service API contract and its implementation
- **Files:** `.../core/bot_config_manifest/bot_config_manifest_apply_service_protocol.py`,
  `.../core/bot_config_manifest/services/config_manifest_apply_service.py`,
  `.../api/bot_config_manifest_apply_service.py`,
  `.../di/modules/bot_management_module.py`,
  `src/backend/tests/community/architecture/test_service_api_conformance.py`
- **Done when:**
  - [x] A **second** contract, not more methods on the document service — the
        docstring gives Rule 9's reason and the different-bars reason.
  - [x] It exposes `start_apply(...)` (with `phases`), `dry_run(...)`,
        `get_apply(...)` and `last_apply(...)`. `phases` is what lets W13 call
        the halves separately.
  - [x] **`start_apply` does not wait for the apply.** It takes the lock,
        re-validates the stored document, records `RUNNING` with a fresh
        `apply_id`, starts the work and returns. A held lock or an invalid
        document raises **before** an id is minted — a caller never gets an id
        for an apply that did not start.
  - [x] The background thread is wrapped
        `threading.Thread(target=bind_current_avernet_tenant(fn), daemon=True)`,
        **inline at the construction site**, matching
        `bot_publish_service.py:1292`. Never as an `@decorator` on a
        module-level function: it captures at wrap-time, so a decorator captures
        at *import*, when there is no request, and binds the default tenant
        forever.
  - [x] A test proves the tenant survives into the thread — not by memory. A
        wrong tenant here substitutes the wrong `${BOT_TENANT}` **and** reads
        and writes the manifest tables under the wrong tenant: an isolation
        failure, not just a correctness one.
  - [x] The report reaches a terminal status in a `finally`, so a raising
        orchestrator still terminates it; a report left `RUNNING` by a killed
        process reads as `FAILED` once its lock is stale, derived at read time
        rather than by a second sweeper mechanism.
  - [x] Every member is `@abstractmethod` and the service inherits the Protocol.
  - [x] The `(Protocol, ConcreteService)` pair is registered in `_PAIRS`.
  - [x] Bound in `bot_management_module.py` beside the document service, with a
        comment saying why it lives there.
  - [x] It re-validates the stored document before applying, and the docstring
        says why that is not paranoia: capabilities resolve from the bot's
        engine, which can change after a document is accepted.
- **Depends on:** Tasks 6, 7, 8

## [x] Task 10: The three routes
- **Files:** `.../adapters/http/openapi_v1/bots/config_manifest_apply.py`,
  `.../bots/schemas.py`, `.../openapi_v1/__init__.py`, `.../responses.py`
- **Done when:**
  - [x] `POST …/config-manifest/apply` answers **202 with the `apply_id`**,
        having started the work rather than done it. It never blocks on device
        I/O.
  - [x] `dry_run=true` is a query parameter that **stays synchronous** and
        returns the plan in the body, minting no id and writing no row.
  - [x] `GET …/config-manifest/applies/{apply_id}` returns that apply's report,
        `RUNNING` or terminal. Its query carries the bot key alongside the id,
        so an id from another bot resolves to nothing — the id is not the
        authorization.
  - [x] `GET …/config-manifest/last-apply` returns the newest report, and a bot
        never applied answers with an **empty report, not a 404** — the rule the
        manifest's own `GET` already sets.
  - [x] A bot with **no stored manifest** applies nothing and reports nothing
        applied, without erroring.
  - [x] `ManifestApplyInProgressError` maps to 409 in both the status map and
        the biz-code map; a failed entry or an aborted category is a **200 with
        a report**, never an error status.
  - [x] Routes carry no domain policy: they resolve the bot, call the service,
        shape the result. Rule 7.
  - [x] Mounted in `openapi_v1/__init__.py` beside `config_manifest_router`.
- **Depends on:** Task 9

## [x] Task 11: The bars and the dominance test
- **Files:** `.../openapi_v1/authorization.py`, `.../openapi_v1/admission.py`,
  `src/backend/tests/community/adapters/http/openapi_v1/test_config_manifest_apply_bars.py`
- **Done when:**
  - [x] `POST …/apply` → `GRANT_CHECKED_ADDRESSED_BOT` +
        `Check(PermissionLevel.OWNER, EDIT_LOCK)`; `GET …/last-apply` and
        `GET …/applies/{apply_id}` → `GRANT_CHECKED_ADDRESSED_BOT` +
        `Check(PermissionLevel.MEMBER)`.
  - [x] The rows carry comments giving W10's reasoning: the bar follows apply's
        own shape, not a maximum over categories; the admission mode follows
        from `Check`, not from taste.
  - [x] The **dominance test** iterates `MATERIALISERS`, so a category W5
        registers enters it automatically.
  - [x] It compares the **admitted set**, not raw enum ordering, and carries the
        `Check(OWNER)` ≡ `OWNER_SCOPED` equivalence as a comment so the next
        reader does not re-derive it.
  - [x] `test_admission_inventory.py` and `test_authorization_inventory.py` pass
        with the new rows and are **not edited**.
- **Depends on:** Task 10

---

## Group D — Proof

## [x] Task 12: Convergence, all-or-nothing, and the overwrite rules
- **Files:** `src/backend/tests/community/core/bot_config_manifest/test_apply_engine.py`
- **Done when:**
  - [x] **Convergence by absence of writes.** Two applies of an unchanged
        document: every entry `unchanged`, and the startup-script and activation
        services not called on the second.
  - [x] **The transient-failure test.** `mcp` declaring `{A, B}` with B failing
        leaves A exactly as it was, B `failed`, A `skipped`, and neither
        `activate_mcp` nor `deactivate_mcp` called.
  - [x] **`[]` and `DELETE` in one test.** `skills: []` empties its area;
        deleting the manifest empties nothing. One rule, one test.
  - [x] **Per-category area.** Applying a document declaring only `mcp` leaves
        skills, identity files and the workspace untouched.
  - [x] **Reserved identity files** are unreachable from apply — asserted here
        as well as refused at `PUT`, so the guarantee rests on two layers.
  - [x] **No materialiser.** A document declaring `skills` and `script`
        delivers the script, fails every `skills` entry, reports `PARTIAL`.
  - [x] **`dry_run` writes nothing**, proven by counting rows in both new tables
        before and after — and it mints no `apply_id`.
  - [x] **Serialization.** Two concurrent applies: one proceeds, one 409s
        **before minting an id**.
  - [x] **Async lifecycle.** `start_apply` returns while the work is still
        running; the report reads `RUNNING`, then reaches a terminal status. A
        raising orchestrator still terminates the report; a report left
        `RUNNING` past the lock's TTL reads as `FAILED`.
  - [x] **The record never over-claims.** A failure between two categories
        leaves nothing recorded as materialised that was not.
- **Depends on:** Tasks 7, 8, 9

## [x] Task 13: The two-phase proof — W13's call pattern, before W13
- **Files:** same test module as Task 12
- **Done when:**
  - [x] Phase A alone applies `script` and nothing else.
  - [x] Phase B alone applies `mcp` and not `script`.
  - [x] Both together preserve `APPLY_ORDER`'s order and produce one report.
  - [x] The phase-A call reaches no device context and no container — the
        property that makes it callable before provisioning, pinned rather than
        assumed. This is the same discipline W10 used for its uncalled
        `from_spec`.
- **Depends on:** Task 6

## [ ] Task 14: Endpoint tests
- **Files:** `src/backend/tests/community/endpoints/test_openapi_config_manifest_apply.py`
- **Done when:**
  - [ ] All three routes answer through the app with their declared bars
        enforced.
  - [ ] `POST …/apply` returns **202 + `apply_id`**, and polling that id returns
        the report.
  - [ ] An `apply_id` belonging to a **different bot** resolves to nothing on
        this bot's poll route.
  - [ ] No stored manifest ⇒ applies nothing, no error.
  - [ ] Never applied ⇒ `last-apply` is an empty report, not a 404.
  - [ ] A partial apply is a **terminal report saying so**, not a 4xx/5xx — the
        request succeeded; the apply was partial.
  - [ ] Every existing manifest, startup-script and MCP endpoint test passes
        **unedited**.
- **Depends on:** Tasks 10, 11

## [ ] Task 15: Documentation
- **Files:** `.../core/bot_config_manifest/README.md`,
  `docs/bot-config-manifest/user-manual.zh-CN.md`,
  `docs/bot-config-manifest/design.zh-CN.md`
- **Done when:**
  - [ ] The module README's Context Boundary block lists everything the apply
        subpackage provides and consumes. Its `consumes` names the five bot
        configuration services and W10's seam.
  - [ ] A README section states the orchestrator must not grow category logic,
        and names the structural test that enforces it.
  - [ ] The README's "apply does not exist yet" statements are corrected — W1
        wrote several, and leaving them is how a README becomes untrustworthy.
  - [ ] Design §3.4's ordering is annotated as **reversed** in the first phase,
        pointing at work-items §2.12, so a reader of the design is not misled.
  - [ ] The user manual documents all three operations, the start-and-poll
        shape, `dry_run`, the outcome vocabulary, and — plainly — that applying
        a declared category removes what it does not declare.
  - [ ] It states when a `script` takes effect in the terms the mechanism has:
        delivered on apply, executed at the next device provisioning (create,
        restart, republish), never re-run inside a container already up.
- **Depends on:** Task 14
