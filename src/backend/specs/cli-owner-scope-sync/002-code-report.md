---
agent: tc-code
status: completed
created: 2026-09-20
iteration: 1
---

# CLI owner synchronization implementation

## Scope and boundaries

Existing flow: identity service → sparse repository mutation → shared CLI Passport reconciler → Passport plugin full-scope update. Production changes are limited to carrying the authorized request-local intent and overlaying it in the existing snapshot builder. Routers, repository persistence, locks, engine fencing, last-caller protection, compensation, global merge defaults, and deployment are unchanged.

## Implementation and files

- `src/agentclaw/community/core/caller_identity/service.py`: forwards `{cli_code: normalized_call_type.value}` after successful persistence, within the existing synchronization/compensation block.
- `src/agentclaw/community/core/mcp/services/cli_passport_scope.py`: accepts optional `requested_cli_identity_modes`; copies persisted sparse modes, overlays explicit intent, and builds the existing complete MCP+CLI scope. Bootstrap omits this argument and retains history-first semantics. Logs include requested and actual outgoing CLI identity maps.
- `tests/community/core/caller_identity/test_service.py`: updates the existing reconciler call assertion.
- `tests/community/core/caller_identity/test_cli_owner_sync.py`: 10 focused regressions through the real service/reconciler with external dependency doubles.

Implementation sequence followed the spec: reproduce the deleted-row bug with failing tests, add minimal request-local propagation, verify complete-scope preservation and compensation, then run the existing affected suites and lint.

## Validation

- Baseline reported and independently confirmed by parent: 113 passed before changes.
- RED: new suite produced 8 failed / 2 passed. The six owner-transition cases submitted historical caller, the explicit-intent parameter did not exist, and the failure-path outbound identity was caller. Evidence: `/tmp/cli-owner-sync-red.txt` (local only).
- GREEN: five affected suites including the new suite: **123 passed**, 17 existing dependency deprecation warnings. Evidence: `/tmp/cli-owner-sync-green.txt` and `/tmp/cli-owner-sync-coverage.txt` (local only).
- New regressions parameterize `dataphin`, `deepinsight-cli`, and `custom-cli`, each with/without a persisted caller row; assert owner result, absent sparse row, unchanged target metadata, retained unrelated historical/local CLI and MCP identities, caller direction, Bootstrap compatibility, no repository-map mutation, and failed-update compensation fencing inputs.
- Coverage: identity service **202/216 (93.52%)**, scope reconciler **142/145 (97.93%)**, combined **344/361 (95.29%)**. Added executable statements intersected with coverage.py statement lines: **4/4 (100%)**. The added service keyword argument belongs to an existing multiline call and adds no independent coverage.py statement; the real service tests validate its outgoing effect. Evidence: `/tmp/cli-owner-sync-coverage.json` (local only).
- Ruff default checks and explicit preview `F,E203,E265` pass for both production and both test files. `git diff --check` passes.
- Production file sizes: service 746 lines; reconciler 333 lines. Existing test-service file is already above 1,000 lines; only its necessary assertion changed. The repository size gate applies to production source.
- Final annotation-only edit was followed by another **10/10** new-suite run.

## Diagnostics and secrets

Existing events retained: `cli_call_type_update_requested/succeeded/failed`, `cli_call_type_update_compensated`, `cli_passport_reconcile_requested/succeeded/failed`, and `agentpass_cli_scope_update_requested/succeeded/failed`.

Added fields: `requested_cli_identity_modes` on reconcile request and `cli_identity_modes` on Passport update request. Both contain CLI codes and normalized owner/caller identity values; existing bot/engine IDs, duration/outcome fields remain. No raw Passport response, headers, exception text, or credentials are added. Tests capture real logs containing an unrelated nested credential fixture and a credential-bearing exception and assert the fixture value never appears, while asserting requested/actual identity and success/failure events.

## Delivery status

No commit, push, deployment, live bot mutation, or merge performed by the coding agent. Independent review, broader regression, and remote CI remain parent pipeline gates. Remote ACI/CI is **not evaluated** by this report.
