# Human Actor Bot Candidates Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend the existing BCN OpenAPI Bot candidates operation so the authenticated Human's own `human_{subject.id}` record can select the same candidate Bot directory as a managed physical Bot.

**Architecture:** Keep the existing HTTP route, Bot-oriented Service API, Core method, repository query, response model, and SQL. Evolve the OpenAPI contract first, then change only `bcs-app-bot` candidate authorization to accept either an owned physical Bot or the current Human Actor before delegating to the existing friend and control-plane candidate services.

**Tech Stack:** OpenAPI 3.1 YAML, Python/PyYAML/pytest contract tests, Rust/Axum/async-trait, Cargo tests.

---

### Task 1: Lock the expanded OpenAPI contract

**Files:**
- Modify: `src/bcs/tests/openapi/test_bot_v1_contract.py:122`
- Modify: `src/bcs/api-contracts/v1/openapi/bots.yaml:1`

**Step 1: Change the contract test first**

Update `test_candidates_contract_matches_legacy_list_semantics` so it requires:

```python
assert operation["x-avernet-behavior"]["acting_bot"] == [
    "managed_physical_bot",
    "current_human_actor",
]
assert operation["x-avernet-behavior"]["result_kind"] == "bot"
assert operation["responses"]["403"]["x-error-codes"] == ["forbidden"]
assert "including Human Actor" in operation["summary"]
```

Also assert that `invalid_bot_kind` is no longer advertised for the candidates
operation, while `invalid_request` remains its `400` code.

**Step 2: Run the focused contract test and verify it fails**

Run:

```bash
uv run --with pytest --with pyyaml \
  pytest src/bcs/tests/openapi/test_bot_v1_contract.py::test_candidates_contract_matches_legacy_list_semantics -q
```

Expected: FAIL because the contract still permits only
`managed_physical_bot` and describes a physical acting Bot.

**Step 3: Update the OpenAPI path item**

In `BotCandidatesPath`:

- keep `operationId: list_bot_candidates` and all Bot terminology;
- update the summary to describe the existing Bot perspective with
  `(including Human Actor)`;
- document that `bot_id` may be either an owned physical Bot or exactly the
  current Human's `human_{subject.id}` record;
- document that results remain physical Bots only;
- change `x-avernet-behavior.acting_bot` to the two allowed values;
- keep purpose, visibility, environment, exclusion, ordering, pagination, and
  response schemas unchanged;
- remove `invalid_bot_kind` from the candidates `400` response description and
  error codes; and
- describe `403` as either an unowned physical Bot or another Human Actor.

**Step 4: Run the focused OpenAPI test**

Run the command from Step 2.

Expected: PASS.

**Step 5: Validate the modular contract**

Run:

```bash
uv run --with pyyaml python src/bcs/scripts/validate_openapi_contract.py \
  --root src/bcs/api-contracts/v1
```

Expected: validation succeeds with the existing operation count; no new path is
introduced.

**Step 6: Commit the contract slice**

```bash
git add src/bcs/api-contracts/v1/openapi/bots.yaml \
  src/bcs/tests/openapi/test_bot_v1_contract.py
git commit -m "feat(bcs): allow human actor bot candidate perspective"
```

### Task 2: Add failing Human Actor application tests

**Files:**
- Modify: `src/bcs/crates/application/v1/bcs-app-bot/tests/v1_bot_service.rs:235`

**Step 1: Replace the obsolete Human-kind rejection assertion**

Keep the physical Bot non-owner assertion. Replace the assertion that
`human_staff-1` returns `invalid_bot_kind` with a successful call by
`human_caller("staff-1")`.

Add a separate Human Actor record for `staff-2`, query it as `staff-1`, and
assert:

```rust
assert_eq!(error.code(), "forbidden");
```

**Step 2: Add a Legacy-compatible collaboration visibility test**

Set up:

```rust
fixture.repo.ensure_human_actor("staff-1", "Human").await?;
fixture.add_bot("private-friend", "staff-2", "private", ActorStatus::Hidden).await;
fixture.add_bot("private-stranger", "staff-3", "private", ActorStatus::Online).await;
fixture.friends.add_friendship("human_staff-1", "private-friend").await?;
```

Call `list_candidates` with `bot_id="human_staff-1"` and
`purpose=BotCandidatePurpose::Collaboration`. Assert the private friend is
returned with `is_friend=true` and the private stranger is absent.

**Step 3: Run the targeted application test and verify it fails**

Run:

```bash
cd src/bcs
cargo test -p bcs-app-bot --test v1_bot_service candidates_
```

Expected: FAIL because `BotServiceImpl::list_candidates` still returns
`invalid_bot_kind` for the Human record.

### Task 3: Generalize the existing candidate authorization branch

**Files:**
- Modify: `src/bcs/crates/application/v1/bcs-app-bot/src/lib.rs:192`
- Test: `src/bcs/crates/application/v1/bcs-app-bot/tests/v1_bot_service.rs`

**Step 1: Implement the minimal authorization change**

