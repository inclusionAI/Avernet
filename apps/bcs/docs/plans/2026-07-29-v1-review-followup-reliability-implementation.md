# BCN OpenAPI V1 Review Follow-up Reliability Implementation Plan

> **For agentic workers:** REQUIRED SKILL: Use executing-plans to implement this
> plan task by task. Keep every Legacy call path on its existing infallible
> contract; only V1 paths may adopt the new fallible companions.

**Goal:** Fix the five actionable review findings on PR #514 while preserving
all existing Legacy behavior and leaving the production V1 router unmounted
until a signed Gateway Principal verifier is available.

**Architecture:** Add backward-compatible fallible companion methods at the
Service API boundary, override them only in the real persistence-backed
implementations, and route V1 application paths through them. Validate
duplicate request participants before Legacy canonicalization, and align the
OpenAPI error-code declaration with the existing V1 behavior.

**Tech Stack:** Rust, async-trait, Cargo workspace tests, Python pytest,
OpenAPI YAML.

**Global constraints:**

- Do not change existing Legacy method signatures or their fallback behavior.
- Do not add tenant semantics or change Human Principal/Legacy actor mapping.
- Do not mount the V1 router with the unsigned test-header verifier.
- Do not run repository-wide `cargo fmt`; format only touched Rust files if
  necessary.
- Follow red-green TDD for every behavior change.

---

### Task 1: Declare the existing public-group conflict

**Files:**

- Modify: `src/bcs/tests/openapi/test_group_v1_contract.py`
- Modify: `src/bcs/api-contracts/v1/openapi/groups.yaml`

- [ ] Add an assertion that POST `/openapi/v1/groups` declares exactly
  `conflict` and `non_public_participant` for HTTP 409.
- [ ] Run
  `uv run pytest src/bcs/tests/openapi/test_group_v1_contract.py -q` from the
  repository root and confirm the new assertion fails.
- [ ] Add `non_public_participant` to the POST 409 `x-error-codes` list.
- [ ] Re-run the focused contract test and confirm it passes.

### Task 2: Reject duplicate participant Actor IDs

**Files:**

- Modify: `src/bcs/crates/services/bcs-group-v1/tests/v1_group_service.rs`
- Modify: `src/bcs/crates/services/bcs-group/src/application/group.rs`

- [ ] Add a V1 service test that supplies the same non-driver `actor_id` twice
  with different roles and expects `ApplicationError::InvalidInput` with code
  `invalid_participant`.
- [ ] Run the new focused `bcs-group` test and confirm the current
  order-dependent acceptance makes it fail.
- [ ] Validate uniqueness of the request-provided participant Actor IDs before
  adding the implicit driver or constructing the Legacy command.
- [ ] Re-run the focused test and the existing V1 group-service tests.

### Task 3: Propagate friendship lookup failures through V1

**Files:**

- Modify:
  `src/bcs/crates/service-api/bcs-service-api/src/core/friend.rs`
- Modify:
  `src/bcs/crates/services/bcs-friend/src/core/friend_core.rs`
- Modify:
  `src/bcs/crates/services/bcs-friend/tests/conformance_friend_services.rs`
- Modify:
  `src/bcs/crates/services/bcs-group-v1/tests/v1_group_service.rs`
- Modify:
  `src/bcs/crates/services/bcs-group/src/application/group.rs`

- [ ] Add a `FriendCore` conformance test using a repository that returns a
  storage error and assert that the desired `try_are_friends` method propagates
  it. Run the test and confirm it fails to compile because the companion
  method is absent.
- [ ] Add
  `FriendCoreService::try_are_friends(...) -> ServiceResult<bool>` with a
  compatibility default that wraps the existing `are_friends` result.
- [ ] Override `try_are_friends` in the real `FriendCore` and return the
  repository result without converting failure into `false`.
- [ ] Re-run the Friend conformance test and confirm it passes.
- [ ] Add a V1 collaboration-creation test using the real `FriendCore` backed
  by the failing repository. Assert that eligibility lookup returns
  `ApplicationError::Internal`, not `friendship_required`.
- [ ] Run the focused V1 test and confirm it fails while the V1 path still
  calls `are_friends`.
- [ ] Change only the V1 eligibility path to call `try_are_friends` and map
  `ServiceError` through the existing application error mapper.
- [ ] Re-run focused and existing Friend/Group tests.

### Task 4: Propagate session-membership lookup failures

**Files:**

- Modify:
  `src/bcs/crates/service-api/bcs-service-api/src/port/repo/session.rs`
- Modify:
  `src/bcs/crates/services/bcs-session-store/src/mysql.rs`
- Modify:
  `src/bcs/crates/services/bcs-session-store/tests/conformance_session_repo.rs`
- Modify:
  `src/bcs/crates/services/bcs-session/src/application.rs`
- Modify/add the closest focused Session application test file discovered in
  `src/bcs/crates/services/bcs-session/`.

- [ ] Add a MySQL Session Store conformance test that invokes the desired
  `try_list_group_ids_by_session_participant` method against a failing DB
  plugin and expects a `ServiceError`. Run it and confirm the absent companion
  method causes failure.
- [ ] Add
  `SessionRepoPort::try_list_group_ids_by_session_participant(...)` with a
  default that wraps the existing infallible method.
- [ ] Override the companion in `MySqlSessionStore`, propagating both query
  failures and row-decoding failures.
- [ ] Re-run the store conformance test and confirm it passes.
- [ ] Add a Session application test whose repository returns an error only
  from the new companion and assert that
  `list_group_ids_by_session_participant` propagates it. Run it and confirm the
  application still hides the error by calling the Legacy repository method.
- [ ] Change the Session application implementation to call the companion.
- [ ] Re-run focused Session Store, Session, and V1 Group tests.

### Task 5: Propagate both session deletion failures

**Files:**

- Modify:
  `src/bcs/crates/services/bcs-session-store/tests/conformance_session_repo.rs`
- Modify:
  `src/bcs/crates/services/bcs-session-store/src/mysql.rs`

- [ ] Add a DB test double that can fail either the participant-delete execute
  or the session-row-delete execute by call index.
- [ ] Add one test for each failure point and assert
  `MySqlSessionStore::delete` returns `ServiceError`.
- [ ] Run the focused tests and confirm the current implementation reports
  success/absence instead.
- [ ] Propagate the participant delete error before issuing the session delete.
- [ ] Propagate the session-row delete error instead of returning `Ok(false)`.
- [ ] Re-run the focused store tests and the Runtime cleanup tests that depend
  on Session deletion.

### Task 6: Verify, publish, and close addressed review threads

**Files:**

- Verify all files changed by Tasks 1-5.

- [ ] Run the affected OpenAPI, Friend, Session Store, Session, Group, and
  Collaboration Runtime tests.
- [ ] Run `git diff --check` and inspect `git diff --stat` plus the complete
  patch for accidental Legacy or router-mount changes.
- [ ] Commit the implementation with an intentional message and push the PR
  branch using `--no-verify`, as requested.
- [ ] Reply to each of the five fixed review threads with the root cause,
  concrete fix, and test evidence, then resolve those threads.
- [ ] Leave the production-router-mount thread unresolved and reply only if
  new information is needed; it remains blocked on a real signed Gateway
  Principal verifier.
- [ ] Inspect the updated PR checks and report any remaining CI failure with
  its actual log evidence.
