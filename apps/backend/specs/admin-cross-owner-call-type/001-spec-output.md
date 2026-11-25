# Super-admin cross-owner MCP/CLI call-type editing

Date: 2026-10-29. User explicitly authorized implementation, rebase onto remote REL20261029 as base, new PRs and fixing concrete CI failures. Source base initially GitHub origin/REL20261029=9e5987016897c2b4eafb47a9a3044443b45a5b46; prior final Caller-to-Owner exception is already merged (#3267). This task supersedes that feature's owner-only editing limitation, not its scope boundaries.

## Existing chain and target behavior
Existing /api/bots/{bot_id}/mcps/{server_code}/call-type and /clis/{cli_code}/call-type authenticate actor, resolve an exact Bot, check edit authorization/active service/engine/resource/collaboration lock, persist via a row-locked repository, synchronize Passport and conditionally compensate on failure.
Allow the authenticated actor to edit these call types if they are either the true Bot owner or a member of configured admin.super_admin. Ordinary non-owner users, collaborators and operator-only actors remain denied. No public admin/force fields or hardcoded personnel. Preserve non-admin Owner rules (including the last Caller-to-Owner prohibition) and the existing final-transition super-admin exception.

## Mandatory identity separation
- actor_id: authenticated administrator/Owner; used for authorization, modifier/audit and lock holder, never as the resource owner.
- owner_id: true current Owner read from the resolved Bot; use for active resource discovery and Passport/Agent Principal addressing and other owner-scoped operations.
- entity_id: immutable resolved Bot entity locator; canonical collaboration lock key is bot_id:entity_id per current CollaboratorLockService. Use this same target key in preliminary checks, transaction writes and failure compensation. Never check the administrator's own Bot lock.
No rewriting Bot owner/entity/binding or changing credential ownership. Entity query remains exact; an admin must still address the actual Bot, ambiguous/missing targets remain rejected.

## Locks and errors
No bypass or stealing of another actor's lock. No epoch is allowed only if the target Bot has no lock; supplied epoch must match a target lock held by the authenticated actor. Preserve transaction-time rechecks, engine/revision CAS and compensation. Existing lock acquire endpoint already resolves the target entity and supports requesting an available lock; no change to shared lock service is required. If Owner currently holds a lock, the admin must coordinate release then acquire normally.
Scope authorization only to the two CallerIdentityService mutation paths; do not globally broaden shared owner-only guards used by acli assets, resource CRUD, reading conversations, publishing or instance lifecycle.

## Allowed files / forbidden changes
Allowed: caller_identity service + minimal scoped guard wiring, its public contract/docs, directly necessary tests and module boundary metadata. Existing repository/Passport implementations are reused unless a demonstrated required behavior gap demands a minimal change documented here.
Forbidden: generic Bot ACL expansion, lock-service permission changes, router architecture changes/new endpoints, database migration, Relay/Cron/BaaS/platform changes, admin config personnel additions, deployment, instance cleanup, session migration.

## Review and tests
Use TDD: reproduce the cross-owner 403 before implementation, real SQLite/service state where feasible. Cover both MCP/CLI, owner vs super admin vs non-owner/operator/collaborator; exact entity vs ambiguous/missing; active service/engine/resource/profile rejection; admin target-owner resource discovery and Passport payload; actor audit attribution; target lock vs irrelevant admin lock; absent/other-holder/stale/raced lock; entity != current Owner after handoff; full sync success/failure and conditional compensation addressing; no privilege injection through HTTP parameters. Keep strict HTTP schema and old unrelated ownership guards.
Add minimal stable diagnostics recording actual actor, target Owner/entity, resource and permission result so future 403 can distinguish lookup miss and denied actor without exposing credentials. Existing HTTP/Passport boundary request/success/failure events should be preserved, with success/failure behavior tests and no credential logging. Do not build a new general logging subsystem.
Run focused/static/architecture checks, independent reviewer and full backend CI script incl report tail against exact base/head, case100%, total>=75% (current repo gate), changed>=90%. Compare failing tests to unmodified release base if necessary; fix actual feature failures and relevant remote gates without weakening thresholds.

## Delivery
New isolated source and OCB worktrees under the prescribed parent directories. Commit task-only files, fetch and rebase onto latest verified source REL20261029 BEFORE first push to avoid unnecessary force. GitHub inclusionAI/Avernet source PR first; verify exact SHA in configured internal mirror before OCB gitlink-only PR to OCB REL20261029. Reuse the legitimate corrective work item where the internal PR platform requires one; keep its internal identifier in the local delivery report. Preserve remote evidence separately and monitor comments/actual checks. No auto-merge or deployment. Prior OCB #5625 is closed and will not be reopened/modified; new worktree/head represents the user's explicitly new cross-owner request.
