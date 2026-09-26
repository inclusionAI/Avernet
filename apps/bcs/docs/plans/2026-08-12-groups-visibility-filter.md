# Group List Visibility Filter Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restrict the public Group list membership filter to direct/session-only Groups, default it to direct, and add optional public/private visibility filtering.

**Architecture:** The OpenAPI/HTTP adapter validates wire query values and forwards a typed visibility filter through the existing V1 `ListGroups` Service API command. The application service selects the required relation source, filters before counting and pagination, and retains the internal `All` variant only for non-HTTP callers.

**Tech Stack:** Rust, Axum, Serde, Tokio tests, OpenAPI 3.1 YAML

---

### Task 1: Change the public membership contract

**Files:**
- Modify: `src/bcs/api-contracts/v1/openapi/groups.yaml`
- Modify: `src/gateway/configs/schemas/bcn.openapi.json`
- Modify: `src/bcs/tests/openapi/test_group_v1_contract.py`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/group_routes.rs`

**Step 1: Write failing HTTP boundary tests**

Add tests that issue an otherwise-valid list request without `membership` and
assert the captured command uses `MembershipFilter::Direct`. Add a second
request with `membership=all` and assert `400` plus `invalid_request`.

**Step 2: Run the tests and verify RED**

Run:

```bash
cd src/bcs
cargo test -p bcs-api-http --test group_routes list_groups_defaults_to_direct -- --exact
cargo test -p bcs-api-http --test group_routes list_groups_rejects_removed_all_membership -- --exact
```

Expected: the default assertion observes `All`, and the removed-value request
returns `200` instead of `400`.

**Step 3: Implement the minimal HTTP contract change**

Change the wire enum to:

```rust
pub enum MembershipQuery {
    Direct,
    SessionOnly,
}

impl Default for MembershipQuery {
    fn default() -> Self {
        Self::Direct
    }
}
```

Map only those two variants to the existing application enum. In OpenAPI,
remove `all` from the membership enum and change its default to `direct`.
Regenerate the Gateway schema snapshot with `scripts/dump_openapi.py` after
the contract is complete.

**Step 4: Run the focused tests and verify GREEN**

Run:

```bash
cargo test -p bcs-api-http --test group_routes list_groups_ -- --nocapture
python3 scripts/validate_openapi_contract.py --root api-contracts/v1
uv run --with pytest --with pyyaml pytest \
  tests/openapi/test_group_v1_contract.py -q
python3 scripts/dump_openapi.py --root api-contracts/v1 \
  ../gateway/configs/schemas/bcn.openapi.json
```

Expected: all matching tests pass and the contract validator exits zero.

### Task 2: Add and forward the visibility parameter

**Files:**
- Modify: `src/bcs/api-contracts/v1/openapi/groups.yaml`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/group.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/group.rs`
- Modify: Rust tests/mocks that construct `ListGroups`

**Step 1: Write failing HTTP tests**

Send `visibility=public`, expect `200`, and assert the captured command has
`Some(GroupVisibility::Public)`. Send an invalid visibility value and assert
the common `400 invalid_request` envelope.

**Step 2: Run the tests and verify RED**

Run:

```bash
cargo test -p bcs-api-http --test group_routes list_groups_accepts_visibility -- --exact
```

Expected: the route returns `400` because `visibility` is currently an unknown
query field.

**Step 3: Add typed forwarding**

Add the field to the Service API command:

```rust
pub visibility: Option<GroupVisibility>,
```

Add the same field to `ListGroupsQuery`, pass it through the route, and add
`visibility: None` to existing Service API command literals. Document the
query parameter using `../domain-models.yaml#/GroupVisibility`.

**Step 4: Run the HTTP suite and verify GREEN**

Run:

```bash
cargo test -p bcs-api-http --test group_routes
```

Expected: the full HTTP Group route suite passes.

### Task 3: Filter before total and pagination

**Files:**
- Modify: `src/bcs/crates/application/v1/bcs-app-group/src/lib.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-group/tests/v1_group_service.rs`

**Step 1: Write failing application tests**

Create related public and private Groups and call `list_groups` with a
visibility filter. Assert that only the requested visibility is returned,
`total` counts the filtered set, and pagination applies afterward. Cover both
`MembershipFilter::Direct` and `MembershipFilter::SessionOnly`.

**Step 2: Run the tests and verify RED**

Run:

```bash
cargo test -p bcs-app-group --test v1_group_service list_groups_filters_by_visibility -- --exact
cargo test -p bcs-app-group --test v1_group_service list_session_only_groups_filters_by_visibility -- --exact
```

Expected: both tests return Groups of both visibility values.

**Step 3: Implement minimal relation selection and filtering**

Build the direct relation map once. For `Direct`, use it without loading
Session relations. For `SessionOnly`, load Session Group IDs and omit IDs
present in the direct map. Retain the existing union behavior for internal
`All` callers. Before sorting/counting/pagination, add:

```rust
.filter(|(group, _)| {
    command.visibility.is_none_or(|visibility| {
        group.visibility == visibility_name(visibility)
    })
})
```

**Step 4: Run the focused tests and verify GREEN**

Run:

```bash
cargo test -p bcs-app-group --test v1_group_service list_groups_filters_by_visibility -- --exact
cargo test -p bcs-app-group --test v1_group_service list_session_only_groups_filters_by_visibility -- --exact
```

Expected: both tests pass.

### Task 4: Complete regression verification

**Files:**
- Verify all modified files

**Step 1: Run focused suites and contract validation**

```bash
cd src/bcs
cargo test -p bcs-api-http --test group_routes
cargo test -p bcs-app-group --test v1_group_service
python3 scripts/validate_openapi_contract.py --root api-contracts/v1
cd ../gateway && uv run pytest \
  tests/unit/core/forwarding/test_served_openapi.py::test_served_openapi_aggregates_bcn_with_existing_domains -q
```

Expected: all commands exit zero with no test failures.

**Step 2: Check the patch**

```bash
git diff --check
git status --short
git diff --stat origin/dev...HEAD
```

Expected: no whitespace errors and only the design, plan, contract, HTTP, and
V1 Group application/test files are changed.

**Step 3: Commit the implementation**

```bash
git add src/bcs/docs/plans/2026-08-12-groups-visibility-filter.md \
  src/bcs/api-contracts/v1/openapi/groups.yaml \
  src/bcs/crates/service-api/bcs-service-api/src/application/v1/group.rs \
  src/bcs/crates/adapters/http/bcs-api-http \
  src/bcs/crates/application/v1/bcs-app-group
git commit -m "feat(bcs): filter group lists by visibility"
```
