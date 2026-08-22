# BCN Ask-User Answer Header Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry the authoritative requested question header through BCS ask-user resolution and require BaaS to use that header, rather than `questionId`, when building the Engine resolution.

**Architecture:** BaaS emits only ask-user requested questions with required headers and independent indexed question IDs. Frontend continues to send values-only answers. BCS canonicalizes each answer from its stored requested payload and forwards trusted `question` plus optional trusted `header`; BaaS applies its stricter Provider contract, requiring `header`, then produces the unchanged Engine request shape using header-based display keys.

**Tech Stack:** Rust 2024, Serde/serde_json, Tokio/Cargo tests, Python 3.12, Pydantic v2, dataclasses, pytest, Ruff.

**Spec:** `src/baas/docs/2026-08-20-bcn-answer-header-design.md`

## Global Constraints

- Do not modify Engine source or Engine wire contracts.
- Do not derive `header` from `questionId`, `question`, or Frontend input.
- BaaS requested conversion must reject missing headers and must not use header
  as question identity; use deterministic source-position IDs instead.
- Keep `header` optional in the global BCN requested protocol.
- Require a non-empty `header` at the BaaS Provider resolve boundary.
- Frontend `interaction.resolve` remains values-only.
- BCS must canonicalize `question/header` before fingerprinting and Provider delivery.
- Duplicate BaaS headers use stable last-write-wins plus a content-free warning.
- Do not run global `cargo fmt`; format only changed Rust files if required.
- Preserve unrelated worktree changes, including the untracked live-verification document.

---

### Task 1: Canonicalize ask-user answer headers in BCS

**Files:**
- Modify: `src/bcs/crates/services/bcs-interaction/src/management.rs`
- Test: `src/bcs/crates/services/bcs-interaction/src/management.rs`

**Interfaces:**
- Consumes: stored `InteractionRecord.requested_payload.questions[]` with required `questionId/question` and optional `header`.
- Produces: canonical `InteractionProviderCommand.resolution.answers[questionId]` containing `values`, stored `question`, and stored `header` only when present and non-empty.

- [ ] **Step 1: Extend the existing augmentation test and add a missing-header test**

Change `ask_user_submit_augments_answers_with_origin_question` so stored
questions contain headers that differ from their IDs, while Frontend answers
contain malicious `question/header` fields:

```rust
{
    "questionId": "target",
    "header": "Deployment environment",
    "question": "Where should this be deployed?",
    "options": [{"value": "staging", "label": "Staging"}]
}
{
    "questionId": "components",
    "header": "Components",
    "question": "Which components?",
    "multiSelect": true,
    "options": [{"value": "web", "label": "Web"}]
}
```

```rust
"answers": {
    "target": {
        "values": ["staging"],
        "question": "frontend question",
        "header": "frontend header"
    },
    "components": {"values": ["web", "worker"]}
}
```

Assert the Provider call contains stored values only:

```rust
assert_eq!(resolution["answers"]["target"]["header"], "Deployment environment");
assert_eq!(
    resolution["answers"]["target"]["question"],
    "Where should this be deployed?"
);
assert_eq!(resolution["answers"]["components"]["header"], "Components");
```

Add `ask_user_submit_omits_header_when_requested_header_is_absent`. Give the
Frontend answer `"header":"untrusted"`, omit stored header, resolve, and
assert:

```rust
assert!(resolution["answers"]["target"].get("header").is_none());
assert_eq!(resolution["answers"]["target"]["question"], "Where?");
```

- [ ] **Step 2: Run focused BCS tests and verify RED**

Run from `src/bcs`:

```bash
cargo test -p bcs-interaction ask_user_submit_augments_answers_with_origin_question -- --exact
cargo test -p bcs-interaction ask_user_submit_omits_header_when_requested_header_is_absent -- --exact
```

Expected: the first test fails because stored header is not added; the second
fails because the Frontend-supplied header survives canonicalization.

- [ ] **Step 3: Implement minimal canonical augmentation**

Inside `augment_ask_user_resolution`, remove any incoming presentation fields
before inserting stored values:

```rust
if let Some(answer) = answers.get_mut(question_id).and_then(Value::as_object_mut) {
    answer.remove("question");
    answer.remove("header");
    answer.insert(
        "question".to_string(),
        Value::String(question_text.to_string()),
    );
    if let Some(header) = question
        .get("header")
        .and_then(Value::as_str)
        .filter(|header| !header.trim().is_empty())
    {
        answer.insert("header".to_string(), Value::String(header.to_string()));
    }
}
```

Update the function comment to state that BCS canonicalizes both fields, never
synthesizes header, and uses the result for fingerprinting and delivery.

- [ ] **Step 4: Run BCS interaction tests and verify GREEN**

```bash
cargo test -p bcs-interaction ask_user_submit_
cargo test -p bcs-interaction
```

