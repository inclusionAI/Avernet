# V1 Create-Group `originator` + dedicated-V1 reachability — Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax. This plan supersedes `docs/superpowers/specs/2026-08-19-v1-create-group-originator-design.md` (the spec assumed prod stays legacy-anchored; the confirmed direction is A+B + Option 1 with a **dedicated V1 `GroupManagement` instance**).

**Goal:** Let V1 `POST /openapi/v1/collaboration/groups` accept an optional `originator`; authorize caller↔originator (self or caller-owned bot); drop caller↔driver; and (only when originator is an owned bot) require the driver to be reachable from that originator bot — by giving the V1 facade its own `for_v1_openapi` `GroupManagement` instance so participants stay driver-anchored like the tests already expect.

**Architecture:** Facade (`bcs-app-group`) owns the new V1 policy. A second, `for_v1_openapi` `GroupManagement` is constructed in `server.rs` and wired into `build_openapi_v1_state` only; legacy HTTP keeps the existing instance untouched. Core/`bcs-group` unchanged.

**Tech Stack:** Rust workspace. Commands use `-p` flags; **no `cargo fmt`** (repo rule). Pre-push is lint-only by default.

## Global Constraints

- `originator` default = `principal.actor_id()` (the human caller, `human_<staff_no>`), persisted into `group.originator`.
- Identity rule (facade `authorize_originator`): `originator == caller` OR (`originator` is a registered Bot with `created_by == caller.user.id`); else `403`.
- `caller↔driver` check is **dropped** (remove `ensure_collaboration_eligible(driver)` from `create_collaboration`; `create_dm` keeps its target check).
- `driver↔originator` check runs **only when `originator != caller`** (the owned-bot case): driver must be `public` OR `friend(originator_bot, driver)`; else `403`.
- Legacy `POST /groups` behavior must NOT change → `for_v1_openapi` goes on a **separate** instance feeding only the V1 facade.

---

### Task 1: Plumb `originator` field (contract + DTO + test literals, behavior unchanged)

**Files:**
- Modify: `crates/service-api/bcs-service-api/src/application/v1/group.rs:266` (`CreateCollaborationGroup`)
- Modify: `crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs:186` (`CreateGroupRequest::Normal`) and `:200` (`From`)
- Modify: `crates/application/v1/bcs-app-group/tests/v1_group_service.rs` (≈24 `CreateCollaborationGroup {` literals)

**Interfaces:**
- Produces: `CreateCollaborationGroup { pub originator: Option<String>, .. }` and DTO `Normal { originator: Option<String>, .. }` threaded via `From`. The facade still ignores `request.originator` until Task 2.

- [ ] **Step 1: Add the service-contract field.** In `CreateCollaborationGroup`, append:
  ```rust
  /// Caller-designated originator. `None` ⇒ resolve to the authenticated
  /// caller principal at the facade (current behavior).
  pub originator: Option<String>,
  ```

- [ ] **Step 2: Add the DTO field.** In `CreateGroupRequest::Normal` add (struct is `deny_unknown_fields`; still optional):
  ```rust
  originator: Option<String>,
  ```

- [ ] **Step 3: Thread in `From`.** In `From<CreateGroupRequest> for CreateGroupSpec`, the `Normal` arm mapping to `CreateCollaborationGroup { .. }`, add:
  ```rust
  originator: value.originator,
  ```

- [ ] **Step 4: Fix breaking struct literals.** Add `originator: None,` to each `CreateCollaborationGroup { … }` in `tests/v1_group_service.rs`. Find them:
  ```bash
  grep -n "CreateCollaborationGroup {" crates/application/v1/bcs-app-group/tests/v1_group_service.rs
  ```
  (One-line transform per site; field order is irrelevant in Rust literals.)

- [ ] **Step 5: Build.**
  ```bash
  cargo build -p bcs-service-api -p bcs-api-http -p bcs-app-group
  ```
  Expected: clean compile (compile errors name any missed literal).

- [ ] **Step 6: Run existing tests — behavior must be unchanged.**
  ```bash
  cargo test -p bcs-app-group
  ```
  Expected: existing suite green (facade still hardcodes originator = caller; `originator: None` ⇒ same).

