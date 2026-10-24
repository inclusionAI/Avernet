# Tasks

- [x] Remove feature-owned membership enhancements and audit; retain one current selection table.
- [x] Preserve the existing MCP Header contract; catalog uses business Bot plus user.
- [x] Verify service-Bot draft-only edits without knowledge version linkage.
- [x] Run focused, HTTP, contract, startup and adjacent architecture regression checks.

## Local validation

Adjacent suite: 1128 passed. Current selection, contracts, HTTP and two-phase boot:
42 passed (overlapping; do not sum). The latter also verifies disabling does not
call the remote catalog. Existing unused textwrap lint debt is reproduced from
fixed HEAD; changed-file formatting passes. No commit, push, PR or deployment SQL
was executed. This is controlled local evidence, not production acceptance.


## Public PR delivery (2026-10-23)

The user has authorized this public enablement-only PR. No authorization
management, private adapter or MCP transport change is included.

- [~] Fix the three review findings: moved-space recovery, draft query indexes,
      and disabled-row growth / current-selection indexes.
- [ ] Exercise the real consumer in catalog contracts and correct stale UUID docs.
- [ ] Re-run focused/adjacent tests and independent review on the refreshed base.
- [ ] Push normally and open the PR against dev with the repository template.

The earlier "no commit/push/PR" text records the previous validation snapshot;
DDL execution and deployment remain unauthorized.
