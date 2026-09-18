# Tasks: Relay Task Scope Execution

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: Add Relay local-scope gate
- **Goal:** Make every relay holder decide which atomic subitems are inside its own runtime scope before it executes.
- **Files:** `/Users/shangjian.msj/Github/aiworkbench/skills/infra/task-loop/SKILL.md`, `references/distributed-relay-protocol.md`
- **Done when:**
  - [x] Relay routing makes local-scope assessment the first holder action.
  - [x] Skill forbids executing uncertain or out-of-scope subitems.
  - [x] Skill distinguishes full coverage, partial coverage, and zero coverage.
- **Depends on:** —

## Task 2: Define scoped execution-result and gap conventions
- **Goal:** Reuse existing relay events to record locally completed work and explicitly hand off unexecuted work.
- **Files:** `/Users/shangjian.msj/Github/aiworkbench/skills/infra/task-loop/references/distributed-relay-protocol.md`
- **Done when:**
  - [x] Partial coverage uses `success=true` with `completed_scope` and `uncovered_scope`.
  - [x] Zero coverage uses existing `success=false`, `exec_error=capability_mismatch`, and no business deliverable.
  - [x] Follow-up `PLAN_RESULT` creates one specialist successor only for uncovered scope.
- **Depends on:** Task 1

## Task 3: Add conservative Relay search and self-loop avoidance
- **Goal:** Prevent Relay from selecting arbitrary candidates or immediately returning work to a declined Bot.
- **Files:** `/Users/shangjian.msj/Github/aiworkbench/skills/infra/task-loop/SKILL.md`, `references/distributed-relay-protocol.md`
- **Done when:**
  - [x] Relay search overrides the generic candidate-count shortcut.
  - [x] Next-node context carries required capabilities and excluded Bot IDs.
  - [x] Search uses explicit available evidence or returns `MISS(no_capability_match)`.
  - [x] Every selected successor repeats the local-scope gate.
- **Depends on:** Task 2

## Task 4: Align BBS claim and continuation behavior
- **Goal:** Make BBS voluntary claim scope-aware and preserve the same relay continuation loop after BBS execution.
- **Files:** `/Users/shangjian.msj/Github/aiworkbench/skills/infra/task-loop/references/distributed-relay-protocol.md`
- **Done when:**
  - [x] BBS Bot checks that it has at least partial scope before claim.
  - [x] Out-of-scope BBS Bot does not claim.
  - [x] Successful claimant reports scoped result, gets the existing relay turn, and plans/searches/dispatches remaining gap.
- **Depends on:** Tasks 1, 2

## Task 5: Validate real relay behavior without Backend changes
- **Goal:** Prove the live Skill produces bounded local work and actual handoffs.
- **Files:** live relay test evidence / Skill validation artifacts
- **Current validation (2026-09-17):**
  - [x] Ran `python3 skills/skill-validator/scripts/cli.py ci -s skills/infra/task-loop` in the live Skill repository: six static gates passed (structure, syntax, security, quality, compatibility, and deduplication).
  - [x] Ran `git diff --check` in the live Skill repository: passed.
  - [~] Runtime validation is pending an environment that can invoke relay Bots and inspect their graph/callback events. Static validation cannot prove an LLM obeys the local-scope gate in a real execution.
- **Done when:**
  - [ ] Root partial-scope, zero-scope, successor, MISS/BBS, and BBS continuation scenarios are exercised.
  - [ ] No scenario changes centralized mode behavior or requires new Backend API fields.
  - [ ] Any observed hard-execution violation is documented as input to a separate Backend-hardening decision.
- **Depends on:** Tasks 1–4

---

## Groups

- **Group A — Scope and result contract:** Tasks 1, 2
  - Theme: Prevent each relay holder from treating a broad user task as unrestricted personal work.
- **Group B — Safe handoff behavior:** Tasks 3, 4
  - Theme: Ensure uncovered work reaches a justified successor or BBS without self-loops.
- **Group C — Runtime validation:** Task 5
  - Theme: Verify actual multi-holder relay behavior before considering any Backend hardening.
