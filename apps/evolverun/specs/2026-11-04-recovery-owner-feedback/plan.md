# Recovery Owner feedback and handbook implementation

Base: fresh origin/dev in independent Avernet and OCB worktrees. Branch: dev_recovery_owner_feedback_20261104. The user has approved development and formal PRs. No production config, template publication, business notification or deployment is included.

## Contract and boundaries

Permission and required-material requests extend Owner handoff into a nonterminal waiting_owner branch. Other handoffs remain terminal. Owner feedback acknowledges processing, never proves recovery, approves a plan, or supplies business confirmation. Reuse ce_tasks/ce_steps, versioned task JSON, tool audit, existing execution tickets/start CAS, release and maintenance. No new table or credentials.

Avernet owns state, shared contracts, standard card transport, common routing and CW UI. OCB owns internal authenticated callback assembly and recovery Skill/runner/handbook assets. Preserve existing plan approval, frozen target and routing, user continuation, and uncertain-write reconciliation.

## Tasks and verification

1. **Task state and feedback API** — extend task-contracts.ts/contracts.ts; add focused owner-cooperation.ts transitions and task-service integration. Test pending request, exact current request/version, trusted Owner/environment, duplicate clicks, stale/terminal cards, persistence failure, live and expired executions, restart maintenance. Waiting is not an upstream final result. Verify with Vitest owner-cooperation/task-state/task-service suites. Start by demonstrating failing behavioral tests.
2. **Execution and worker protocol** — agent-service.ts plus OCB recovery_ais runner/MCP/output schema. Feedback re-enters assessment while retaining plan/history and previous phase; require fresh original-session observation before continuation. Use request-specific deterministic delivery identity, no duplicate/replayed initial message and no receipt overwrite. Partial completion reuses the card with a new request revision. Test real runner handling and message idempotency/old execution fencing.
3. **Notification and callback adapters** — extend existing session_recovery_owner_notifications config with published template_id and callback_route_key scoped to CW control plane. Reuse DingTalkNotificationSender credentials/token cache and standard createAndDeliver/update APIs. Persist stable card identity and notification states; never claim a failed/unknown send succeeded. OCB callback validates server-verified identity, action and request identity, durably saves before fast ack and async resume. Test local HTTP server transport and auth rejection paths. Prepare template parameters and configuration instructions without sending real notifications.
4. **CW UI** — show pending action, exact missing items, notification/feedback/verification state and authenticated confirmation fallback. Keep plan approval separate. Test page interaction and stale/failed API behavior.
5. **Skill and handbooks** — main Skill global Owner modes and candidate discovery; common MCP/entry text remove unconditional terminal contradiction; file-download adds real allowed media directory copy/read; new common/network uses explicit egress denial, nodes URL example only, pure handoff; custom/97 adds actual three review Skills task reasoning, missing-material collaboration, and maga current progress/waiting. Preserve existing maga missing-knowledge exclusion. No case-specific scoring/identity rule. Verify package/runtime tests and manually review semantic boundaries.
6. **Routing and rollout** — network candidates for TC.MCP/TOOL/SKILL/MODEL; prepare 97 config expansion to SKILL/MCP/MODEL/TOOL/WORKFLOW_PLANNER without production writes. Preserve frozen existing tasks. Ship handbook package before enabling routes. Test candidate existence and matched-team isolation.
7. **Integration and delivery** — run affected suites, TypeScript checks/build, Python runner tests, local card/callback/UI integration, repository gates and source-size check. Review spec then code quality. Commit/push to correct upstreams, open formal English PRs targeting dev, continuously follow CI and fix authorized failures. Describe external template/route prerequisites and any mirror/pin sequencing accurately.

## Progress

- [x] Design and scope approved; fresh isolated dev branches created.
- [x] Baseline and failing new tests.
- [x] State, execution, notification, callback and UI; review corrections verified.
- [x] Skills, handbooks and routing; deployment/config prerequisites prepared without remote writes.
- [ ] Integration, review, formal PRs and completed CI.

## Verified candidate

ClawInsight full suite: 1,582 tests passed. Shared Skill/runtime package: 664 tests passed, one explicit skip and 346 subtests. Gateway configuration: 23 tests passed. OCB recovery/callback: 19 tests passed. UI: 66 unit/integration tests and 9 Playwright checks on desktop/mobile; no runtime/console errors. Two independent review axes completed; reported races, projections, CAS idempotency, capability closeout propagation and fresh-read bypasses fixed with regressions. Local composition build and public/internal checks passed. Live platform card/template/route setup remains an external rollout prerequisite.