After loading the selected record, authorize by persisted kind without adding a
new route, Service API command, Core call, or repository query:

```rust
match acting.kind {
    ActorKind::Bot if acting.created_by.as_deref() == Some(staff_no.as_str()) => {}
    ActorKind::Human if acting.bot_id == format!("human_{staff_no}") => {}
    _ => {
        return Err(ApplicationError::forbidden(format!(
            "Current Human cannot use Bot '{}' as the candidate perspective",
            command.bot_id
        )));
    }
}
```

Keep the existing code below this match unchanged: list friends for
`command.bot_id`, map `purpose`, and call the same
`control_plane.list_candidates` operation using `acting.env`.

**Step 2: Run the targeted tests**

Run:

```bash
cd src/bcs
cargo test -p bcs-app-bot --test v1_bot_service candidates_
```

Expected: all candidate tests pass.

**Step 3: Run the complete application test binary**

Run:

```bash
cd src/bcs
cargo test -p bcs-app-bot --test v1_bot_service
```

Expected: all tests pass, including unchanged physical Bot authorization,
projection, provider hydration ordering, and validation tests.

**Step 4: Commit the implementation slice**

```bash
git add src/bcs/crates/application/v1/bcs-app-bot/src/lib.rs \
  src/bcs/crates/application/v1/bcs-app-bot/tests/v1_bot_service.rs
git commit -m "feat(bcs): support human actor candidate views"
```

### Task 4: Update HTTP forwarding coverage and related documentation

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/bot_routes.rs:277`
- Modify: `src/bcs/api-contracts/README.md:14`
- Modify: `src/bcs/docs/superpowers/specs/2026-08-02-bot-control-plane-v1-implementation-design.md:65`
- Modify: `src/bcs/docs/superpowers/specs/2026-08-02-bcs-v1-human-caller-integration-design.md:217`

**Step 1: Prove the route forwards a Human Actor identifier unchanged**

Change the candidates request in the existing route-forwarding test to:

```text
/openapi/v1/collaboration/bots/human_staff-1/candidates?purpose=collaboration&name=planner&offset=5&limit=10
```

Assert the recorded command contains `bot_id == "human_staff-1"`. This remains
adapter-only coverage; authorization stays in `bcs-app-bot`.

**Step 2: Run the HTTP route test**

Run:

```bash
cd src/bcs
cargo test -p bcs-api-http --test bot_routes all_five_bot_routes_forward_verified_human_and_contract_inputs
```

Expected: PASS without production adapter changes.

**Step 3: Update related documentation**

- In `api-contracts/README.md`, state that the candidates path accepts an owned
  physical Bot or the current Human's Human Actor and still returns only
  physical Bot candidates.
- In the Bot control-plane implementation design, replace the physical-only
  candidate rule with the two permitted Bot perspectives and their respective
  authorization checks.
- In the Human caller integration design, state that `list_bot_candidates`
  accepts the current Human Actor in addition to an owned physical Bot; other
  Human Actor IDs remain forbidden.
- Keep the approved design document
  `src/bcs/docs/plans/2026-08-13-human-actor-bot-candidates-design.md` as the
  detailed rationale.

**Step 4: Commit route coverage and documentation**

```bash
git add src/bcs/crates/adapters/http/bcs-api-http/tests/bot_routes.rs \
  src/bcs/api-contracts/README.md \
  src/bcs/docs/superpowers/specs/2026-08-02-bot-control-plane-v1-implementation-design.md \
  src/bcs/docs/superpowers/specs/2026-08-02-bcs-v1-human-caller-integration-design.md
git commit -m "docs(bcs): document human actor candidate views"
```

### Task 5: Run contract, Rust, and hygiene verification

**Files:**
- Verify all modified files

**Step 1: Run all BCN OpenAPI tests**

```bash
uv run --with pytest --with pyyaml pytest src/bcs/tests/openapi -q
```

Expected: all tests pass.

**Step 2: Validate and bundle the OpenAPI document**

```bash
uv run --with pyyaml python src/bcs/scripts/validate_openapi_contract.py \
  --root src/bcs/api-contracts/v1
uv run --with pyyaml python src/bcs/scripts/bundle_openapi_contract.py \
  --root src/bcs/api-contracts/v1 \
  --output-dir /tmp/bcn-human-actor-candidates-openapi
```

Expected: validation and bundling succeed with no unresolved references.

**Step 3: Run affected Rust test suites**

```bash
cd src/bcs
cargo test -p bcs-app-bot --test v1_bot_service
cargo test -p bcs-api-http --test bot_routes
```

Expected: all tests pass.

**Step 4: Run diff hygiene checks**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors and only intended files are modified. Do not run
global `cargo fmt`; format only touched Rust files if a targeted formatting fix
is necessary.

**Step 5: Commit any verification-only corrections**

If verification required a correction, commit only that correction with an
appropriate `fix(bcs): ...`, `test(bcs): ...`, or `docs(bcs): ...` message.
