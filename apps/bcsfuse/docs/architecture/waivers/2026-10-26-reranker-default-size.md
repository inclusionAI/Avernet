# Reranker default: bounded module size exception

Status: proposed bounded exception, submitted for review with PR #3150.
No CI gate or allowlist is changed.

- Rule: modified source files must not exceed 1,000 lines.
- Scope: `src/interfaces/api/profile_routes.py` and
  `src/bootstrap/oss_business_routes.py` only.
- Existing and temporary maximum sizes: 1,227 and 1,553 lines respectively.
- Allowed change: replace the BGE fallback model literal with Qwen in each file.
  No lines, functions, routes, or dependencies are added to either module.
- Owner: BCSFuse composition and API maintainers.
- Review date: 2026-11-02. Review or remove this exception by this date.

## Reason and controls

The default model must agree across the HTTP adapter, recommendation API,
profile search, and composed OSS search. Splitting several hundred lines of
legacy route registration and shared state for two literal replacements would
expand a small configuration change into an unrelated routing refactor.

The recommendation and profile search contract tests cover the Qwen default,
explicit BGE selection, environment overrides, and disabled reranking. The full
BCSFuse suite exercises the composed OSS routes. Existing imports, exported
handlers, provider resolution, and model-failure behavior remain unchanged.

## Removal plan

Split profile search and OSS business route registration by responsibility in a
separate change, retaining public handler imports and route compatibility.
Remove this exception when each resulting source module is below 1,000 lines.
