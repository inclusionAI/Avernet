# Admin Caller to Owner implementation plan

Goal: permit the final aggregate transition only for a configured super admin who already has edit authority.

1. Add real SQLite service-chain tests for both resources, role rejection, locks, aggregate persistence and compensation; observe missing authorization before implementation.
2. Add default-deny internal authorization to repository protocol/implementation methods. Resolve trusted scope membership in CallerIdentityService after owner/lock checks; log the authorization decision without secrets.
3. Run focused identity/repository/API/scope suites and static checks. Record exact results in 002-code-report.md; leave full regression and PR delivery to parent.
