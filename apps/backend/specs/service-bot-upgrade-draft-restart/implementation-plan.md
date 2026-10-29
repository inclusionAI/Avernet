# Draft restart on publication upgrade implementation plan

> **For agentic workers:** Execute natively in the assigned worktree with test-driven-development.

**Goal:** Submit a draft restart once when upgrading a successful publication creates its successor.
**Architecture:** Keep orchestration in the publication service and reuse ordinary Bot lifecycle. HTTP maps a retained-draft failure; the OpenAPI facade uses its existing upstream error contract.
**Tech Stack:** Python, pytest, FastAPI.
**Spec:** 001-spec-output.md

## Global constraints
- Only new successors trigger restart; retries return the existing successor.
- Resolve source Bot by immutable PK and use its current owner; Teclaw skips restart.
- Keep draft on restart failure; no health waiting or new persistence state machine.
- No OCB worktree/gitlink, deployment, push or commit by the coding agent.

## Review focus
- Entity differs from owner; current owner may have changed.
- Existing successor must not trigger a second restart.
- Explicit failed result and exceptions must not report success.
- Source identity changes must not target another Bot.
- Credential values in returned Bot or exception must not enter new logs.

## Task 1: Publication service
- [x] Add failure-first tests for fresh draft restart, two idempotent paths, Teclaw, current owner, missing/mismatched source, failed submit, and logs.
- [x] Run tests and observe missing restart behavior.
- [x] Add local restart helper and retained-draft exception, reusing existing lifecycle.
- [x] Run publication service tests and existing restart tests.

## Task 2: HTTP compatibility
- [x] Add failure-first legacy API mapping and OpenAPI facade mapping tests.
- [x] Add safe request/result logs and retained-draft error mapping without moving orchestration into adapters.
- [x] Document the Service API side effect and recovery; run router/facade tests, lint and diff check.
- [x] Produce 002-code-report.md with commands and observed results for independent review.
