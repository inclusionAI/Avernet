# Plan: Relay Task Scope Execution

## Approach
Modify the actual `task-loop` Skill so relay execution is a repeated **scope → execute → report → gap → handoff** loop. Every holder is allowed to execute only the atomic subitems that it can justify from its own runtime identity and mounted Skills. It must turn every unexecuted item into explicit `uncovered_scope`, then use the already-existing relay API sequence to create and hand off the next specialist node.

No Avernet Backend code changes are planned for this iteration. Existing relay contracts already provide the necessary result report, relay turn, planning, candidate catalog, selection, dispatch, BBS publication, BBS claim, and iteration-limit semantics.

## Actual Skill Sources
- `/Users/shangjian.msj/Github/aiworkbench/skills/infra/task-loop/SKILL.md` — relay routing and top-level execution rules.
- `/Users/shangjian.msj/Github/aiworkbench/skills/infra/task-loop/references/distributed-relay-protocol.md` — relay event payloads and the holder-to-holder protocol.

The historical copy under `src/backend/specs/2026-08-23-task-loop-skill/` is not the runtime source and will not be edited for behavior.

## Confirmed Execution Flow

```text
1. Human → main Bot: submit task requirement.
2. Main Bot → task recognition Skill: collect/validate four task elements and show a clarification card.
3. Human → main Bot: clarify missing task information.
4. Human → main Bot: confirm task; platform calls the existing system execute API and records task metadata/graph.
5. Main Bot → relay execution Skill:
   - read current task requirement and graph context;
   - split work into atomic subitems;
   - execute only subitems within its own runtime scope;
   - validate and report completed scope plus uncovered scope through the existing system callback API.
6. Main Bot → relay planning/search/dispatch:
   - read latest graph;
   - calculate root gap;
   - create one next specialist task for uncovered work;
   - obtain catalog; select Bot/group mode; dispatch through existing Runner.
7. Next Bot or group reporter repeats steps 5–6 until work is handed off, the root gap closes, or no direct candidate matches.
8. BBS Bot prechecks scope, claims only a matching BBS task, then executes/validates/reports its scoped result.
9. The BBS claimant becomes the relay holder: read latest graph → compute gap → plan → search → dispatch the next holder. Stop only on completion, confirmed handoff/BBS publication, or existing iteration limit/HUNG.
```

## Skill Contract Changes

### A. Add a mandatory relay scope gate

```diff
# /Users/shangjian.msj/Github/aiworkbench/skills/infra/task-loop/SKILL.md
-| Relay: current Bot executes and reports, calculates gap, plans next node...
+| Relay: current Bot first determines its local executable scope, executes
+| only matching atomic subitems, reports covered/uncovered scope, then
+| calculates graph gap and hands off remaining work.
```

The Relay section receives this rule:

```text
You are not a general task substitute. A task assigned to you grants the
current relay context, not permission to complete every user request.

Before executing, split the node instruction into atomic subitems. Execute
only subitems that are clearly covered by your existing system role, mounted
Skills, permitted tools, and instructions. If coverage is uncertain, treat the
item as uncovered. Do not use generic model knowledge to hard-complete an item
outside that scope.
```

### B. Use scoped results rather than whole-task results

```jsonc
// Existing EXECUTION_RESULT event, partial local coverage
{
  "event_type": "EXECUTION_RESULT",
  "payload": {
    "success": true,
    "output": {
      "completed_scope": [
        {
          "subtask": "完成市场分析",
          "result": "...",
          "evidence": "当前 Bot 的研究流程和已挂载研究 Skill"
        }
      ],
      "uncovered_scope": [
        {
          "subtask": "合同合规审查",
          "required_capability": "legal-compliance-review",
          "reason": "不属于当前 Bot 的运行时职责或已挂载 Skill 范围"
        }
      ]
    }
  }
}
```

A `success=true` means the Bot truthfully completed its own local node scope. It does **not** mean the root task is complete. The next `PLAN_RESULT` must calculate the graph gap from all outputs and plan only remaining uncovered work.

```jsonc
// Existing EXECUTION_RESULT event, no local coverage
{
  "event_type": "EXECUTION_RESULT",
  "progress_reason": "当前 Bot 没有可安全执行的子项，转入专业接力规划",
  "failure_reason": "capability_mismatch",
  "payload": {
    "success": false,
    "exec_error": "capability_mismatch",
    "output": {
      "execution_decision": "DECLINE",
      "completed_scope": [],
      "uncovered_scope": [
        {
          "subtask": "法律合规审查",
          "required_capability": "legal-compliance-review",
          "reason": "当前 Bot 不具备可证明的执行范围"
        }
      ]
    }
  }
}
```

The existing relay protocol already permits `success=false` with `exec_error` and lets the current holder plan a remedy after it receives its relay turn. The Skill must use this only for **zero coverage**, not for partial success.

### C. Restrict Relay planning to the uncovered gap

```text
When completed_scope is non-empty:
- preserve it as evidence in the graph;
- do not repeat or replace it;
- PLAN_RESULT creates exactly one next node for the highest-priority uncovered gap.

When completed_scope is empty:
- PLAN_RESULT must have has_gap=true and exactly one specialist successor;
- never claim has_gap=false;
- never fabricate a local business result.
```

