# Tasks: Public API — Owner-Granted Bot Authorization for Applications

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> Branch: `claude/github-issue-928-investigation-bia0fl`, based on `dev`.
>
> Scope reminder: this ships the authorization **record** and four operations
> over it — the owner's grant / list / withdraw, plus the application's read of
> which of that owner's bots it may reach. Every one of the four still requires
> an end user. It admits no application-only caller anywhere — that is the
> remaining half of issue #928 and a separate piece of work.
> `verify_principal_token::_require_user_principal` and `require_user_id` must
> come out of this feature **unchanged**; a diff touching either is a scope
> escape, not a fix.

## Task 1: Settle the `app_name` deviation  `[x]`

- **Goal:** Decide, before any schema lands, whether the grant row snapshots the
  application's display name.
- **Files:** `specs/2026-08-10-openapi-v1-bot-app-grant/plan.md` (the
  "Deviation to accept or strike" section)
- **Done when:**
  - [x] The user has said keep or strike. **KEPT** — approved at the review gate.
  - [x] `app_name` stays in the ORM model, the record, the service signature and
        the list response.
  - [x] `spec.md` Open Question 1 closed with the snapshot decision and its
        reasoning; Open Question 2 closed too (idempotent re-grant does not move
        `gmt_create`; re-grant after withdraw is a new period). The section is
        now `Resolved Questions`, kept rather than deleted so a later reader
        sees what was weighed.
- **Depends on:** — (resolved at the review gate, not during implementation)

## Task 2: The grant record  `[!]`

> **BLOCKED — the approved unique key does not survive a second withdrawal.**
>
> `UniqueConstraint(app_id, bot_id, owner_id, env, status)` breaks on
> grant → withdraw → grant → **withdraw**: two rows then hold
> `status='revoked'` for the same `(app_id, bot_id, owner_id, env)`, and
> `status` is `NOT NULL`, so MySQL/OceanBase reject the second withdrawal with
> a duplicate-key error. The plan reasoned about the second *grant* colliding
> with the withdrawn row — that is why `status` is in the key — and did not
> walk one step further. A second withdrawal is ordinary: authorize, withdraw,
> re-authorize, withdraw.
>
> No portable fix exists at this layer. A filtered unique index
> (`WHERE status='active'`) is the textbook answer, but OceanBase is
> MySQL-compatible and MySQL has no partial indexes. Moving `revoked_at` into
> the key inverts the problem: MySQL treats `NULL`s as distinct, so multiple
> *active* rows would be admitted — losing the one invariant that matters.
>
> Two ways out, awaiting a decision:
>
> 1. **Two tables (recommended).** `ac_bot_app_grant` holds live grants only,
>    unique on `(app_id, bot_id, owner_id, env)`, hard-deleted on withdraw;
>    `ac_bot_app_grant_log` is append-only, one row per grant and per withdraw.
>    DB-enforced uniqueness on the live grant, an audit trail that holds
>    unboundedly many periods, and it matches the existing
>    `ac_bot_collaborator` / `ac_bot_collab_log` pair. This **overturns**
>    `plan.md` → Alternatives, which rejected a log table as redundant on the
>    grounds that "a log row would carry nothing the soft-deleted grant row
>    does not" — false, since the soft-deleted row carries exactly one period.
> 2. **One table, repository-enforced.** Drop `status` from the unique key and
>    enforce "at most one active" in the repository inside a transaction.
>    Smaller diff, keeps soft-delete, but moves the guarantee off the database
>    and into code — on a permission record.
>
> Groups C–F all depend on this, so the run stopped here rather than running
> ahead. Nothing schema-shaped has landed.

- **Goal:** One ORM model and its Pydantic record, with a unique key that makes
  grant → withdraw → grant expressible.
- **Files:**
  `src/backend/src/agentclaw/community/core/bot_app_grant/__init__.py` (new),
  `…/core/bot_app_grant/models.py` (new)
