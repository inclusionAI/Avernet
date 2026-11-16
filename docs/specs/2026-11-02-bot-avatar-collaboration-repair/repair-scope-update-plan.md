# Collaboration repair scope update — 2026-11-05

## Design

The repair command now covers exactly `ownership`, `profile`, and `managers`,
in that order. Remove caller snapshots, caller fact validation and caller
writes from this command's contracts and implementations. Normal MCP/CLI
caller configuration synchronization is unchanged. This deliberately changes
the repair response from four items to three; consumers must accept the new
component vocabulary and count.

Profile repair accepts a valid BCS onboard acknowledgement as success even
when the submitted summary is empty or whitespace-only. BCS continues to
preserve its stored summary for these inputs; success means the existing
operation was acknowledged, not that an empty summary cleared remote data.
Actual transport errors, missing targets and malformed acknowledgements retain
their existing failure/unknown semantics.

## Implementation and validation

1. Add regression coverage for blank-summary success, exactly three report
   items, and no caller-config validation or sends. Run it before production
   edits to demonstrate failures.
2. Remove the caller repair capability across kernel, repository, service,
   plugin implementations and HTTP schema; accept blank-summary acknowledgements.
3. Update consumer conformance, endpoint, facts, delivery, and live acceptance
   tests plus the canonical design and context-boundary documentation.
4. Run focused tests, relevant architecture gates, lint, diff hygiene and
   changed-source line-count checks. Report unavailable live validation.

## Validation evidence

- RED: the initial five regression cases failed on the prior behavior (four
  report components and blank-summary `collaboration_summary_unconfirmed`).
- GREEN: 615 tests passed across repair facts/service/report, production/local
  metadata plugins, consumer conformance, HTTP endpoint/schema, composition,
  unchanged caller-identity and caller transport suites, and seven selected
  architecture suites (protocol contracts, kernel imports, framework isolation,
  module boundaries, service APIs, repository contracts, file-size limits).
- Changed-file Ruff checks and `git diff --check` passed. All 26 changed Python
  files are at most 1,000 lines (largest: 786).
- All seven live acceptance cases collected successfully, including empty and
  whitespace-only summary variants. Live Singlebox execution and its coverage
  gate were not run: this change did not start/provision the required shared
  product stack. No deployment/runtime result is claimed.
- Compatibility: response item count is now three and `caller_config` is no
  longer accepted by the report schema. No database/config migration is needed.
  Reverting this change restores the previous repair contract; normal caller
  identity-mode synchronization is not changed by this patch.

## Singlebox manifest follow-up

The first CI run exposed a missed consumer of the removed Plugin method:
`singlebox_coverage_modules.yaml` still declared
`BcsCollaborationMetadataPlugin.sync_caller` and its production evidence.
The unchanged manifest validator correctly rejected this stale declaration.
Remove only that non-existent operation; preserve the full Core paths, route,
remaining `exists`/`update_profile`/`aclose` denominator, and all thresholds.

- Reproduced the exact CI error with the production manifest-check CLI.
- Added two registration regressions; both failed before the manifest fix.
- The same manifest-check CLI passes after the fix.
- The complete `singlebox/ci/tests` run yielded 222 passing tests and three
  failures. All three failures were independently reproduced from a clean
  `git archive HEAD` snapshot: two expect the absent `singlebox/ci/pre_push.sh`,
  and one requires a historical workflow branch trigger. They are pre-existing
  and outside this repair contract change; no checks were weakened or skipped.
- An additional 15 backend boundary, repair-scope and consumer-conformance tests
  passed. Live Singlebox stack/coverage execution is still not claimed.
- Focused Singlebox coverage tooling suites passed: 45 tests and 14 subtests,
  including manifest validation, report generation, registration, workflows,
  and artifact verification. Changed-file Ruff and diff hygiene checks passed;
  the added Python regression file is 53 lines.
