# Skills Track B — Pre-production acceptance and release gate

**Status: PRE-PROD PENDING.** This runbook is intentionally executable without
claiming that the release gate has passed. Run it only with approved
pre-production credentials, one ready owner Bot, and one authorized service-Bot
collaborator. Do not run production or pre-production schema changes from an
application shell without the normal database change approval.

## Deploy-before-code schema gate

Apply `src/backend/src/agentclaw/community/core/skill_center/sql/2026_08_04_local_skill_cleanup_work.sql`
before deploying code that can commit Local Skill replacement or deletion. #726
introduces no additional DDL.

Before rollout, an approved database operator must record the target database
version and verify that `ac_local_skill_cleanup_work` exists with the expected
SHA-256 locator key:

```sql
SELECT @@version, @@character_set_server;
SHOW CREATE TABLE ac_local_skill_cleanup_work;
SELECT index_name, column_name, seq_in_index
FROM information_schema.statistics
WHERE table_schema = DATABASE()
  AND table_name = 'ac_local_skill_cleanup_work'
ORDER BY index_name, seq_in_index;
```

The required unique-key dimensions are `env, owner_id, bot_id,
package_locator_hash`; the digest is a 64-character SHA-256 value. Save the
query output, migration identifier, deploy timestamp, and operator identity in
the release evidence.

Rollback is only `DROP TABLE ac_local_skill_cleanup_work` **before** any
application version that uses it is deployed. After rollout, use forward repair
and retain pending cleanup work; dropping the table can lose recovery evidence.

## Test data and evidence

Record these values before starting: environment, deployment version and commit,
request IDs, owner entity ID, collaborator entity ID, owner Bot ID, tenant ID,
Skill ID, response status/code/message, timestamps, runtime probe result, and
the owner container path proof. Use a new harmless skill name for the run.

Use raw `application/zip` requests only. Each archive must contain one effective
`SKILL.md` with an English name and description. Do not test marketplace, Git,
Center, install, download, or a separate Active-list endpoint: they are outside
this contract.

## Owner lifecycle

1. With the owner principal, upload the ZIP to
   `POST /openapi/v1/bots/skills/upload?bot_id=<owner-bot-id>`. Verify `201`,
   `201000`, `operation=created`, and `active=false`.
2. List with `GET /openapi/v1/bots/skills?bot_id=<owner-bot-id>` and fetch
   `GET /openapi/v1/bots/skills/<skill-id>`. Verify metadata-only Envelope/Page
   data, owner scope, and no owner, actor, tenant, path, package, or manifest
   fields.
3. While the Bot is intentionally offline but database access remains available,
   repeat list and detail. They must still return the desired database state.
   Restore readiness before any mutation.
4. Activate using `POST .../<skill-id>/activate`. Verify `active=true`, runtime
   mapping, and the owner Bot container contains the complete package.
5. Upload a changed ZIP with the same name while Active. Verify `200`,
   `operation=updated`, the same Skill ID, and no transient runtime loss. Record
   before/after runtime probe evidence.
6. Deactivate using `POST .../<skill-id>/deactivate`; verify `active=false` and
   no active runtime mapping.
7. Delete using `DELETE .../<skill-id>`; verify the standard deleted Envelope,
   absent list/detail result, removed association/exclusion, and no usable owner
   package. Retain the cleanup-work evidence if post-commit purge is deferred.

## Collaborator lifecycle and tenant negatives

1. Grant the service-Bot collaborator edit permission for the same owner Bot.
   Upload or replace a second harmless package using the collaborator principal.
2. Verify the persisted Local Skill owner is still the owner entity, bytes land
   in the owner Bot container, and audit/operation attribution identifies the
   collaborator only as actor. Public response payloads must not expose either
   identity.
3. With a principal from another tenant that owns a different globally scoped
   Bot, reuse the same tenant-local Skill name and verify list and raw ZIP
   upload-or-replace operate only on that tenant's own Bot data. The two Bots
   must have distinct `(env, entity_id, bot_id)` identities: that identity is
   deployment-wide unique and is also the scope of cleanup work. Confirm the
   target tenant's rows, desired state, packages, associations, exclusions, and
   cleanup work do not change.
4. For masked target-tenant negatives, use a Bot ID that exists in the owner
   tenant but is missing from the other tenant. Attempt list, detail, raw ZIP
   upload-or-replace, activate, deactivate, and delete against the owner
   tenant's identifiers. Every response must be the masked `404000` Envelope;
   confirm no target row, desired state, package, association, exclusion, or
   cleanup work changed.

## Completion rule

Mark this runbook **PASS** only when all owner, collaborator, tenant-negative,
schema-verification, and evidence steps pass. Until then the status is
**PRE-PROD PENDING**, and Skills Track B must not be described as release
complete.
