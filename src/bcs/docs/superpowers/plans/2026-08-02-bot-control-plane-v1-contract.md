# Bot Control-plane OpenAPI V1 Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the approved Bot domain model and five Human control-plane operations to the modular BCN OpenAPI V1 contract, without implementing Rust handlers.

**Architecture:** Keep `api-contracts/v1/openapi.yaml` as the public entrypoint. Define reusable Bot response models in `domain-models.yaml`, resource-specific requests and path items in a new `openapi/bots.yaml`, and the `bot_id` path parameter in `shared.yaml`. Contract tests lock down the operation inventory, Human-principal boundary, model variants, mutable fields, filters, ordering, and legacy-compatible query semantics.

**Tech Stack:** OpenAPI 3.1 YAML, JSON Schema composition, Python 3, PyYAML, pytest.

**Approved scope:**

- `GET /openapi/v1/bots/{bot_id}/candidates`
- `POST /openapi/v1/bots/query`
- `GET /openapi/v1/bots/{bot_id}`
- `PATCH /openapi/v1/bots/{bot_id}`
- `GET /openapi/v1/bots/mine`

The batch deliberately excludes generic `GET /bots`, `/actors/**`, `GET /bots/discover`, and a separate descriptor patch endpoint.

---

### Task 1: Lock the approved operation and security surface

**Files:**

- Modify: `src/bcs/tests/openapi/test_contract.py`
- Create: `src/bcs/tests/openapi/test_bot_v1_contract.py`

- [x] Add the five Bot operations to the exact public operation inventory.
- [x] Assert every Bot management operation declares `x-avernet-security.principal: human`.
- [x] Assert candidates use path `bot_id`, not `acting_bot_id` query input.
- [x] Assert `kind` accepts only `bot|human` and omission means all kinds.
- [x] Run the OpenAPI tests and confirm they fail before the contract is added.

Run:

```bash
cd src/bcs
python -m pytest tests/openapi -q
```

Expected: FAIL because the five Bot paths and schemas are not yet present.

---

### Task 2: Define the Bot domain model

**Files:**

- Modify: `src/bcs/api-contracts/v1/domain-models.yaml`

- [x] Define common enums for `kind`, `visibility`, raw `status`, and computed `reachability`.
- [x] Define `BotDescriptor`, descriptor skills, and optional provider identity without a provider slug.
- [x] Define a discriminated `Bot` union:
  - Physical Bot requires `descriptor` and `reachability`; `provider` and `agent_code` are optional.
  - Human Bot has only common fields and cannot carry physical-Bot fields.
- [x] Keep `env` required, `created_by` optional, and expose `created_at`/`updated_at` as Unix milliseconds.
- [x] Define `BotCandidate`, Bot pages, query result, and success envelopes.

---

### Task 3: Define the five Bot resource contracts

**Files:**

- Create: `src/bcs/api-contracts/v1/openapi/bots.yaml`
- Modify: `src/bcs/api-contracts/v1/shared.yaml`
- Modify: `src/bcs/api-contracts/v1/openapi.yaml`

- [x] Add reusable `BotIdPath` named `bot_id` without changing legacy `BotUuidPath`.
- [x] Define candidates behavior:
  - acting `bot_id` must be a physical Bot managed by the current Human;
  - `purpose=discovery|collaboration`, default `discovery`;
  - optional trimmed case-insensitive `name`, `offset=0`, `limit=20`, maximum 100;
  - same environment, non-deleted physical Bots only, excluding self;
  - discovery is `public|protected`; collaboration is `public|friend`;
  - no `status` or `reachability` filtering;
  - order by `created_at DESC, bot_id ASC`.
- [x] Define batch query behavior:
  - request `{bot_ids: [...]}`, maximum 100, duplicates allowed;
  - preserve first-occurrence input order and de-duplicate;
  - return both kinds regardless of ownership or visibility;
  - silently omit missing, deleted, and unonboarded rows; allow an empty request.
- [x] Define exact-ID read for both kinds with no acting Bot parameter or visibility filter.
- [x] Define owner-only patch with at least one of `name`, `visibility`, `status`, or partial `descriptor`; descriptor arrays replace the complete array and descriptor is valid only for physical Bots.
- [x] Define `mine` filters for optional `kind`, `name`, `status`, `reachability`, offset, and limit; omission of kind means both kinds and reachability only matches physical Bots.
- [x] Reference all paths and public schemas from the root OpenAPI document.

---

### Task 4: Document and verify the modular contract

**Files:**

- Modify: `src/bcs/api-contracts/README.md`

- [x] Document the Bot control-plane batch and validation commands.
- [x] Run the custom contract validator.
- [x] Run all OpenAPI tests.
- [x] Bundle twice and confirm deterministic output plus resolvable discriminator mappings.
- [x] Review the diff for accidental legacy Actor/API changes and credential fields.

Run:

```bash
cd src/bcs
python scripts/validate_openapi_contract.py --root api-contracts/v1
python -m pytest tests/openapi -q
python scripts/bundle_openapi_contract.py \
  --root api-contracts/v1 \
  --output-dir /tmp/bcn-bot-v1-contract
```

Expected: validator reports 32 operations; all OpenAPI tests pass; bundling succeeds without unresolved references.
