# BaaS Caller status implementation plan

1. Add signed HTTP regression tests; verify the missing GET fails before implementation.
2. Share existing BaaS verification behind route-specific wrappers and add a flat read-only GET using the existing instance Service API.
3. Register real DI/SQLite happy/error cases and live Singlebox acceptance; preserve the public endpoint policy and coverage thresholds.
4. Run focused JWT/legacy/public/domain tests, framework and complete OpenAPI suites, then full Backend behavior regression; record exact evidence.
