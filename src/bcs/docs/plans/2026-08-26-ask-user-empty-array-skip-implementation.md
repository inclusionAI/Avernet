# Ask-User Empty-Array Skip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore `values: []` as the canonical ask-user skip representation without weakening question identity, value typing, or custom-value classification.

**Architecture:** Keep validation and normalization in the transport-agnostic BCS interaction service. Relax only answer cardinality for an empty array; the existing BCS augmentation remains authoritative for `question`, `header`, and generated `customValues`, while BaaS continues to preserve an empty Provider `values` list.

**Tech Stack:** Rust, Tokio, serde_json, BCS Provider 2.0 WebSocket and HTTP contracts, Markdown protocol documentation

**Spec:** `src/bcs/docs/plans/2026-08-26-ask-user-empty-array-skip-design.md`

## Global Constraints

- Every requested `questionId` and every answer's `values` field remain required.
- Only `values: []` represents a skip; empty and whitespace-only string elements remain invalid.
- Single-select/free-text cardinality is zero or one; multi-select cardinality is zero or more.
- Frontend remains values-only; `customValues` remains generated exclusively by BCS.
- Do not run global BCS formatters or change unrelated code.

---

### Task 1: Interaction service regression and minimal validation change

**Files:**
- Modify/Test: `src/bcs/crates/services/bcs-interaction/src/management.rs`

**Interfaces:**
- Consumes: stored Provider ask-user questions and Frontend `{"action":"submit","answers":...}` resolution JSON.
- Produces: the existing `InteractionService::resolve` result and augmented Provider resolution JSON.

- [ ] **Step 1: Write the failing regression test**

Add `ask_user_submit_accepts_empty_array_as_explicit_skip` using the existing real `InteractionManagement` test fixture. Register two requested questions, submit `question_1.values=[]` and `question_2.values=["human-confirmed-delete"]`, then assert resolution acceptance and the exact Provider payload: the skipped answer retains `values: []`, has no `customValues`, and both answers receive authoritative question metadata.

- [ ] **Step 2: Run the test to verify RED**

Run:

```bash
cd src/bcs
cargo test -p bcs-interaction ask_user_submit_accepts_empty_array_as_explicit_skip -- --nocapture
```

Expected: FAIL because the service returns `InvalidRequest("ask_user answer question_1 requires values")`.

- [ ] **Step 3: Implement the minimum validation change**

In `validate_ask_user_resolution`:

- remove the explicit `values.is_empty()` rejection;
- change non-multi-select cardinality from `values.len() != 1` to
  `values.len() > 1`;
- change free-text cardinality from `values.len() != 1` to
  `values.len() > 1`;
- keep the existing per-element non-empty-string validation, the required
  answer map, the required `values` array, and BCS-only `customValues` rule.

- [ ] **Step 4: Run focused service verification**

Run:

```bash
cd src/bcs
cargo test -p bcs-interaction ask_user_submit_accepts_empty_array_as_explicit_skip -- --nocapture
cargo test -p bcs-interaction
```

Expected: the regression and all interaction tests pass.

### Task 2: WebSocket public contract regression

**Files:**
- Modify/Test: `src/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat.rs`

**Interfaces:**
- Consumes: a public WebSocket `interaction.resolve` frame with one empty answer array.
- Produces: a successful response preserving the typed command passed to the interaction service.

- [ ] **Step 1: Write the WebSocket contract test**

Extend the interaction resolve compatibility test with a request containing
`answers.question_1.values=[]` and `answers.question_2.values=["human-confirmed-delete"]`.
Use the existing capturing interaction service and assert the response is
successful and the captured resolution exactly preserves both arrays. This is
a serialization contract; Task 1 provides the service-level RED proof.

- [ ] **Step 2: Run the focused WebSocket contract test**

Run:

```bash
cd src/bcs
cargo test -p bcs-ws --test web_frame_compat interaction_resolve -- --nocapture
```

Expected: all interaction resolve WebSocket compatibility cases pass.

### Task 3: Provider protocol and compatibility contract update

**Files:**
- Modify: `src/bcs/docs/bcs-provider-2.0-sse-protocol.md`
- Modify/Test: `src/bcs/crates/adapters/http/bcs-provider-http/tests/provider_transport_contract.rs`

**Interfaces:**
- Consumes: the canonical augmented BCS Provider resolution.
- Produces: documented and executable Provider 2.0 forwarding semantics.

- [ ] **Step 1: Update protocol examples and constraints**

Document `values: []` as the only skip representation, retain exact
questionId coverage, and state zero-or-one/zero-or-more cardinalities. Clarify
that missing `values`, non-string values, and blank string elements remain
invalid and that an empty answer never creates `customValues`.

- [ ] **Step 2: Add Provider transport coverage**

Add an exact forwarding assertion for an ask-user answer whose `values` is an
empty array, proving the HTTP Provider request preserves it and does not invent
`customValues`.

- [ ] **Step 3: Run Provider contract verification**

Run:

```bash
cd src/bcs
cargo test -p bcs-provider-http --test provider_transport_contract
```

Expected: all Provider transport contract tests pass.

### Task 4: Final verification and review

**Files:**
- Review only the files changed by Tasks 1-3 and the approved design/plan.

**Interfaces:**
- Consumes: complete working-tree diff.
- Produces: verified implementation ready for user review.

- [ ] **Step 1: Run BCS focused verification**

```bash
cd src/bcs
cargo test -p bcs-interaction
cargo test -p bcs-ws --test web_frame_compat interaction_resolve -- --nocapture
cargo test -p bcs-provider-http --test provider_transport_contract
cargo check -p bcs-interaction
```

- [ ] **Step 2: Verify diff hygiene**

```bash
git diff --check
git status --short
```

- [ ] **Step 3: Request independent code review**

Provide the reviewer the approved spec and implementation plan, and require a
read-only review of the working-tree diff for contract consistency, validation
gaps, and test adequacy. Resolve every Critical or Important finding before
completion.