- [ ] **Step 7: Commit.**
  ```bash
  git add -A && git commit -m "feat(bcs): plumb originator field through V1 create-group contract"
  ```

---

### Task 2: Facade `authorize_originator` + driver↔originator(bot) gate; drop caller↔driver

**Files:**
- Modify: `crates/application/v1/bcs-app-group/src/lib.rs` (`create_collaboration` ~line 623, new helper near `ensure_collaboration_eligible` ~line 170)
- Test: `crates/application/v1/bcs-app-group/tests/v1_group_service.rs`

**Interfaces:**
- Consumes: `CreateCollaborationGroup.originator` (Task 1), `self.load_bot` (`lib.rs:84`), `self.friends` (FriendCoreService), `Principal::Human(human).subject.id`, `principal.actor_id()`.
- Produces: the V1 create-group policy.

- [ ] **Step 1: Write the failing service tests** (append in `v1_group_service.rs`). Use a fixture where the caller is `human_principal_with_profile("staff-1", …)` unless noted.
  - (a) `create_uses_authenticated_human_as_originator_by_default` — omit `originator` ⇒ `detail.originator_actor_id == "human_staff-1"`. (Overlaps the existing `create_uses_the_authenticated_human_as_originator`; keep that, add the explicit forward-compat variant.)
  - (b) `create_accepts_originator_equal_to_caller` — `originator: Some("human_staff-1".into())` ⇒ OK.
  - (c) `create_rejects_originator_bot_not_owned_by_caller` — caller `human_principal_with_profile("staff-1", …)`, `originator: Some(other_human_owned_bot)` ⇒ `ApplicationError`/forbidden; `expect_err`.
  - (d) `create_rejects_unregistered_originator` — `originator: Some("nope".into())` ⇒ `expect_err`.
  - (e) `create_rejects_other_human_originator` — `originator: Some("human_staff-2".into())` ⇒ `expect_err`.
  - (f) `create_accepts_owned_bot_originator_with_reachable_driver` — caller owns `bot-O` (`add_bot_with_created_by`), `originator: Some("bot-O")`, driver public ⇒ OK, `detail.originator_actor_id == "bot-O"`.
  - (g) `create_rejects_owned_bot_originator_when_driver_unreachable_from_it` — `originator: Some("bot-O")`, driver = protected bot that is **not** public and **not** a friend of `bot-O` ⇒ `expect_err`.
  - (h) `create_does_not_gate_driver_against_caller_when_originator_is_human` — caller `staff-1`, driver = a protected bot owned by **another** human (not staff-1, not staff-1's friend) ⇒ **OK** (driver ungated vs caller). Assert success.

  Reuse existing fixture helpers: `add_public_bot`, `add_protected_bot`, `friends.add_friendship`, `bot_principal` (which is `human_principal_with_profile(bot_uuid, …)` — i.e. a human caller whose id == the bot name; verify whether the suite has an `add_bot_with_created_by(owner_staff)` helper; if not, register a bot then call `bot_registry.save_created_by(bot, staff, true)` or the test-only equivalent used elsewhere — locate it via `grep -n "save_created_by\|created_by" crates/application/v1/bcs-app-group/tests/`.

- [ ] **Step 2: Run tests to verify they fail.**
  ```bash
  cargo test -p bcs-app-group -- create_uses_authenticated_human_as_originator_by_default create_accepts_originator_equal_to_caller create_rejects_originator_bot_not_owned_by_caller create_rejects_unregistered_originator create_rejects_other_human_originator create_accepts_owned_bot_originator_with_reachable_driver create_rejects_owned_bot_originator_when_driver_unreachable_from_it create_does_not_gate_driver_against_caller_when_originator_is_human
  ```
  Expected: FAIL — originator ignored / driver still caller-gated.

- [ ] **Step 3: Add helper `authorize_originator`.** Near `ensure_collaboration_eligible` (`lib.rs:170`):
  ```rust
  /// The authenticated human caller may act as itself or as an owned Bot
  /// originator. Anything else is forbidden. (Stricter than legacy
  /// `authorize_originator`, which lets any human designate any originator.)
  async fn authorize_originator(
      &self,
      principal: &Principal,
      originator: &str,
  ) -> Result<(), ApplicationError> {
      if principal.actor_id() == originator {
          return Ok(());
      }
      let bot = self.load_bot(originator).await.map_err(|error| match error {
          ApplicationError::NotFound { .. } => ApplicationError::forbidden(format!(
              "Authenticated User cannot act as originator '{originator}'"
          )),
          other => other,
      })?;
      if bot.actor_kind != ActorKind::Bot {
          return Err(ApplicationError::invalid(
              "invalid_originator",
              "originator must be a Bot Actor",
          ));
      }
      if let Principal::Human(human) = principal {
          if bot.created_by.as_deref() == Some(human.subject.id.as_str()) {
              return Ok(());
          }
      }
      Err(ApplicationError::forbidden(format!(
          "Authenticated User cannot act as originator '{originator}'"
      )))
  }
  ```
  And add the driver↔originator(bot) gate:
  ```rust
  /// When the originator is a caller-owned Bot (≠ caller), the driver must be
  /// reachable from that originator bot (public or friend). Skipped when the
  /// originator is the human caller itself (driver is ungated vs caller).
  async fn ensure_originator_can_reach_driver(
      &self,
      originator_bot_id: &str,
      driver_bot_id: &str,
  ) -> Result<(), ApplicationError> {
      let driver = self.load_bot(driver_bot_id).await?;
      if driver.status == ActorStatus::Hidden {
          return Err(ApplicationError::forbidden(format!(
              "Bot '{driver_bot_id}' is hidden and cannot be invited into a group"
          )));
      }
      if driver.capabilities.visibility == "public" {
          return Ok(());
      }
      if self.friends.are_friends(originator_bot_id, driver_bot_id).await {
          return Ok(());
      }
      Err(ApplicationError::forbidden(format!(
          "Driver '{driver_bot_id}' is not reachable from originator '{originator_bot_id}'"
      )))
  }
  ```
  (`are_friends`, `ActorStatus`, `ActorKind` are already imported/used by `ensure_collaboration_eligible`.)

- [ ] **Step 4: Wire into `create_collaboration` (`lib.rs:623`).** Replace the `ensure_collaboration_eligible(driver)` call (~line 628) and the `originator = principal_actor_id.clone()` assignment (~line 670):
  ```rust
  let originator = request.originator.take().unwrap_or_else(|| principal.actor_id());
  self.authorize_originator(&principal, &originator).await?;
  if originator != principal.actor_id() {
      self.ensure_originator_can_reach_driver(&originator, &request.driver_bot_uuid).await?;
  }
  ```
  Keep the existing later use of the `originator` local in `GroupCreateCommand { originator: Some(originator), .. }` (lib.rs:782). Remove the now-dead `let originator = principal_actor_id.clone();` line.

- [ ] **Step 5: Run the new tests — expect pass.**
  ```bash
  cargo test -p bcs-app-group -- create_
  ```
  Expected: PASS (tests run `for_v1_openapi`, which is the target core behavior).

- [ ] **Step 6: Full suite regression.**
  ```bash
  cargo test -p bcs-app-group
  ```
  Expected: green. If an existing test asserted driver-was-gated-vs-caller (caller↔driver rejection, previously via `ensure_collaboration_eligible(driver)`), it will now break — that breakage is **expected and intended**; update the test to reflect the dropped check (doc the reason in the test).

- [ ] **Step 7: Commit.**
  ```bash
  git add -A && git commit -m "feat(bcs): V1 create-group authorizes caller↔originator, not caller↔driver"
  ```

---

### Task 3: DTO contract test (originator field accepted/optional)

**Files:**
- Modify: `crates/adapters/http/bcs-api-http/tests/group_routes.rs`

- [ ] **Step 1: Add a test** that POSTs a `Normal` body **without** `originator` ⇒ 2xx (existing behavior) and one **with** `originator: "human_<caller>"` ⇒ 2xx (accepted, no `unknown field`). Mirror an existing `create` happy-path test for wiring (route + caller principal fixture).

- [ ] **Step 2: Run.**
  ```bash
  cargo test -p bcs-api-http --test group_routes
  ```
  Expected: PASS.

- [ ] **Step 3: Commit.**
  ```bash
  git add -A && git commit -m "test(bcs): V1 group create accepts optional originator field"
  ```

---

### Task 4: Dedicated V1 `GroupManagement` instance (the wiring fix)

**Files:**
- Modify: `crates/bootstrap/bcs/src/server.rs` (~line 1870 build, ~line 1978 `build_openapi_v1_state` call)

> **Checkpoint:** this changes production bootstrap wiring (least unit-tested). Flag for user review before this task; execute only after confirmation. Legacy HTTP must keep the existing instance.

- [ ] **Step 1: Construct a second `GroupManagement` for V1.** Immediately after the existing `group_management_impl` block (`server.rs:1870-1886`), add a V1 twin:
  ```rust
  let group_management_v1_impl = Arc::new(GroupManagement::new(
      sessions.clone(),
      bot_registry.clone(),
      friend_store.clone(),
      relation_store.clone(),
      GroupConfig {
          max_group_members: config.max_group_members,
          max_groups_as_driver: config.max_groups_as_driver,
          max_groups_as_member: config.max_groups_as_member,
          relation_env: crate::env::resolve_env(),
      },
      session_management.clone(),
      system_message.clone(),
  )
  .with_channel_binding_cleanup(channel_binding_cleanup.clone())
  .with_outbound_url_guard(outbound_url_guard.clone())
  .with_bot_runtime(bot_use_cases.clone())
  .for_v1_openapi());
  ```

- [ ] **Step 2: Wrap for runtime cleanup like the legacy one.** After the legacy `group_management = maybe_wrap_group_management(...)` block (`server.rs:1944-1950`), add:
  ```rust
  let group_management_v1 = maybe_wrap_group_management(
      &config,
      Arc::new(GroupManagementWithRuntimeCleanup::new(
          group_management_v1_impl,
          collaboration_runtime.clone(),
      )),
  );
  ```

- [ ] **Step 3: Wire V1 instance into `build_openapi_v1_state`.** At `server.rs:1978` change the argument from `group_management.clone()` to `group_management_v1.clone()`. Legacy HTTP routing (`:2041-2043`) keeps using `group_management` / `group_management_impl` — **do not touch**.

- [ ] **Step 4: Build.**
  ```bash
  cargo build -p bcs
  ```
  Expected: clean compile.

- [ ] **Step 5: Smoke test the server binary compiles + existing V1 tests still green** (they already used `for_v1_openapi`; prod now matches):
  ```bash
  cargo test -p bcs-app-group -p bcs-api-http
  ```

- [ ] **Step 6: Commit.**
  ```bash
  git add -A && git commit -m "fix(bcs): give V1 facade a dedicated for_v1_openapi group-management instance"
  ```

---

### Task 5: Final gate + lint

- [ ] **Step 1: Build the workspace touched.**
  ```bash
  cargo build -p bcs -p bcs-service-api -p bcs-api-http -p bcs-app-group
  ```

- [ ] **Step 2: Clippy (no `cargo fmt`).**
  ```bash
  cargo clippy -p bcs-app-group -p bcs-api-http -p bcs --no-deps
  ```

- [ ] **Step 3: Full test of touched crates.**
  ```bash
  cargo test -p bcs-app-group -p bcs-api-http -p bcs-service-api
  ```

- [ ] **Step 4: If clean, summarize for user; do not push unless asked.**

---

## Notes / risks

- Task 4 duplicating `GroupManagement` shares the same repos/sessions (the wrapper is stateless-around-shared-persistence); safe. The two instances differ only in the `v1_openapi_create_policy` flag.
- If Task 2 Step 6 reveals the existing suite relied on `ensure_collaboration_eligible(driver)` rejecting ineligible drivers, those tests encode the behavior we are intentionally removing — update them in-task, not as scope creep.
- `create_dm` is intentionally untouched (originator does not apply to DM).
