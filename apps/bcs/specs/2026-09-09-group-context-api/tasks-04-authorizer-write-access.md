# Tasks: Group Context Authorizer Write Access

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: Make add authorization rely on default ownership [x]
- **Goal:** Make `AddGroupContext.collect_from` optional, remove the creator-membership hard check, persist missing policies as empty, and expose owner-only `owners` in `get`.
- **Files:**
  - `apps/bcs/crates/service-api/bcs-service-api/src/application/v1/group_context.rs`
  - `apps/bcs/crates/service-api/bcs-service-api/tests/v1_group_context_application_contracts.rs`
  - `apps/bcs/crates/application/v1/bcs-app-group-context/src/service.rs`
  - `apps/bcs/crates/application/v1/bcs-app-group-context/tests/group_context_service.rs`
  - `apps/bcs/crates/application/v1/bcs-app-session/tests/group_context_access_guard.rs`
- **Done when:**
  - [ ] `AddGroupContext` omits/accepts `collect_from` as `Option<GroupContextPolicy>` and explicit `null` deserializes to `None`.
  - [ ] Add without `collect_from` succeeds and persists an empty policy plus `owners=[actor_id]`.
  - [ ] Add with explicit empty `collect_from` succeeds and persists the empty policy unchanged.
  - [ ] Add with `collect_from` containing another bot grants that bot `W` update access while preserving the acknowledged creator as owner.
  - [ ] Add no longer rejects a creator absent from `collect_from`.
  - [ ] Owner `get` returns `owners`; non-owner `get` omits it.
  - [ ] Update and systemset authorization semantics remain unchanged.
  - [ ] Focused service-api, application, and access-guard tests pass.
- **Depends on:** —

## Task 2: Sync HTTP and CLI transport surfaces [x]
- **Goal:** Use the new optional add contract at both deliveries without introducing domain validation into the adapters.
- **Files:**
  - `apps/bcs/crates/adapters/http/bcs-http/src/routes/group_context.rs`
  - `apps/bcs/crates/adapters/http/bcs-http/tests/group_context_routes.rs`
  - `apps/bcs/crates/tools/bcs-cli/src/main.rs`
  - `apps/bcs/crates/tools/bcs-cli/tests/e2e/group_context_test.rs`
- **Done when:**
  - [ ] HTTP `AddRequest.collect_from` is optional without changing authenticated actor resolution.
  - [ ] HTTP add succeeds with the key omitted and with `{"userIds":[]}`.
  - [ ] CLI `group-context add --collect-from` is optional.
  - [ ] CLI omits `collect_from` from the add JSON body when the flag is absent and preserves it when supplied.
  - [ ] HTTP owner `get` response includes `owners`; non-owner response omits it.
  - [ ] Focused HTTP and CLI body/E2E tests pass.
- **Depends on:** Task 1

## Task 3: Update user-facing semantic documentation
- **Goal:** Keep the bcs-coordination skill documentation aligned with the implemented owner semantics.
- **Files:**
  - `apps/bcs/crates/tools/bcs-cli/bcs-coordination/SKILL.md`
  - `apps/bcs/crates/tools/bcs-cli/bcs-coordination/references/group-context.md`
  - `apps/bcs/crates/tools/bcs-cli/bcs-coordination/test/test.md`
  - `apps/bcs/specs/2026-09-09-group-context-api/spec-04-authorizer-write-access.md`
- **Done when:**
  - [ ] Reference command tables and examples no longer state that `--collect-from` is required for add.
  - [ ] Add guidance says the field is optional and explicitly means additional authorized writers.
  - [ ] Existing “must add self to collect_from” troubleshooting advice is removed.
  - [ ] Get guidance documents owner-only `owners` output.
  - [ ] Feature spec status reflects implementation.
- **Depends on:** Task 2

## Task 4: Verify against existing CI and acceptance criteria
- **Goal:** Run the repository's existing BCS gates without weakening or bypassing them and confirm every spec criterion.
- **Files:**
  - `apps/bcs/specs/2026-09-09-group-context-api/tasks-04-authorizer-write-access.md`
- **Done when:**
  - [ ] Focused BCS tests for service-api, group-context application, HTTP adapter, session guard, and bcs-cli pass.
  - [ ] All existing BCS workspace unit tests pass, or every failure is shown to pre-exist outside this change.
  - [ ] Required diff hygiene/workflow checks pass.
  - [ ] Required Singlebox BCS coverage gate is run or its inability to run is documented with the missing dependency.
  - [ ] Every acceptance criterion in `spec-04-authorizer-write-access.md` is checked and reported.
  - [ ] No coverage threshold, workflow, or architecture guard is weakened.
- **Depends on:** Task 3

---

## Groups

> Groups bundle tasks into end-to-end units of work. `implement` executes
> one group at a time and runs code review on each group before moving on.
> Each task appears in exactly one group.

- **Group A — Application contract:** Task 1
  - Theme: Establish owner-first add semantics in the transport-neutral application contract.
- **Group B — Delivery surfaces:** Task 2
  - Theme: Propagate the contract through HTTP and CLI without moving policy into adapters.
- **Group C — User guidance:** Task 3
  - Theme: Make the public skill/reference documentation match the implementation.
- **Group D — Verification:** Task 4
  - Theme: Run the existing CI-equivalent gates and validate all acceptance criteria.