The next node instruction must name the required scope explicitly:

```jsonc
{
  "metadata": {
    "title": "合同合规审查",
    "instruction": "仅执行合同条款、合规风险和审查意见。不要重做已完成的市场分析。"
  },
  "context": {
    "background": "已完成范围：市场分析。未覆盖范围：合同合规审查。",
    "extend_props": {
      "required_capabilities": ["legal-compliance-review"],
      "excluded_bot_ids": ["当前拒绝执行的 Bot ID"]
    }
  }
}
```

### D. Replace Relay candidate-count routing with conservative selection

The generic `task-search` section's current validation-only rule selects `catalog[0]` for one or two candidates. It must **not** be used as the decision rule within the Relay segment.

```text
Relay selection rule:
1. Read required_capabilities and uncovered_scope from the next-node context.
2. Exclude excluded_bot_ids from consideration.
3. Use only candidate information available in the supplied catalog and current
   task context. Do not invent candidate capabilities.
4. Select HIT_SINGLE only with an explicit match reason.
5. Select HIT_MULTI_BOTS only when the uncovered gap requires complementary
   scopes and each member is assigned a concrete scope.
6. If no candidate can be justified, return MISS(no_capability_match).
7. A newly selected Bot repeats the same scope gate, so selection is advisory,
   not permission to hard-execute.
```

This is intentionally conservative. Without a capability-summary API, the Skill cannot prove that another Bot is an ideal match. It can prevent the current Bot from hard-executing and use the next holder's own scope gate as the final protection.

### E. BBS behavior

```text
Before BBS claim:
- read the published BBS task and determine whether at least one subitem is in scope;
- if none is in scope, do not claim;
- if scope exists, claim and execute only that scope.

After BBS execution:
- report EXECUTION_RESULT through the existing relay callback;
- obtain the existing relay_turn;
- use the same scoped gap/planning/search/dispatch loop as every other relay holder.
```

A BBS claimant is voluntary selection, so no new Backend admission event is required. Claiming a task represents the Bot's assertion that it has at least partial local scope; the Skill still forbids it from hard-completing uncovered parts.

## Files to Change

```text
/Users/shangjian.msj/Github/aiworkbench/skills/infra/task-loop/SKILL.md
/Users/shangjian.msj/Github/aiworkbench/skills/infra/task-loop/references/distributed-relay-protocol.md
```

No files in `/Users/shangjian.msj/Github/Avernet/src/backend/src/` are changed in the first iteration.

## Risks & Mitigations
- **Risk:** A Bot does not have an explicit role or Skill list in its runtime context.  
  **Mitigation:** The Skill uses a conservative rule: uncertain coverage is uncovered. It creates a handoff gap rather than making a broad claim of competence.
- **Risk:** The candidate catalog lacks enough evidence to identify a specialist.  
  **Mitigation:** Return `MISS(no_capability_match)` and use the existing BBS path; never choose `catalog[0]` merely because it exists.
- **Risk:** A model ignores the scope instruction and emits a full answer.  
  **Mitigation:** The Skill requires explicit `completed_scope` and `uncovered_scope`; test prompts should reject outputs that claim work outside the declared local scope. Backend enforcement is a later hardening phase if needed.
- **Risk:** Partial outputs are mistaken for task completion.  
  **Mitigation:** Relay planning always calculates the root gap after every result; only `PLAN_RESULT(has_gap=false)` closes the task.
- **Risk:** BBS Bot occupies a task it cannot perform.  
  **Mitigation:** Add scope screening before claim; do not claim when no subitem is covered.

## Alternatives Considered
- **Backend capability/admission state machine.** Deferred. It provides stronger enforcement but requires new state, callback, Runner, and root-injection behavior.
- **Keep full-task execution and only change search.** Rejected. The current holder can still finish specialist work before search runs.
- **Require external capability-summary APIs first.** Rejected for phase one. Use conservative self-scope instructions now; add platform facts later if available.

## Rollout

```text
1. Update the live task-loop Skill and relay protocol reference.
2. Validate relay root partial-scope behavior in a controlled task.
3. Validate successor, BBS, and MISS paths.
4. Enable/keep the existing relay execution setting only after the Skill package is mounted.
5. Observe outputs for hard-execution violations before deciding whether Backend hardening is needed.
```

## Test Strategy

```text
Skill scenario 1: Market Bot receives market + legal + technical task.
Expected: executes market only; legal/technical become uncovered gap.

Skill scenario 2: Legal Bot receives technical implementation task.
Expected: no business output; capability_mismatch; plans/dispatches technical successor.

Skill scenario 3: Successor Bot receives a task with partly matching scope.
Expected: executes only its covered subitems, preserves prior output, plans next gap.

Skill scenario 4: No catalog candidate can be justified.
Expected: SEARCH_RESULT MISS(no_capability_match), BBS publication.

Skill scenario 5: BBS Bot sees an out-of-scope task.
Expected: does not claim.

Skill scenario 6: BBS claimant completes partial scope.
Expected: reports result, receives relay turn, plans/dispatches uncovered successor.
```