- **Done when:**
  - [ ] `BotAppGrantModel` maps `ac_bot_app_grant` with the columns in
        `plan.md` → Data Model Changes: `app_id`, `bot_id`, `owner_id`,
        `tenant`, `env`, `status`, `gmt_create`, `gmt_modified`, `revoked_at`
        (+ `app_name` per Task 1).
  - [ ] `env` defaults from `agentclaw.community.utils.env_utils.get_current_env`
        (`env_utils.py:68`), matching `ac_bot_collaborator`
        (`core/bot_collaborator/models.py:130`).
  - [ ] `UniqueConstraint("app_id", "bot_id", "owner_id", "env", "status")`.
        Both `env` and `status` are in the key, and the module docstring says
        why: `env` so one row cannot collide across environments sharing a
        database, `status` so a withdrawn grant does not block a later re-grant.
  - [ ] Indexes `idx_bot_owner_env_status` and `idx_app_owner_env_status`
        exist — the record's two reading ends. The second is not redundant with
        the unique key: that key's second column is `bot_id`, so it cannot serve
        an `(app_id, owner_id)` lookup that names no bot.
  - [ ] `revoked_at` is nullable, and a comment states this is an intentional
        contract state (an active grant has no revocation time), per `CLAUDE.md`.
  - [ ] `GrantStatus` is an enum with `ACTIVE` / `REVOKED`, not bare strings.
  - [ ] `to_record()` returns `BotAppGrantRecord`, mirroring
        `BotCollaboratorModel.to_record` (`core/bot_collaborator/models.py:146`).
- **Depends on:** Task 1

## Task 3: Repository contract and implementation  `[ ]`

- **Goal:** Persistence behind an enforceable contract, in the consolidated
  `core/repository` package rather than beside the domain module.
- **Files:**
  `…/core/repository/protocols/bot/app_grant.py` (new),
  `…/core/repository/protocols/bot/__init__.py`,
  `…/core/repository/implementations/bot/app_grant.py` (new),
  `…/core/repository/implementations/bot/__init__.py`
- **Done when:**
  - [ ] `BotAppGrantRepositoryProtocol` declares `upsert_active`,
        `list_active_for_bot`, `list_active_for_app`, `find_active`,
        `mark_revoked` — **every member `@abstractmethod`**, domain imports
        under `TYPE_CHECKING` only, per `core/repository/README.md`.
  - [ ] Both `list_active_*` members take `owner_id`, so neither can return a
        row belonging to anyone but the caller. The scoping is in the contract,
        not left to each caller to remember.
  - [ ] The protocol is re-exported from `protocols/bot/__init__.py` so
        importers see one module, as the other `bot` contracts are.
  - [ ] `BotAppGrantRepository` declares the Protocol as a base and takes
        `DatabasePlugin` via `@inject`, modelled on
        `implementations/bot/collaborator.py:34`.
  - [ ] `upsert_active` is idempotent on a live grant and **does not touch
        `gmt_create`** — the record must keep answering "could reach this bot
        from T1" honestly.
  - [ ] `tests/community/architecture/test_repository_contracts.py` passes: every
        member abstract, implementation based, no runtime domain import in
        `protocols/`, contract and body on different paths.
- **Depends on:** Task 2

## Task 4: The grant service (domain policy)  `[ ]`

- **Goal:** One transport-agnostic place holding the grant/withdraw/list rules.
- **Files:**
  `…/core/bot_app_grant/services/__init__.py` (new),
  `…/core/bot_app_grant/services/grant_service.py` (new),
  `…/core/bot_app_grant/errors.py` (new)
- **Done when:**
  - [ ] `BotAppGrantService.grant(bot_id, owner_id, app_id, app_name, tenant)`
        writes `owner_id` and `tenant` as **resolved values**, never anything
        the request supplied.
  - [ ] Re-granting a live grant returns the existing record unchanged — same
        `gmt_create` — and creates no second row.
  - [ ] Re-granting after a withdraw inserts a **new** row, leaving the
        withdrawn row's `[gmt_create, revoked_at]` interval intact.
  - [ ] `revoke` raises `GrantNotFoundError` when no live grant matches, so the
        adapter can answer 404 distinctly from a successful withdraw.
  - [ ] `list_for_bot` returns live grants only.
  - [ ] `list_for_app(app_id, owner_id)` returns the owner's bots this app may
        reach, live only. It performs **no bot-existence check** — deliberately
        asymmetric with the other three, which resolve a named bot and inherit
        the masked 404 from that read. This one names no bot, so there is
        nothing to mask.
  - [ ] No FastAPI/HTTP import anywhere in the module (Rule 7: core is
        transport-agnostic).
