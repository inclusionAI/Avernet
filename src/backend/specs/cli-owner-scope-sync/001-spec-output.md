---
agent: tc-review
status: completed
created: 2026-09-20
iteration: 1
---

# CLI owner identity synchronization

## Requirement and existing flow

A CLI identity mutation to owner removes its local sparse caller override. The subsequent Passport scope reconciliation currently merges historical CLI items first; without an explicit override, the historical caller identity survives. The API can therefore report owner while sending caller to Passport.

Fix this transition for both default and custom CLIs. Keep existing authorization, last-caller protection, repository semantics, failure compensation, and unrelated CLI/MCP identities intact. This work is authorized as a focused bug fix on the existing feature worktree based on GitHub dev. No deployment, production mutation, or merge is included.

## Coding spec

The existing chain remains identity application service → repository mutation → CLI Passport reconciler → Passport plugin. Only the identity service and existing scope reconciler need production changes, plus focused tests. Do not change routers, storage schema, global merge defaults, engine, relay, BCS, or Passport SDK behavior.

### Key methods and contracts

| Method | Responsibility | Contract and side effects |
| --- | --- | --- |
| Identity service CLI update method | Carry the validated mutation intent into synchronization | After successful persistence, pass the exact target CLI code and normalized owner/caller mode to reconcile; preserve existing compensation and error mapping. |
| CLI Passport reconciler reconcile/snapshot construction | Apply request-local identity intent to the complete scope | Accept optional requested CLI identity modes. Copy persisted sparse overrides, overlay explicit requested values, then use the existing full-scope merge/update flow. Absence means existing boot/reconciliation behavior. Do not mutate the repository mapping. |

The optional parameter is necessary because boot and ordinary reconciliation have no user mutation intent. Explicit request values take priority over persisted sparse values for that synchronization only. Historical CLI membership and metadata and unrelated historical/persisted identities retain their existing semantics.

### Domain model

No new entity or persistence fields. The request-local mapping represents one authorized CLI identity mutation, lives only for the reconcile call, and is not stored. Keys are the already validated CLI codes; values are normalized `owner` or `caller`. Existing CliItem and mutation result models remain sufficient. owner continues to be represented by absence of a sparse row.

### Diagnostics and external boundary

Use existing logging facilities. Record the bot identifier, target CLI/mode mapping, resulting CLI identity mapping, operation, outcome, elapsed time, and existing trace/request context when available. Retain existing Passport query/update request, success, and failure logging and exception semantics. Do not log the raw Passport, full headers, authentication materials, tokens, cookies, credentials, secrets, or sessions. Any newly logged nested boundary data must use existing recursive redaction. Tests should prove successful target propagation, failure diagnostics and compensation, and absence of raw credential fixture values.

## Review spec

- Explicit owner reaches the real reconciler and Passport update despite deletion of the sparse row.
- caller→owner and owner→caller both work; default and custom CLI names follow the same rule.
- Full-scope updates retain other CLI identities and MCP items; no reset-all-owner shortcut.
- Boot/ordinary reconcile without explicit mapping behaves unchanged.
- Authorization, lock/engine checks, last-caller protection, repository persistence, and failure compensation are preserved.
- No unused imports/variables, Python whitespace violations, or new source files exceeding 1,000 lines.
- Focused baseline: parent reports 113 passing tests; rerun affected tests and applicable lint after changes.
- Measure coverage using pytest coverage and a meaningful changed-line report; require changed-line coverage above 90%. Do not lower existing module/CI thresholds, exclude production code, or add assertion-free tests. Report any broader file-coverage shortfall explicitly.

## QA spec

| Case | Action | Expected |
| --- | --- | --- |
| Historical caller to owner | Real service/reconciler path; persisted sparse row disappears | Passport receives target owner and service returns owner. |
| CLI variants | Parameterize default and multiple custom CLI identifiers | Same transition semantics for all identifiers. |
| Owner to caller | Persist caller and reconcile | Passport receives caller. |
| Scope preservation | Include unrelated historical caller/owner CLIs, persisted caller overrides, metadata and MCP items | Only requested CLI identity changes; remaining scope preserved. |
| No mutation intent | Ordinary/boot reconciliation without new argument | Existing history-first behavior remains. |
| Sparse mapping precedence | Supply persisted and explicit values | Explicit target wins; input mapping remains unchanged. |
| Passport failure | Update raises | Existing error and repository compensation behavior retained; no success diagnostic. |
| Guards | Run existing unauthorized, lock, engine and last-caller tests | Protected operations remain rejected before prohibited effects. |
| Observability | Capture successful and failing logs with synthetic credential fixtures | Target and result identity information appears; raw credentials do not. |

Use isolated test doubles for external services and the real service/reconciler path where validating propagation. No live bot operations are necessary for this local regression.

## Ship spec

Develop in the preselected feature worktree; PR base is GitHub dev. Run local review and regression, then follow the parent pipeline's separate commit/rebase/PR gates. No ARCA or pre/prod deployment is authorized by this spec. Rollback is reverting the focused code commit; no schema migration is required.
