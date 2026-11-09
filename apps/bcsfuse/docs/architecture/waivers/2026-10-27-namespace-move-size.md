# Namespace migration: existing oversized modules

Status: proposed bounded exception for review; no CI gate or allowlist is changed.
Owner: BCSFuse application and composition maintainers.
Review date: 2026-11-03.

The repository limits modified source files to 1,000 lines. These existing production and test
modules exceed that limit before and after the directory move. This exception
covers only the mechanical move and import/logger namespace substitutions.
No functions, routes, branches or lines are added to these modules. Comparing
the old AST after namespace substitution with the moved AST found them equal.

| New path under `apps/bcsfuse/` | Before / after lines |
| --- | --- |
| `src/app/application/services/conflict_alignment_service.py` | 1753 / 1753 |
| `src/app/application/services/expert_diagnosis_service.py` | 1839 / 1839 |
| `src/app/application/services/g5_expert_enhancer_impl.py` | 1473 / 1473 |
| `src/app/application/services/group_fusion_service.py` | 1466 / 1466 |
| `src/app/bootstrap/oss_business_routes.py` | 1553 / 1553 |
| `src/app/infra/public/vectorstores/qdrant_local_vector_store.py` | 1319 / 1319 |
| `src/app/adapters/http/dependencies/fusion_dependencies.py` | 2023 / 2023 |
| `src/app/adapters/http/fusion_parity_routes.py` | 1146 / 1146 |
| `src/app/adapters/http/profile_routes.py` | 1227 / 1227 |
| `src/app/adapters/http/worker_routes.py` | 1887 / 1887 |
| `tests/unit/application/test_group_fusion_service.py` (existing tests) | 1171 / 1171 |
| `tests/unit/domain/test_fusion_result.py` (existing tests) | 1178 / 1178 |

Splitting the shared state and registration logic of ten legacy modules during
a package migration would combine routing and service refactors with an import
change, making TC behavior harder to compare. The migration regression tests
remain unchanged except for import paths; no test or architecture gate is waived.
This does not authorize feature work in oversized files.

Follow-up: split HTTP routes by resource, dependency construction by provider,
service orchestration by stage, and Qdrant persistence/synchronization from query
execution. Keep public handler/service contracts and test each split before
removing this size exception. Review the outstanding size debt by the date above.
