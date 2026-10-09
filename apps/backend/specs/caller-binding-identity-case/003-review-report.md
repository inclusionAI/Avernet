# Independent review: Caller binding identity case

Status: PASS for local implementation; remote ACI evidence PENDING. Iteration: 1.

## Scope and evidence
- Base: `227613a5e30667177109978ec73a2bf8ebcd8abd` (GitHub REL20260910).
- Reviewed head: `31c69e8f826b611ee983d1ab1347baa03cdc5acf`.
- Reviewed the specification, code report, complete base-to-head diff, resolver, tests, and module README. The working tree implementation was unchanged when committed to this head.
- Existing flow remains Bot lookup, Caller instance lookup, readiness and ACTIVE checks, scope validation, and returning the existing binding. No device selection, provisioning, persistence, API, or frontend changes.

## Findings
No blocking findings.

| Check | Result | Evidence |
|---|---|---|
| Identity correctness | PASS | entity_id, applied_by, and apply_reason now compare directly against trusted owner, actor, and Bot-derived values. Matching mixed-case values succeed; case-only differences fail. |
| Enumeration compatibility | PASS | Existing normalization of environment, provider, instance/binding status, Bot type and call type remains intact; uppercase fixtures exercise the supported behavior. |
| Authorization boundaries | PASS | Missing or invalid metadata, readiness, exact initialization allowlist, missing/inactive binding, all scope mismatches, and no-fallback behavior are covered by outcome assertions. |
| Lookup integrity | PASS | Tests assert original owner/Bot/actor arguments reach both repositories. |
| Diagnostic safety | PASS | Stable caller_binding_scope_invalid event records only numeric binding ID and failed field name. Captured real logger output verifies fields and excludes nested credential sentinels. Success emits no rejection event. |
| Architecture and dependencies | PASS | README adds only the logger dependency introduced by this fix. Independent module-boundary tests pass. No external transport boundary changes; transport request/response logging is not applicable. |
| Performance and minimality | PASS | Fixed five-field validation adds no repository reads, network calls or state changes. Additional tests verify actual source behavior and required coverage. |
| Static quality | PASS | Independent ruff and diff whitespace checks pass; no new unused imports/variables or orphan helpers. |

## Independent local verification
Run from src/backend unless stated otherwise:

- `uv run pytest tests/community/core/runtime_binding/test_service.py --cov=agentclaw.community.core.runtime_binding.service --cov-report=term-missing --cov-report=xml:/tmp/caller-binding-review-coverage.xml -q`: 48/48 passed, zero failures/skips; affected file 97/97 executable lines covered (100%). Existing dependency deprecation warnings only.
- `uv run ruff check src/agentclaw/community/core/runtime_binding/service.py tests/community/core/runtime_binding/test_service.py`: PASS.
- `uv run pytest tests/community/architecture/test_module_boundaries.py -q --no-cov`: 3/3 passed.
- Repository-root `git diff 227613a5e30667177109978ec73a2bf8ebcd8abd...HEAD --check`: PASS.
- Inspected the retained pre-fix RED output: four matching-case regressions failed and seven original tests passed, consistent with the reported reproduction.

## ACI boundary
Focused test pass rate is 48/48 (100%) and affected-file coverage is 97/97 (100%); these are local evidence, not repository-wide or remote ACI results. The separate regression/PR stage must report actual complete-suite passed/total, total covered/total, and changed-covered/changed-lines for the same base and published head. Required remote thresholds remain 100%, 70%, and 90%, respectively. At review time remote job evidence and those repository-wide numerators/denominators are unavailable: PENDING, not PASS.

Local implementation is accepted for the authorized PR workflow. No deployment or merge approval is implied.
