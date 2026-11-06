# Failed draft restart implementation plan

**Goal:** Recover the original Provider for failed, unbound service drafts while separating Caller and published instances.

**Architecture:** Add a pure scope classifier in bot management and minimally wire it into the existing paged historical Provider resolver. Existing allocation, locks, Provider override policy, and persistence remain authoritative.

1. Add realistic service binding fixtures and a public restart regression reproducing newer Caller PENDING masking an older released draft. Run it and confirm FAILED_WITHOUT_BINDING before production edits.
2. Add the small classifier and service-only resolver wiring; unknown scope fails closed, known foreign scope is skipped, complete lifecycle metadata identifies drafts including BaaS drafts with publish_id.
3. Expand focused tests for scope precedence, identity, pagination, live/unknown refusal, missing Provider, secrets in logs, duplicate retry and failed-then-successful allocation with fresh binding writeback.
4. Run focused plus existing lifecycle suites, coverage, static checks and diff hygiene. Record actual results in 002-code-report.md; independent review and regression are handled by main agent.
