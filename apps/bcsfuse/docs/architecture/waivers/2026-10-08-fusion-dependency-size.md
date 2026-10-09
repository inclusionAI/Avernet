# Fusion dependency module size exception

Status: requester-approved bounded exception on 2026-10-09 for the four-line
provider-resolution repair below. No CI gate or allowlist is changed.

- Rule: added or modified source files must not exceed 1,000 lines.
- Scope: `src/interfaces/api/dependencies/fusion_dependencies.py` only.
- Existing size: 2,013 lines before the embedding-provider repair.
- Temporary maximum: 2,020 lines; the repair adds four lines.
- Owner: BCSFuse composition maintainers.
- Review date: 2026-11-07. The exception must be reviewed or removed by this date.

## Reason and bounded change

The embedding getter lost the composed provider when the service cache was reset.
It now resolves the active application's registry before consulting the standalone
cache or environment settings. An absent composed provider remains absent; the
getter must not create an environment-backed substitute.

Reducing this existing module below 1,000 lines would require moving more than
1,000 lines across profile, vector, G5, G9, group, and capability composition.
Factories share singleton state and exported accessors that tests and consumers
replace. A broad relocation during this compatibility repair risks changing those
contracts and is outside the small provider-resolution fix.

## Risks and controls

The existing oversized module remains harder to review and maintain. The allowed
growth is restricted to the provider-resolution fix; unrelated behavior or new
features are not covered. Existing exports, globals, cache-reset behavior, and
standalone construction stay in place. Regression coverage checks cache reset,
direct context switching, and missing composed providers without environment
fallback, followed by fusion integration tests. No CI check is weakened and no
allowlist entry is added. The requester's approval applies only to this bounded
repair, not to further growth or deployment.

## Removal plan

Split service factories and state management by responsibility in a separate
change. Preserve exported accessors and monkeypatch behavior using explicit
dependencies, without module replacement or dynamic attribute interception.
Add compatibility tests for singleton reset, context switching, and replaced
accessors before moving construction code. Remove this exception once every
resulting source file is below 1,000 lines.
