# Insight improvement management v1

## Purpose and boundaries

Management of improvement records is independent of repair verification. An authorized
operator can close, reopen, start, defer, reject, edit or assign an item to manual
handling without a new Session, handledAt, appliedAt, a waiting window or delivery
proof. Existing verification endpoints and their policies are unchanged. New clients
must call this contract, not report a fabricated DISAPPEARED result.

This does **not** approve pending candidates, change Owner/Bot/evidence, launch or
cancel Evolve jobs, write Bot configuration, grant repair consent or publish rules.
Managing an item with a running repair does not stop that repair; inspect its returned
latestEvolveTaskStatus and use the separately authorized repair lifecycle if needed.
PENDING_ADMIN items may only be edited here; retain existing review endpoints.

## Composition and authorization

`createInsightManagementRuntime(db, env).router` is mounted at `/api/insight/v1`
in the public ClawWeb bootstrap. Enterprise hosts using a separate bootstrap must
mount the same factory explicitly before releasing the feature. No database migration.

Two equivalent prefixes:
- `/admin/improvement-management`: existing admin role and real actor identity.
  Writes require `X-Insight-Management-CSRF: 1`; foreign origins are rejected.
- `/internal/governance/improvement-management`: existing HMAC Agent authentication,
  explicitly configured `improvement.manage` scope. No unsigned loopback bypass.

This scope is a privileged **cross-owner record-management** grant. Configure only
trusted management clients in the existing `INSIGHT_AGENT_CLIENTS_JSON` configuration;
do not grant it to ordinary Bots. Never hardcode or return client secrets. Existing
`verification.force` clients do not automatically gain the new scope.

HMAC canonical string is METHOD + newline + router-relative req.path (without
`/api/insight/v1` or query) + newline + timestamp + newline + nonce + newline +
SHA256 of JSON.stringify(body), or SHA256 of empty bytes for a body-less GET.
Use fresh signatures/nonces for transport retries, but retain command requestId.

## API

- GET `{prefix}/capabilities`: actions and workflow boundaries.
- GET `{prefix}`: list, optional ownerUserId/botId/status/cursor/pageSize (1–100).
  Follow nextCursor; never interpret one page as the complete inventory.
- GET `{prefix}/{id}`: current item and version.
- POST `{prefix}/{id}`: one explicit command.
- POST `{prefix}/batch/execute`: `{ "items": [{ "improvementId": 123, ...command }] }`.
  1–50 unique explicit IDs. No wildcard mutation. Item validation/conflict errors are
  returned per item; the batch is not atomic. Database/transport failures abort the
  remaining work and require readback, not automatic replay of the batch.

Command example (illustrative ID/version, not production authorization):
```json
{
  "requestId": "cleanup-20261103-item123",
  "version": 4,
  "action": "close",
  "reason": "Owner-approved retirement of an old issue; not a verified repair"
}
```

Actions:
| Action | Result |
| --- | --- |
| close | RESOLVED, management closure, no evidence/waiting prerequisites |
| reopen / defer | ACTIVE, resets the verification interpretation of this cycle |
| start | IN_PROGRESS, starts tracking only, not an executable repair |
| assign_owner | ACTIVE; direct-evolution governance type becomes ASSIGN_OWNER; ordinary manual types remain manual |
| reject | ARCHIVED; OTHER reason and explanation retained in existing rejection feedback |
| edit | Preserve state; title, note (append-only), suggestedAction can be provided |

Every command requires a stable requestId (1–128 ASCII letters/digits/`_.:-`),
positive integer version and a non-empty reason (max 1000 characters). `edit` accepts
title (1–256), note and suggestedAction (max 5000 each). Unknown fields are rejected.
No newSessionCount is requested: this is not a verification claim.

## Persistence, recovery and effect reporting

A transaction updates version and record state, appends a JSON-encoded management
audit event to user_guidance and reads the item back. Owner, Bot, original evidence,
original narrative and running tasks are unchanged. Request IDs are scoped per item;
reuse with the identical canonical command and actor returns `replayed: true`, the
original receipt and the **current** item, even if later operations changed it.
Reusing a requestId for different content/actor fails with REQUEST_ID_CONFLICT.
A stale version fails with VERSION_CONFLICT. Concurrent submissions cannot silently
overwrite each other. On unknown outcomes GET first, then reuse the original command
only as needed. Do not overwrite user_guidance via legacy raw-edit interfaces: the
management audit and replay receipts live there; use `edit.note` to append instead.

Responses contain `improvement`, `receipt` and `replayed`. Receipts include actor,
action, reason, timestamp, previous/next status and version. Closed management items
project `resolvedSource=MANUAL_CLOSED` and `verificationStatus=INSUFFICIENT_DATA`
(the existing DTO vocabulary), not VERIFIED/AUTO_VERIFIED. This status describes
missing effectiveness verification, not a failed management command. Later genuine
verification events override the earlier management interpretation. Management
closures do not count toward automated-closure success or rule-evolution promotion.

## AIStudio / stdlib client

Use `public/modules/clawinsight/scripts/insight_manage.py`, requiring only Python 3.
Provide INSIGHT_BASE_URL (origin), INSIGHT_AGENT_ID and INSIGHT_AGENT_SECRET securely
in the runtime environment. The key must match a server-configured management client.
Do not print or export secrets into chat, code or receipts.

```sh
python3 insight_manage.py capabilities
python3 insight_manage.py list --status IN_PROGRESS
python3 insight_manage.py get --id 123
python3 insight_manage.py execute --id 123 --file command.json
python3 insight_manage.py batch --file batch.json
```

The client never follows redirects or retries mutations. Batch partial failures exit 2.
Non-success and uncertain transport outcomes exit 1; complete success exits 0.

## Release checklist

1. Run contract, authorization, repository and legacy verification regression tests.
2. Release host with the management runtime mounted (public and enterprise independently).
3. Provision the intended management identity/scope, not wildcard permissions for all Agents.
4. Distribute the client and change the AIStudio management procedure to use it.
5. Verify capabilities, one explicit operator-approved management command and GET readback.

Local code/test completion is not deployment, credential provisioning or AIStudio migration.

## Local validation (2026-11-03)

- Based on fetched `origin/dev` at `ed856e92b`; original dirty checkout was not merged or overwritten.
- ClawInsight type check and build passed.
- Five focused ClawInsight suites: 95 tests passed, including 16 management contract tests.
- Python stdlib client exercised against the actual local HTTP router (signed GET, Chinese reason, POST and idempotent replay).
- ClawWeb host type check and three bootstrap/security/regression tests passed.
- Repository persistence code was mechanically split into model/core/verification files to keep each changed source below 1000 lines; existing import entrypoint and public repository class remain compatible.
- Rule promotion counts now inspect the latest verification projection rather than matching old VERIFIED text anywhere in history; administrative closure is not counted as automated success.
- No production deployment, remote task change, credential provisioning or business mutation was performed for this patch.