Expected: all tests pass.

- [ ] **Step 5: Commit the BCS canonicalization**

```bash
git add src/bcs/crates/services/bcs-interaction/src/management.rs
git commit -m "feat(bcs): forward canonical ask-user headers"
```

### Task 2: Lock the BCS Provider wire contract and documentation

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-provider-http/tests/provider_transport_contract.rs`
- Modify: `src/bcs/docs/bcs-provider-2.0-sse-protocol.md`

**Interfaces:**
- Consumes: canonical resolution produced by Task 1.
- Produces: documented Provider 2.0 `interaction.resolve` body with outer `questionId` keys and inner `header/question/values`.

- [ ] **Step 1: Add a Provider transport contract test**

Add a test that sends an ask-user `InteractionProviderCommand` with:

```rust
kind: InteractionKind::AskUser,
resolution: json!({
    "action": "submit",
    "answers": {
        "deploy_target": {
            "header": "Deployment environment",
            "question": "Where should this be deployed?",
            "values": ["staging"]
        }
    }
}),
```

Assert the captured Provider request preserves all three answer fields and the
outer identity:

```rust
assert_eq!(
    request.body["params"]["answers"]["deploy_target"],
    json!({
        "header": "Deployment environment",
        "question": "Where should this be deployed?",
        "values": ["staging"]
    })
);
```

- [ ] **Step 2: Run the transport contract test**

```bash
cargo test -p bcs-provider-http --test provider_transport_contract interaction_resolve_forwards_ask_user_header -- --exact
```

Expected: PASS if the generic resolution flattening already preserves the
field. This is a characterization test for an unchanged transport adapter; if
it fails, make only the smallest serialization fix in
`src/bcs/crates/adapters/http/bcs-provider-http/src/lib.rs` and rerun.

- [ ] **Step 3: Update the Provider 2.0 protocol document**

In section 6.2:

- keep Frontend submit values-only;
- show BCS-to-Provider answers with `header/question/values`;
- state that `question` and `header` come from stored requested data;
- state that BCS strips same-named Frontend fields;
- state that missing requested header remains absent and is never synthesized;
- state that a specific Provider may impose a stricter required-header
  contract, as BaaS does.

- [ ] **Step 4: Run BCS protocol and transport regression tests**

```bash
cargo test -p bcs-provider-http --test provider_transport_contract
cargo test -p bcs-ws --test web_frame_compat interaction_resolve
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the BCS contract evidence**

```bash
git add src/bcs/crates/adapters/http/bcs-provider-http/tests/provider_transport_contract.rs \
  src/bcs/crates/adapters/http/bcs-provider-http/src/lib.rs \
  src/bcs/docs/bcs-provider-2.0-sse-protocol.md
git commit -m "docs(bcs): define ask-user answer headers"
```

Only add `src/lib.rs` when Step 2 required a production change.

### Task 3: Require and preserve answer header at the BaaS boundary

**Files:**
- Modify: `src/baas/src/secbaas/community/adapters/web/routers/bcn_downlink/bcn_model.py`
- Modify: `src/baas/src/secbaas/community/adapters/web/routers/bcn_downlink/bcn_router.py`
- Modify: `src/baas/src/secbaas/community/api/bcn/_models.py`
- Test: `src/baas/tests/unit/adapters/web/open_api/test_bcn_router.py`

**Interfaces:**
- Consumes: BCS Provider resolve answer `{header, question, values}`.
- Produces: `BcnInteractionAnswer(values: tuple[str, ...], question: str, header: str)`.

- [ ] **Step 1: Write strict request and mapping tests**

Update `_interaction_resolve_body` so each answer has a header different from
its outer question ID:

```python
"deploy_target": {
    "header": "Deployment environment",
    "values": ["staging"],
    "question": "what's your deploy target?",
}
```

Assert parsing and dispatch preserve it:

```python
assert request.params.answers["deploy_target"].header == "Deployment environment"
assert resolve_input.answers["deploy_target"].header == "Deployment environment"
```

Extend invalid-answer cases with:

```python
{"answers": {"question-1": {"values": ["value"], "question": "Question?"}}}
{"answers": {"question-1": {"header": "", "values": ["value"], "question": "Question?"}}}
{"answers": {"question-1": {"header": "   ", "values": ["value"], "question": "Question?"}}}
{"answers": {"question-1": {"header": 7, "values": ["value"], "question": "Question?"}}}
```

- [ ] **Step 2: Run BaaS adapter tests and verify RED**

Run from `src/baas`:

```bash
.venv/bin/pytest tests/unit/adapters/web/open_api/test_bcn_router.py -q
```

Expected: header attribute/mapping assertions fail and missing header is still
accepted.

- [ ] **Step 3: Add the strict typed field**

Add to the Pydantic model:

