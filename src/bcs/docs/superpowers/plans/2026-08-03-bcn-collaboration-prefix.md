# BCN Unified Collaboration Prefix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> to implement this plan task-by-task.

**Goal:** Move all 32 BCN V1 operations below
`/openapi/v1/collaboration/**` while keeping the public contract and Axum
runtime endpoints identical.

**Architecture:** The BCS OpenAPI document remains authoritative. The Axum V1
composition root applies the ownership prefix once with `Router::nest`, and
the Bot, Group, Session, Invitation, and Friendship modules declare paths
relative to that boundary. No Gateway contract or rewrite is added.

**Tech Stack:** OpenAPI 3.1 YAML, Python/pytest contract tests, Rust/Axum,
Cargo.

## Constraints

- Modify only `src/bcs`.
- Keep exactly 32 OpenAPI operations and preserve all operation behavior.
- Do not add compatibility aliases for the previous paths.
- Do not change DTOs, application services, authorization, persistence, or
  production mounting.
- Do not modify Gateway code, add a Gateway contract, or add a rewrite rule.
- Do not run verification commands during this execution, per the requester's
  explicit instruction. Keep the normal commands below for later validation.

### Task 1: Align the authoritative OpenAPI contract

**Files:**

- Modify: `src/bcs/api-contracts/v1/openapi.yaml`
- Modify: `src/bcs/api-contracts/README.md`
- Modify: `src/bcs/tests/openapi/test_contract.py`
- Modify: `src/bcs/tests/openapi/test_bot_v1_contract.py`
- Modify: `src/bcs/tests/openapi/test_group_v1_contract.py`
- Modify: `src/bcs/tests/openapi/test_session_v1_contract.py`

**Steps:**

1. Prefix every path key with `/openapi/v1/collaboration`.
2. Normalize Bot paths to `/collaboration/bots/**` and global Session paths to
   `/collaboration/sessions/**`.
3. Update the exact-operation inventory and add an invariant that every
   operation has the ownership prefix.
4. Update resource-specific contract assertions and the contract README.

Normal validation, intentionally not run in this execution:

```bash
uv run --with pytest --with pyyaml pytest src/bcs/tests/openapi -q
```

### Task 2: Apply one structural prefix to the Axum router

**Files:**

- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/mod.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/bot.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/group.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/session.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/invitation.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/friendship.rs`

**Steps:**

1. Merge the five resource routers and nest them under
   `/openapi/v1/collaboration` in the composition root.
2. Make every resource route relative: `/bots`, `/groups`, `/sessions`,
   `/friend-requests`, and `/invitations`.
3. Leave all handlers and service calls unchanged.

### Task 3: Synchronize HTTP route tests and boundary metadata

**Files:**

- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/bot_routes.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/group_routes.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/session_routes.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/invitation_routes.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/friendship_routes.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/CONTEXT.md`

**Steps:**

1. Point successful route tests at the exact contract paths.
2. Preserve or add negative coverage for representative unprefixed,
   `/bots/collaboration/**`, and `/group-sessions/**` paths.
3. Document that the adapter provides `/openapi/v1/collaboration/**` plus its
   existing internal boundary.

Normal validation, intentionally not run in this execution:

```bash
cargo test --manifest-path src/bcs/Cargo.toml --package bcs-api-http
```

### Task 4: Publish the isolated change

**Steps:**

1. Review only the changed path inventory and diff; do not run tests, lint,
   formatting, or contract validation.
2. Commit the `src/bcs` changes with a conventional BCS commit message.
3. Push the isolated branch to the contributor fork.
4. Open a Draft PR against `inclusionAI/Avernet:dev` with Problem, Solution,
   Validation, Compatibility and risk, and Spec sections.
5. State explicitly that verification was not run at the requester's
   direction.

