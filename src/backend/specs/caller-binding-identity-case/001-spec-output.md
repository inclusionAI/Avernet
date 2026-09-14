# Caller binding identity case preservation

## Requirement and boundary
Fix Caller runtime binding scope validation on GitHub inclusionAI/Avernet REL20260910. Deliver a reviewed and tested pull request; deployment, merging, and OCB gitlink updates are outside scope.

The existing resolver reads Bot and Caller instance repositories, verifies instance readiness and an ACTIVE binding, validates scope, and returns the existing binding ID. Keep this flow and exception semantics.

## Implementation
Compare entity_id with owner_id, applied_by with actor_user_id, and apply_reason with caller_instance:{bot_id} exactly, preserving identifier case. Keep existing enum normalization for environment, provider, status, bot_type, and call_type. Do not normalize both sides, rewrite IDs, provision instances, or add fallback paths.

Allowed changes: core/runtime_binding/service.py, focused runtime binding tests, and these task reports. Add a stable diagnostic event identifying the failed scope field using the existing logger; never log complete request/binding/instance objects or credentials. No external transport boundary is being changed.

## Review and verification
- First demonstrate regression tests fail on original code, then pass after the fix.
- Matching uppercase owner and mixed-case actor/Bot IDs succeed; repository calls preserve their original values.
- Different identities, including case-only differences, fail closed.
- Missing/not-ready instances, invalid binding IDs, inactive bindings, initialization allowlist, environment and provider checks retain their behavior.
- Verify diagnostics and absence of credential sentinel values in logs.
- Run focused tests, static checks, backend CI and independent review/regression. Target affected-file coverage above 90%; retain repository total/changed-line thresholds.

## Delivery
Base: github/REL20260910 at 227613a5e30667177109978ec73a2bf8ebcd8abd. Head: fix/caller-binding-identity-case-rel20260910.
Create English PR with Problem, Solution, Validation sections. Push only to GitHub inclusionAI/Avernet topic branch using --no-verify. Process actual review comments and CI failures without weakening gates. Report pending/infrastructure failures accurately; do not merge or post review replies.