```python
class InteractionResolveAnswer(BaseModel):
    values: list[str] = Field(..., min_length=1)
    question: str
    header: str

    @field_validator("question", "header")
    @classmethod
    def _text_fields_must_be_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("interaction answer text fields must be non-empty")
        return value
```

Add the required domain field:

```python
@dataclass(slots=True, frozen=True)
class BcnInteractionAnswer:
    values: tuple[str, ...]
    question: str
    header: str
```

Map `header=answer.header` in `_dispatch_interaction_resolve`. Do not add a
default or fallback in any layer.

- [ ] **Step 4: Run BaaS adapter tests and verify GREEN**

```bash
.venv/bin/pytest tests/unit/adapters/web/open_api/test_bcn_router.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the BaaS boundary contract**

```bash
git add src/baas/src/secbaas/community/adapters/web/routers/bcn_downlink \
  src/baas/src/secbaas/community/api/bcn/_models.py \
  src/baas/tests/unit/adapters/web/open_api/test_bcn_router.py
git commit -m "feat(baas): require ask-user answer headers"
```

### Task 4: Normalize BaaS Engine answers from header

**Files:**
- Modify: `src/baas/src/secbaas/community/core/service/bcn/_bcn_service.py`
- Test: `src/baas/tests/unit/core/service/bcn/test_bcn_service.py`

**Interfaces:**
- Consumes: strict `BcnInteractionAnswer.header/question/values` from Task 3.
- Produces: `InteractionResolution` whose summary and `values` use header, whose `answers` use question, and whose `selected_options` preserve answer order.

- [ ] **Step 1: Write header projection and duplicate-header tests**

Change `_make_interaction_resolve_input` answers to include headers that differ
from their IDs. Update the main expected resolution to:

```python
InteractionResolution(
    kind="ask_user",
    decision="submit",
    answer="Deployment environment: staging；Components: web，worker，custom raw value",
    message="Deployment environment: staging；Components: web，worker，custom raw value",
    values={
        "Deployment environment": "staging",
        "Components": "web，worker，custom raw value",
    },
    answers={
        "what's your deploy target?": "staging",
        "whats' the components?": "web，worker，custom raw value",
    },
    selected_options=(("staging",), ("web", "worker", "custom raw value")),
)
```

Add a duplicate-header test with two different question IDs and questions.
Assert both summaries and selected option groups remain ordered, the `values`
entry contains the second answer, and one `duplicate_answer_header` warning is
emitted without header, question, or answer text.

- [ ] **Step 2: Run BaaS normalization tests and verify RED**

```bash
.venv/bin/pytest tests/unit/core/service/bcn/test_bcn_service.py -q
```

Expected: existing output still uses outer question IDs, and no duplicate
header warning exists.

- [ ] **Step 3: Implement header-based normalization**

In `_normalize_interaction_resolution` validate and use source header:

```python
if not source_answer.header.strip() or not source_answer.question.strip():
    raise ValueError("ask_user answer identity must be non-empty")
joined_values = "，".join(source_answer.values)
summaries.append(f"{source_answer.header}: {joined_values}")
if source_answer.header in values:
    logger.warning(
        "Interaction resolution warning: interaction_id=%s "
        "field_path=answers.header error_type=duplicate_answer_header",
        resolve_input.interaction_id,
    )
values[source_answer.header] = joined_values
answers[source_answer.question] = joined_values
selected_options.append(tuple(source_answer.values))
```

Do not log the question ID, header, question text, or selected values.

- [ ] **Step 4: Run BaaS service and Engine-frame regression tests**

```bash
.venv/bin/pytest tests/unit/core/service/bcn/test_bcn_service.py -q
.venv/bin/pytest \
  tests/unit/core/service/bot_run/test_interaction_protocol.py \
  tests/unit/core/service/bot_run/test_bot_websocket_client.py -q
```

Expected: all tests pass and the unchanged Engine builder emits the new
header-based normalized values exactly.

- [ ] **Step 5: Commit the BaaS normalization**

```bash
git add src/baas/src/secbaas/community/core/service/bcn/_bcn_service.py \
  src/baas/tests/unit/core/service/bcn/test_bcn_service.py
