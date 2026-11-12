# Caller instance status workflow

- User scope: add read-only caller existence/liveness OpenAPI; no provisioning, restart, deployment, OCB worktree or gitlink update.
- Worktree: caller-instance-status-20261112
- Source remote: github, inclusionAI/Avernet (verified using gh repo view and fetch). Internal origin mirror is excluded from source delivery.
- Base (bottom): latest remote github/dev; topic commits replay on top.
- Initial base: d0cdd02345
- Initial topic: feat/caller-instance-status-20261112
- Task: caller-instance-status
- Authorized: implementation, tests, fresh worktree, rebase onto dev, push feature branch, PR, fixes for CI and reasonable reviews. No additional approval is needed for these steps.
- Current stage: code; demand review written before implementation. Existing ExpertChat access (owner / public / collaborator) is required rather than generic MEMBER-only to retain caller chat entitlement; Public ServiceChecked delegates this check to ExpertChatService.
- Deployment / pre / prod QA: not requested, not executed; the reference endpoint is a side-effecting lifecycle POST and has not been invoked.