- **Depends on:** Task 3

## Task 5: DI binding  `[ ]`

- **Goal:** The service and repository resolve through the container.
- **Files:** `…/di/modules/bot_app_grant_module.py` (new), the container that
  installs the module list
- **Done when:**
  - [ ] The module binds `BotAppGrantRepositoryProtocol` →
        `BotAppGrantRepository` and provides `BotAppGrantService`, modelled on
        `di/modules/bot_collaborator_module.py:52`.
  - [ ] The module is installed in the container alongside the other domain
        modules.
  - [ ] The app boots and the service resolves — verified by an existing
        container/bootstrap test, not by inspection.
- **Depends on:** Task 4

## Task 6: The `authorized-apps` router group  `[ ]`

- **Goal:** Four routes with the consent asymmetry wired through existing
  dependencies and no new authorization code.
- **Files:**
  `…/adapters/http/openapi_v1/authorized_apps/__init__.py` (new),
  `…/adapters/http/openapi_v1/authorized_apps/router.py` (new),
  `…/adapters/http/openapi_v1/authorized_apps/schemas.py` (new)
- **Done when:**
  - [ ] `POST ""` declares `require_user_and_app_principal`
        (`dependencies.py:194`) and reads `app_id` / `app_name` / `tenant` off
        the `AppPrincipal`. **No `app_id` request parameter exists** — a caller
        cannot name an application other than its own.
  - [ ] `GET ""` and `DELETE "/{app_id}"` declare `require_principal` only, so
        an owner can list and withdraw without any application credential.
  - [ ] A second router at prefix `/authorized-bots` serves the app's view:
        `GET ""` declaring `require_user_and_app_principal`, scoped by the
        `app_id` read off the principal. **No `app_id` parameter** — it cannot
        be used to ask what some other application may reach.
  - [ ] The app's view answers `200` with an empty page when the app holds no
        grants from this owner — not `404`. "You have nothing here" is a valid
        answer, and the owner's existence is already implied by their own
        credential being on the call.
  - [ ] All four take `UserIdDep` (`principal.py:205`), unchanged in meaning.
  - [ ] Owner-only authority comes from the existing owner-scoped bot read
        raising `BotNotFoundError` — no new permission check is written, and a
        non-owner's answer is byte-identical to an absent bot's.
  - [ ] Responses use the surface's `Envelope` / `Page` / `Deleted` contracts
        and `envelope_errors`, as `bots/router.py` does.
  - [ ] The module docstring states the asymmetry and why (consent needs both
        parties; withdrawal needs only the one withdrawing).
- **Depends on:** Task 5

## Task 7: Register the group  `[ ]`