git commit -m "feat(baas): normalize ask-user answers by header"
```

### Task 5: Enforce BaaS requested headers and independent question identity

**Files:**
- Modify: `src/baas/src/secbaas/community/core/service/sse/_default_converter.py`
- Test: `src/baas/tests/unit/core/service/sse/test_default_converter.py`
- Modify: `src/baas/docs/2026-08-20-bcn-answer-header-design.md`
- Modify: `src/baas/docs/2026-08-20-bcn-answer-header-implementation.md`

**Interfaces:**
- Consumes: Engine ask-user `questions[]` with required BaaS `header` and
  `question` fields.
- Produces: BCN questions with `questionId=question_<source position>` and a
  separate non-empty header.

- [ ] **Step 1: Add RED tests for required header and independent identity**

Cover missing and whitespace-only header dropping the entire interaction
without consuming seq. Cover two questions with the same header remaining
distinct as `question_1` and `question_2`, with a sanitized duplicate warning.
Also prove an Engine-supplied questionId does not override BaaS identity.

- [ ] **Step 2: Implement the minimal requested conversion change**

Return no questions as soon as a missing/empty header is encountered. Always
derive questionId from the source array position. Track headers only to emit a
content-free duplicate warning; never replace a previously converted question.

- [ ] **Step 3: Run the complete converter regression and static checks**

```bash
cd src/baas
.venv/bin/pytest tests/unit/core/service/sse/test_default_converter.py -q
.venv/bin/ruff check \
  src/secbaas/community/core/service/sse/_default_converter.py \
  tests/unit/core/service/sse/test_default_converter.py
.venv/bin/ruff format --check \
  src/secbaas/community/core/service/sse/_default_converter.py \
  tests/unit/core/service/sse/test_default_converter.py
```

Expected: all commands exit zero.

### Task 6: Cross-module verification and documentation consistency

**Files:**
- Verify: `src/bcs/crates/services/bcs-interaction/src/management.rs`
- Verify: `src/bcs/crates/adapters/http/bcs-provider-http/`
- Verify: `src/baas/src/secbaas/community/adapters/web/routers/bcn_downlink/`
- Verify: `src/baas/src/secbaas/community/core/service/bcn/_bcn_service.py`
- Verify: `src/baas/src/secbaas/community/core/service/sse/_default_converter.py`
- Verify: `src/baas/docs/2026-08-20-bcn-answer-header-design.md`
- Verify: `src/baas/docs/2026-08-20-bcn-answer-header-implementation.md`

**Interfaces:**
- Consumes: all Task 1-5 deliverables.
- Produces: verified BCS-to-BaaS-to-Engine contract evidence with no Engine modification.

- [ ] **Step 1: Run complete affected BCS tests**

```bash
cd src/bcs
cargo test -p bcs-interaction
cargo test -p bcs-provider-http
cargo test -p bcs-ws --test web_frame_compat interaction_resolve
```

Expected: all tests pass.

- [ ] **Step 2: Run complete affected BaaS tests**

```bash
cd src/baas
.venv/bin/pytest \
  tests/unit/adapters/web/open_api/test_bcn_router.py \
  tests/unit/core/service/bcn/test_bcn_service.py \
  tests/unit/core/service/bot_run/test_interaction_protocol.py \
  tests/unit/core/service/bot_run/test_bot_websocket_client.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Run changed-file static checks**

```bash
cd src/baas
.venv/bin/ruff check \
  src/secbaas/community/adapters/web/routers/bcn_downlink/bcn_model.py \
  src/secbaas/community/adapters/web/routers/bcn_downlink/bcn_router.py \
  src/secbaas/community/api/bcn/_models.py \
  src/secbaas/community/core/service/bcn/_bcn_service.py \
  tests/unit/adapters/web/open_api/test_bcn_router.py \
  tests/unit/core/service/bcn/test_bcn_service.py
.venv/bin/ruff format --check \
  src/secbaas/community/adapters/web/routers/bcn_downlink/bcn_model.py \
  src/secbaas/community/adapters/web/routers/bcn_downlink/bcn_router.py \
  src/secbaas/community/api/bcn/_models.py \
  src/secbaas/community/core/service/bcn/_bcn_service.py \
  tests/unit/adapters/web/open_api/test_bcn_router.py \
  tests/unit/core/service/bcn/test_bcn_service.py
```

Expected: both commands exit zero.

- [ ] **Step 4: Audit the final contract and diff**

```bash
rg -n 'questionId.*header|header.*questionId|fallback' \
  src/bcs/crates/services/bcs-interaction/src/management.rs \
  src/bcs/docs/bcs-provider-2.0-sse-protocol.md \
  src/baas/src/secbaas/community/adapters/web/routers/bcn_downlink \
  src/baas/src/secbaas/community/core/service/bcn/_bcn_service.py
git diff --check
git status --short
```

Confirm there is no production fallback, no Engine file change, and no
unrelated file staged or committed.

- [ ] **Step 5: Commit final documentation-only corrections if required**

If verification exposed wording drift, update only the two answer-header docs
and the BCS Provider protocol, then commit:

```bash
git add src/baas/docs/2026-08-20-bcn-answer-header-design.md \
  src/baas/docs/2026-08-20-bcn-answer-header-implementation.md \
  src/bcs/docs/bcs-provider-2.0-sse-protocol.md
git commit -m "docs(baas): align answer header contract"
```

If no correction is required, do not create an empty commit.
