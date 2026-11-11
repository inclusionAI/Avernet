# Avernet agent entry point

Avernet is the public implementation. Preserve public reproducibility and keep
private endpoints, credentials and runtime state outside this repository.

## Start with the relevant context

1. Read [CONTEXT-MAP.md](CONTEXT-MAP.md) to select the app and topic.
2. Read the app README and the closest applicable AGENTS.md before changing it.
   Local guidance adds to the repository rules within its own scope.
3. Follow the affected domain's contracts and source/test evidence. Read specs
   for task-specific intent or history; completed plans do not replace current contracts.

## Required reading by task

| When | Read before proceeding |
| --- | --- |
| Changing code | [Development rules](docs/agents/development.md), including testing, file hygiene and type contracts. |
| Architecture, API or dependency boundaries | [Architecture constitution](docs/arch/arch.rules.md), [CI constraints](docs/arch/ci.enforce.md), [Context Boundary format](docs/arch/context-boundary-format.md), [Protocol tests](docs/arch/protocol-contract-tests.md). |
| Backend work | [Backend README](apps/backend/README.md), then its domain and diagnostic links. |
| Skill management, publication or activation | [Skill Center rules](apps/backend/src/agentclaw/community/core/skill_center/AGENTS.md) and the skills section of the development rules. |
| BCS Rust work | [BCS rules](apps/bcs/AGENTS.md), then its CLAUDE.md and affected crate context. |
| Frontend work | The app's local rules and the frontend design-token section of the development rules. |
| Commit or PR preparation | [PR conventions](docs/agents/pull-requests.md), [issue tracking](docs/agents/issue-tracker.md). |
| Pushing or changing CI/coverage | [Push validation](docs/agents/validation.md); keep repository hooks and mandatory gates enabled. |
| Review | Applicable standards above; also read `/tmp/CODE_REVIEW.md` if it exists. |
| Knowledge or specification updates | [Knowledge layout and promotion](docs/agents/knowledge-standard.md), [domain/ADR lifecycle](docs/agents/domain.md), and affected source/contract evidence. |

A Markdown link is a navigation instruction, not proof that the target was loaded.
Read the relevant row when its condition applies; load other topics only as needed.
When creating or updating knowledge, use the [templates](docs/agents/templates/README.md) and the owning document; preserve existing contracts.
When durable behavior, boundaries or diagnostics change, update the owning
knowledge and navigation with the change; use the knowledge standard for layout.
Report actual verification, failures and remaining gaps separately.