- **Goal:** The routes are mounted, documented, and not shadowed.
- **Files:** `…/adapters/http/openapi_v1/__init__.py:155`
- **Done when:**
  - [ ] Both routers are added to the literal sub-group list, mounted **before**
        the `{bot_id}` wildcard `bots` router. `authorized-bots` is a top-level
        literal and genuinely needs to precede it.
  - [ ] All four routes resolve to their handlers — asserted by a test, not by
        starting the app and looking.
  - [ ] The group inherits the surface-wide error-response table; it is not
        added to `_GROUPS_WITHOUT_CALLER_SCOPE` (every route here **is** scoped
        by the caller's user).
- **Depends on:** Task 6

## Task 8: The gateway route-security rule  `[ ]`

- **Goal:** The gateway refuses a grant request that does not carry both
  identities, before it ever reaches the backend.
- **Files:** `src/gateway/configs/application.yaml:111`
- **Done when:**
  - [ ] `"POST /openapi/v1/bots/{bot_id}/authorized-apps": {user: required,
        app: required}` is present.
  - [ ] `"/openapi/v1/authorized-bots": {user: required, app: required}` is
        present — not method-qualified, since GET is the only method this path
        has and every method it could grow answers the same question.
  - [ ] Both comment blocks from `plan.md` are included: why granting needs both
        parties; why GET/DELETE deliberately inherit `user: required` from
        `/openapi/v1/bots/**` rather than restating it; and why the app's view
        declares `app` — the runner resolves only declared identities
        (`_runner.py:40`), so without it the App would be invisible and the
        query would have nothing to scope by. Written in the style of the
        existing `WEBSOCKET` rule commentary in that file, which is the
        precedent for explaining a non-obvious rule in place.
  - [ ] No other rule in the table changes.
- **Depends on:** — (independent of the backend tasks; ships with them)

## Task 9: Gateway resolution tests  `[ ]`

- **Goal:** Prove the new rule actually wins, against the config that ships.
- **Files:** `src/gateway/tests/unit/core/authn/test_route_security.py`
- **Done when:**
  - [ ] A test asserts `POST /openapi/v1/bots/{any}/authorized-apps` resolves to
        `{user: required, app: required}`, beating `/openapi/v1/bots/**`.
  - [ ] A test asserts `GET` and `DELETE` on the same path resolve to
        `user: required` only.
  - [ ] A test asserts `GET /openapi/v1/authorized-bots` resolves to
        `{user: required, app: required}`, beating the `"/**"` default.
  - [ ] All load the **real** `src/gateway/configs/application.yaml` via
        `RouteSecurity.from_yaml`, not a fixture table — a typo in the shipped
        config must fail the suite.
  - [ ] `pytest src/gateway/tests/unit/core/authn/test_route_security.py` passes.
        Note: `src/gateway` runs nothing in the default lint-only pre-push mode
        (`AGENTS.md` → Pre-push Module Selection), so this must be run
        explicitly.
- **Depends on:** Task 8

## Task 10: Router and service tests  `[ ]`

- **Goal:** The behaviors `spec.md` promises are pinned.
- **Files:**
  `src/backend/tests/community/adapters/http/openapi_v1/authorized_apps/test_router.py` (new),
  `src/backend/tests/community/core/bot_app_grant/test_grant_service.py` (new)
- **Done when:**
  - [ ] `test_grant_reads_app_from_principal_not_from_request`
  - [ ] `test_grant_is_idempotent_and_does_not_move_granted_at`
  - [ ] `test_regrant_after_revoke_creates_a_new_period`
  - [ ] `test_list_excludes_revoked_grants`
  - [ ] `test_revoke_absent_grant_is_404_distinct_from_successful_revoke`
  - [ ] `test_non_owner_answer_is_byte_identical_to_absent_bot` — compares
        status **and** body, since "byte-identical" is the promise.
  - [ ] `test_collaborator_may_operate_but_may_not_grant` — pins that this
        surface is narrower than the MEMBER+ operator bar
        (`core/engine_runtime/gate.py:78`).
  - [ ] `test_cross_tenant_bot_is_not_grantable` — the tenant guard is inherited
        from `AvernetTenantMiddleware` rather than written here, so it is
        asserted rather than trusted; an untested inherited guard is one a later
        change can remove without noticing.
  - [ ] `test_owner_and_tenant_are_resolved_at_write_time_not_read_from_request`
  - [ ] `test_revoked_row_retains_its_interval`
  - [ ] The app's view — `test_list_authorized_bots_is_scoped_to_the_calling_app`
        (two apps granted on one owner's bots see disjoint lists),
        `test_list_authorized_bots_is_scoped_to_the_calling_owner` (one app
        granted by two owners sees only the calling owner's bots),
        `test_list_authorized_bots_excludes_revoked`, and
        `test_list_authorized_bots_empty_is_200_not_404`. The first two are the
        ones that matter: a listing that silently widens is worse than one that
        fails, so both scoping dimensions are pinned separately.
- **Depends on:** Task 7

## Task 11: Principal-seam assertions  `[ ]`

- **Goal:** The identity requirement is pinned at the route level, where a
  future route cannot quietly omit it.
- **Files:** `src/backend/tests/community/adapters/http/openapi_v1/test_principal_seam.py`
- **Done when:**
  - [ ] `test_authorized_apps_post_requires_user_and_app_principal`, mirroring
        `test_bot_logs_routes_require_user_and_app_principal` (`:361`) — it
        walks the dependency tree, so it catches a handler that forgets the
        dependency even if the gateway rule is right.
  - [ ] `test_authorized_bots_get_requires_user_and_app_principal` — the same
        walk. This one is load-bearing beyond auth: the handler scopes its query
        by the App principal, so a missing dependency would not merely weaken a
        check, it would leave the listing with nothing to filter by.
  - [ ] `test_authorized_apps_get_and_delete_require_only_principal`.
  - [ ] The existing assertion that every public route depends on
        `require_principal` still passes with the new group mounted.
- **Depends on:** Task 7

## Task 12: Regenerate the published description  `[ ]`

- **Goal:** The gateway's served OpenAPI document includes the four new
  operations and nothing else moves.
- **Files:** `src/gateway/configs/schemas/bots.openapi.json` (regenerated output)
- **Done when:**
  - [ ] `python src/backend/scripts/dump_openapi.py` has been run and the
        artifact regenerated.
  - [ ] The diff adds exactly the four operations — no unrelated churn. A
        noisy diff means the dump is non-deterministic and must be investigated,
        not committed.
  - [ ] `src/gateway/tests/fixtures/bots.openapi.json` is **NOT** regenerated —
        it is a frozen test fixture, per
        `src/gateway/specs/2026-08-03-gateway-path-specific-domain-routing/tasks.md:212`.
- **Depends on:** Task 7

## Task 13: Coverage-manifest verification  `[ ]`

- **Goal:** Decide, on evidence, whether the new core module must be registered
  for singlebox coverage — rather than assuming either way.
- **Files:** `scripts/ci/singlebox_coverage_modules.yaml` (only if required)
- **Done when:**
  - [ ] Confirmed how the manifest treats backend core paths: it enumerates
        `core_paths` per registered module (10 backend modules today, e.g.
        `bot_collaborator` at `:144`), so it is a **registry of user-story
        modules, not a mirror of every core directory**.
  - [ ] Established whether recent `openapi_v1` work registered new modules or
        followed the documented "pending openapi_v1 acceptance coverage" pattern
        (`singlebox_coverage_modules.yaml:291`), and matched that precedent.
  - [ ] If registration **is** required: `core_paths`, `router_api` items and
        real acceptance stories are declared, thresholds set from a fresh
        focused run, then the all-module gate run to catch shared-stack
        interference — per `AGENTS.md`. Coverage is **not** manufactured by
        excluding production paths or adding test-only calls.
  - [ ] If registration is **not** required: the reason is recorded in the PR
        description, so the omission is a decision rather than a gap.
- **Depends on:** Task 10

## Task 14: Spec acceptance verification  `[ ]`

- **Goal:** Every acceptance criterion in `spec.md` is demonstrably met.
- **Files:** `specs/2026-08-10-openapi-v1-bot-app-grant/spec.md`
- **Done when:**
  - [ ] Every checkbox under `spec.md` → Acceptance Criteria is ticked, each
        traceable to a named passing test.
  - [ ] `_require_user_principal` and `require_user_id` are **unmodified** —
        confirmed by `git diff`, not by memory. The out-of-scope promise is that
        no application-only caller reaches anything, **including the four routes
        this feature adds** — the application's view requires the owner
        alongside it.
  - [ ] Backend gate green: `scripts/ci/pre_push.sh` (or
        `OCB_PRE_PUSH_RUN_CI=1 git push`) — SAST, unit tests, changed-line
        coverage, singlebox coverage.
  - [ ] The gateway test from Task 9 run explicitly, since the gateway module
        has no standalone lint step.
  - [ ] Both `spec.md` Open Questions are closed in the file.
- **Depends on:** Tasks 9, 11, 12, 13

---

## Groups

- **Group A — Deviation decision:** Task 1
  - Theme: Settle `app_name` before any schema exists. Resolved at the review
    gate; costs one word now and a schema change later.
- **Group B — The record and its contract:** Tasks 2, 3, 4, 5
  - Theme: The persisted authorization and its domain policy, with no HTTP.
    Lands green and useful on its own — the record exists and is writable
    through the container before anything exposes it.
- **Group C — The HTTP surface:** Tasks 6, 7
  - Theme: The owner's three operations plus the application's view, wired
    through existing dependencies. After this group the feature is end-to-end
    usable against a locally-signed principal.
- **Group D — The gateway rule:** Tasks 8, 9
  - Theme: The consent requirement enforced at the edge, and proved against the
    config that actually ships.
- **Group E — Tests and artifacts:** Tasks 10, 11, 12, 13
  - Theme: Pin the promised behaviors, publish the description, settle the
    coverage question on evidence.
- **Group F — Verification:** Task 14
  - Theme: Final spec acceptance check, including the negative promise that the
    admission path did not move.
