# SDD ledger — plan: apps/bcs/specs/2026-10-09-provider-slug/plan.md

Spec: spec.md. Worktree: codex/provider-slug, based on 6c78364a1.

Pre-flight: HTTP DTOs flow into application commands, then core projections and
repository records. Slug uniqueness and atomic metadata updates belong to both
repository implementations; public projection and validation belong to core.

Ruling: Keep the existing oversized Provider files intact — the user explicitly
deferred splitting — the existing file-size gate violations remain; no allowlist
or gate was weakened.

Compatibility: Preserve old core registration/update signatures using additive
slug-aware methods — existing callers remain source-compatible — repository and
command implementers must adopt the explicitly changed contracts.

Hooks: Use the repository's actual hook installer devops/hook/install_git_hooks.sh
instead of the absent scripts/install_git_hooks.sh from AGENTS.md — verified the
available entry point — no feature behavior changes.

Task 1: complete. Initial HTTP slug tests failed before implementation. After the
implementation and review fix, provider_slug_contract passes 8/8.

Task 2: complete. Provider repository conformance passes 5/5; migration unit tests
pass 37/37; the legacy-row upgrade/repeatability test passes 1/1.

Task 3: complete. Core/application/HTTP behavior is implemented, including optional
legacy inputs and protocol DTO defaults. Initial affected module regression passed
627 tests, 4 ignored; provider HTTP contracts passed 60 tests before review changes.

Final review: independent read-only reviewer found one P2 issue involving an
invalid admin_callback_url after the slug write; no other actionable findings.

Final: fixed callback/slug partial mutation —
invalid_callback_does_not_reserve_a_registration_slug and
invalid_callback_patch_preserves_slug_and_all_metadata both RED→GREEN; the full
provider_slug_contract suite passes 8/8. Callback validation and config merge now
precede the sole Provider insert/metadata update.

Task 4: complete. Expanded regression passes 1,255 tests (9 ignored),
bootstrap passes 309 (5 ignored). Both commands exit 0 after the sandbox's local
socket restriction is resolved. Static conformance comparisons have no new
failures and resolve 7 existing Provider entries. The extra whole-workspace
discovery/baseline listing was stopped after prolonged execution; new Provider
repository/core/application conformance suites actually ran and passed in the
module regression. validation.md records final results and unavailable checks.

Integration: The follow-up request authorizes committing and pushing the named
branch and creating a PR to dev with a structured English summary. Skip new
verification and local Git hooks as explicitly requested; preserve the worktree.
