# Tasks: Gateway — inbound A2A server

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: Apply the approved contract edits
- **Goal:** `GatewayError::NotCancelable`, `wait_task`, one admission success, version 7.0.0.
- **Files:** `crates/service-api/bcs-service-api/{Cargo.toml,CONTEXT.md,src/application/dist_gateway.rs,src/core/dist_gateway.rs,src/port/repo/distributed/{inbox.rs,README.md}}`, `crates/test-support/bcs-test-support/src/contract/distributed/`, `crates/services/bcs-dist-store/{src/inbox/producer.rs,tests/}`
- **Done when:**
  - [x] no `Duplicate` admission outcome is left in the tree
  - [x] `bcs-service-api`, `bcs-test-support`, `bcs-dist-store` build and their tests pass
- **Depends on:** —

## Task 2: Shared store: task index key, index write, `ev` codec
- **Goal:** the four shared changes of research §4.
- **Files:** `crates/services/bcs-dist-store/src/{layout.rs,codec/ev.rs,codec/mod.rs,inbox/producer.rs}`, `tests/{admission.rs,building_blocks.rs}`
- **Done when:**
  - [x] a new task is listed once in `…:tasks:<sid>`; a retry repairs a lost index write
  - [x] an `ev` entry round-trips and its stored fields are pinned by a test
- **Depends on:** Task 1

## Task 3: Gateway store crate: task reader, session readers, spec read
- **Goal:** `TaskReaderPort` and the read-only `SessionStorePort` over `bcs-store-api`.
- **Files:** `crates/services/bcs-dist-gateway-store/` (new), `apps/bcs/Cargo.toml`
- **Done when:**
  - [x] restore / validate_resume / follow / list tests pass over the store model
  - [x] subscribers of one session share one blocking read; a slow one is closed alone
  - [x] Worker operations on the Gateway's session store are refused
- **Depends on:** Task 2

## Task 4: Core service
- **Goal:** `DistGatewayCoreService`: scope, transition and reference rules over the ports.
- **Files:** `crates/services/bcs-dist-gateway/` (new)
- **Done when:**
  - [x] every row of the plan's admission-check table has a test
  - [x] the core contract harness passes
- **Depends on:** Task 3

## Task 5: Application service
- **Goal:** `DistGatewayService`: identity, routing, admission, query, subscribe, wait, cancel, list.
- **Files:** `crates/application/bcs-app-dist-gateway/` (new)
- **Done when:**
  - [x] the same message selects the same session, task and input; changed content conflicts
  - [x] snapshot-first and resume streams, wait, cancel and list tests pass
  - [x] the application contract harness passes
- **Depends on:** Task 4

## Task 6: A2A JSON-RPC + SSE adapter
- **Goal:** the six A2A 1.0 methods, resume ids, agent card, error mapping.
- **Files:** `crates/adapters/http/bcs-dist-gateway-http/` (new)
- **Done when:**
  - [x] cursor, error-mapping, SSE and push-notification tests pass
  - [x] an end-to-end test runs send, stream, reply and cancel through all layers over the store model
  - [x] a test asserts `wake` is written on every input while D1 is stubbed
- **Depends on:** Task 5

## Task 7: Verification
- **Goal:** the spec's acceptance criteria hold and the repo gates pass.
- **Done when:**
  - [x] every acceptance criterion in `spec.md` is covered by a passing test
  - [x] the architecture checks report nothing for the new crates (their remaining failures exist on `dev`); no file over 1,000 lines; no formatter-only changes
- **Depends on:** Tasks 1–6

---

## Groups

- **Group A — Contracts and shared store:** Tasks 1, 2
  - Theme: the merged contracts and the shared store carry what the Gateway needs.
- **Group B — Store and core:** Tasks 3, 4
  - Theme: tasks can be read, followed and listed, and inputs admitted under the rules.
- **Group C — Application and adapter:** Tasks 5, 6
  - Theme: a client can use the Gateway over A2A.
- **Group D — Verification:** Task 7
  - Theme: final acceptance check.
