# ExpertChat Instance Caller Token forced installation

## Requirement and boundary

When a dedicated non-Teclaw ExpertChat Caller container is created/rebuilt/upgraded and its publish reaches SUCCESS with an IAM token, force reinstall the Caller credential through the existing internal exchange path. Existing instance fast-path reuse and ordinary chat keep default False. The existing path directly calls CallerIdentityService and does not need HTTP/orchestrator parameters. This change does not include an ASC fallback when IAM is absent, external BaaS restart detection, schema changes, scheduling changes or OCB gitlink-only updates. GitHub inclusionAI/Avernet REL20261117 is base (initial4dba3aa803); PR-only, no deployment/merge/live data changes.

## Existing chain / permitted changes

ExpertChatInstanceService SUCCESS -> _exchange_caller_identity -> CallerIdentityService.exchange_caller_identity -> ExpiryAwareCallerRuntimeUpdater -> repository caller_token_update/caller_token_refresh -> existing outbound delegate. Add optional force_runtime_refresh=False through relevant internal contracts. Only pass extra kwargs when True for compatibility with existing default consumers; expiry wrapper consumes the option rather than forwarding it to BaaS/Corp delegates. Preserve exact Bot/Caller/entity/env/binding checks and shared IAM/ASC SQL row lock. Reinstall even when credential expiry is shorter/equal; on successful installation persist actual new expiry, not max(old,new). Permit regression only explicitly through force path; normal update and ASC increasing-expiry guard remains. Failure leaves expiry unchanged via transaction rollback. No token/auth values or raw infrastructure exception text in new diagnostic events.

## Engine/stage matrix

Non-Teclaw dedicated Caller, online initialization with IAM: forced install and accurate expiry. Ordinary draft/pre/online chat: default existing policy. Teclaw engine or provider across shared stages: existing bypass, no expiry reads/writes introduced. Missing IAM/init/failed provisioning or ready fast path: no forced installation.

## Review and verification

Check both existing skip guard and transaction post-install monotonic guard; verify same scoped SQL row lock. Tests cover force shorter/equal/longer; ordinary shorter/equal skip; delegate failure rollback; invalid expiry/scope/binding still rejected; concurrent normal/forced/ASC ordering; initialization SUCCESS flag forwarding; no IAM/nonSUCCESS/fast reuse; Teclaw bypass. Include a real service->expiry wrapper->repository->recording outbound delegate regression, not just mock keyword tests. Check protocol/architecture tests and Ruff/diffcheck. Use project venv Python on PATH for CI/report scripts to avoid systemPython2. Full Backend CI and remote checks, total and changed coverage recorded with original thresholds (review changed>=90%).

## Delivery

Write002/003/003b/009 reports. Rebase feature on latest remote REL20261117 after commit and before push; source remote explicitly github. English conventional PR title and Problem/Solution/Validation body. No merge/deploy. If OCB only gitlink, no OCB changes/worktree/PR.
