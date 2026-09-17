# Relay Task Scope Execution

## Summary
In distributed relay mode, every execution holder performs only the atomic task subitems that are within its own concrete runtime scope: its established role, currently mounted Skills, permitted tools, and system instructions. Work outside that scope becomes an explicit task-graph gap and is handed to the next Bot or cooperative group through the existing relay planning, search, and dispatch flow.

The behavior applies to the initial owner Bot, every single-Bot successor, cooperative-group reporter, and a successful BBS claimant. Centralized planning execution remains unchanged.

## Motivation
A relay holder currently receives a broad user or child-task instruction and can use general model knowledge to complete all of it. This makes the holder look successful but bypasses the specialist handoff that relay mode is intended to create. The execution Skill must turn broad user work into scoped local work plus explicit remaining gaps.

## User Stories
- As a user, I want the Bot I first talk to to make progress only within its real scope and hand specialist work to the right next Bot.
- As a relay holder, I want to execute my covered subitems and clearly record uncovered subitems rather than guessing or hard-executing them.
- As a BBS Bot, I want to claim only work I can cover, then continue the relay after I report my actual result.
- As an operator, I want task outputs and graph context to show which parts were completed locally and which parts were deliberately handed off.

## Acceptance Criteria
- [ ] After a human confirms a relay task, the owner Bot receives the task graph context and runs the relay execution Skill, not an unconstrained business-answer flow.
- [ ] Before executing, every relay holder decomposes the current instruction into atomic subitems and executes only subitems covered by its own concrete runtime scope.
- [ ] A holder records completed local scope and uncovered scope separately in its task result.
- [ ] A holder with no covered subitems produces no business deliverable, reports `capability_mismatch`, receives/uses the existing relay turn, and plans a specialist successor.
- [ ] A holder with partial coverage reports only the covered output, then computes the graph gap and plans only remaining uncovered work.
- [ ] Successor single Bots repeat the same scoped-execution behavior before and during their own node execution.
- [ ] Cooperative groups execute only the scoped work assigned to their members; the reporter/master/manager is the sole relay continuation holder after the group result.
- [ ] A BBS Bot checks scope before claiming; after a successful claim it executes covered scope, reports its result, then uses the existing relay continuation flow.
- [ ] When no candidate can be justified for an uncovered gap, the relay Skill reports MISS and uses the existing BBS publication path.
- [ ] Relay stops only on root-gap closure, a successful handoff to the next holder, BBS publication awaiting claim, or the existing iteration-limit/HUNG outcome.
- [ ] Centralized planning execution behavior and APIs remain unchanged.

## In Scope
- Task-loop Relay execution, planning, search, and BBS instructions.
- Relay result payload conventions that reuse existing `EXECUTION_RESULT`, `PLAN_RESULT`, `SEARCH_RESULT`, `MISS`, BBS claim, and relay-turn behavior.
- Relay test scenarios proving scoped partial execution, no-scope handoff, successor continuation, group reporter continuation, and BBS continuation.

## Out of Scope
- New Backend APIs, callback event types, graph states, capability-summary endpoints, or active-Skill query endpoints.
- Backend semantic matching of a task to a Bot.
- Any central-planning execution change.
- A persistent global Bot capability registry.

## Open Questions
- None for the first Skill-only iteration. A future hardening phase may add Backend enforcement if model behavior shows that Skill instructions alone are insufficient.
