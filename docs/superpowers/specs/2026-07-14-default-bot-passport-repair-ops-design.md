# Default Bot Passport Repair Operations API

Date: 2026-07-14

## Background

`POST /api/bots/create-for-others` creates a target user's default bot by
calling `BotService.create_bot` directly. It bypasses the Passport and owner
authorization work performed by regular `POST /api/bots` creation. Existing
active bots are skipped, so calling it again does not repair identity state.

The repair endpoint is deployed and invoked in pre-production while the target
Bot row can belong to either `ac_bots.env=pre` or `ac_bots.env=prod`. The five
reported owners currently each have one active `bot_id=default`, `env=prod`,
`bot_type=personal`, `active_engine=openclaw`, `device_provider=baas` record
without Passport data in `ext`.

The cross-environment Passport design relies on two confirmed deployment
facts:

- pre-production and production tcauthmng use the same physical database,
  `tcauthmng#sec_gzcom4x#sec_tcauthmng0_17629`, and distinguish records by the
  logical `env` field plus the `env|owner|bot` agent ID; and
- Agent ACM is not data-isolated between pre-production and production. Calls
  made through the pre-production ACM client can create and update the same
  credential state used by production.

Therefore `target_env` selects the tcauthmng logical namespace and persistence
records. It does not select a different ACM client or endpoint.

## Decision: Two Separate Phases

The workflow is deliberately split:

1. The pre-production operations endpoint repairs and verifies identity
   control-plane state only.
2. An operator subsequently restarts the Bot through the existing online
   lifecycle. Online container bootstrap updates outbound rules and writes
   runtime credentials.

The pre-production endpoint must not hot-update or restart a prod Bot. Existing
BaaS clients and outbound-rule construction are bound to the deployment
environment, and hot update does not write `/home/admin/.credentials` or
refresh process-local credential caches.

## Goal

Add an administrator-only, synchronous, idempotent operation that:

1. selects exactly one live `bot_id=default` by owner and explicit
   `target_env`;
2. obtains a Passport only through
   `PassportPlugin.apply_first_agent_passport`;
3. rejects an empty token instead of returning an authorization URL;
4. verifies the issued Passport and merges `ext.passport.agent_code` without
   replacing unrelated `ext` keys;
5. ensures and verifies the owner-to-agent relationship in `target_env`; and
6. returns structured control-plane evidence plus
   `runtime.restart_required=true`, without returning the raw token.

## Non-goals

- Repairing a non-default Bot or accepting a caller-supplied `bot_id`.
- Calling `apply_agent_passport`; it can require interactive authorization.
- Creating, restarting, releasing, or hot-updating a Bot.
- Calling BaaS/ARCA or writing container files from pre-production.
- Adding a second ACM client, routing to production tcauthmng, or changing ACM
  endpoint selection.
- Bulk repair. Operators invoke one owner at a time for auditability.

## HTTP Contract

### Request

`POST /api/bots/repair-default-passport-for-others`

```json
{
  "target_user_id": "172168",
  "target_env": "prod"
}
```

Rules:

- The authenticated caller must be in `super_admin()`.
- `target_user_id` is required and trimmed before use.
- `target_env` is required and accepts only `pre` or `prod`.
- `bot_id` is not accepted; the service always uses `default`.
- The target is exactly
  `(target_env, target_user_id, default, is_delete=0)`.
- Zero or multiple live matches is an error.

### Success response

```json
{
  "success": true,
  "message": "default bot Passport 修复并校验成功，需在目标环境重启",
  "error_code": 200,
  "data": {
    "target_user_id": "172168",
    "bot_id": "default",
    "target_env": "prod",
    "action": "repaired",
    "passport": {
      "status": "ISSUED",
      "agent_code": "agent_xxx",
      "credential_id": "credential_xxx",
      "token_present": true,
      "source": "applied"
    },
    "owner_relationship": {
      "verified": true,
      "created": true,
      "auth_id": 123
    },
    "database": {
      "ext_agent_code_verified": true
    },
    "runtime": {
      "restart_required": true,
      "restart_environment": "prod"
    }
  }
}
```

`action` is `repaired` when this call changes state and `verified` when all
required identity state already exists. `passport.source` is `applied` or
`existing`. The API never returns a Passport token, cookie, iframe URL, or
redirect URL.

### Failure semantics

The endpoint uses the existing `ApiResponse` envelope and returns
`success=false` for incomplete control-plane repair.

| Case | `error_code` | Meaning |
| --- | ---: | --- |
| Invalid or missing input | 400 | Required owner/target-env contract was not met |
| Caller is not a super administrator | 403 | Operation is forbidden |
| Exact default Bot is absent | 404 | No target exists in `target_env` |
| More than one live exact target | 409 | Data invariant is violated; no external mutation is attempted |
| First Passport apply returns no token | 5401 | Operations flow cannot complete interactive authorization |
| Passport apply/query or verification fails | 5400 | Identity was not proven |
| Owner relationship create/re-query fails | 5402 | Authorization was not proven |
| Database write/read-back fails | 500 | `ext` was not safely persisted |

External services and the database cannot share a transaction. A failure may
leave a completed workflow prefix. Every completed prefix is safe to retry;
the next call resumes from the first missing verified state.

## Architecture

The HTTP adapter owns authentication, input validation, error mapping, and
serialization only. Repair policy lives in a dedicated core service exposed by
a Service API protocol.

### Service API

```python
class DefaultBotPassportRepairServiceProtocol(Protocol):
    def repair(
        self,
        *,
        target_user_id: str,
        target_env: str,
        operator_user_id: str,
        operator_name: str,
    ) -> dict[str, Any]: ...
```

The concrete service consumes:

- the Bot repository contract for exact env-scoped read and `ext` update;
- `PassportPlugin` for first apply and verification queries;
- `AuthRelationshipPlugin` for target-env query/create/re-query;
- `SkillSetServiceFactory` for the normal creation MCP scope; and
- the existing default CLI policy for the target Bot engine.

It does not consume `BotServiceProtocol`, `DeviceService`, BaaS, or a container
filesystem API.

### Explicit `target_env`

Existing Bot repository operations use `get_current_env()` and cannot safely
serve this endpoint. Add required-env operations instead of widening existing
contracts with optional parameters:

```python
def get_live_by_id_owner_and_env(
    self, *, bot_id: str, owner_id: str, env: str
) -> list[dict[str, Any]]: ...

def update_ext_by_id_owner_and_env(
    self, *, bot_id: str, owner_id: str, env: str, ext: dict[str, Any]
) -> dict[str, Any]: ...
```

The read returns all exact live matches so the service can distinguish zero,
one, and duplicate rows. The update must affect exactly one live row; otherwise
it raises and rolls back. All env-scoped OCB reads needed to construct MCP
scope also receive `target_env` explicitly and never fall back to the runtime
deployment env.

AceAgent is environment-specific, but the current production plugin captures
its base URL from the deployment environment. Add explicit target-env
operations to the plugin contract and implementations. The production
implementation selects the pre/prod URL per call and must not mutate a
singleton's cached base URL. Community/local implementations preserve their
existing no-op semantics while accepting and validating `target_env`.

Passport operations evolve compatibly to accept an optional explicit target
environment:

```python
def apply_first_agent_passport(
    ...,
    *,
    target_env: str | None = None,
) -> dict[str, Any] | None: ...

def query_agent_passport(
    bot_id: str,
    owner_workno: str,
    *,
    target_env: str | None = None,
) -> dict[str, Any] | None: ...
```

`query_auth_status` and `query_token` follow the same rule. Omitting
`target_env` retains the existing current-deployment behavior for normal Bot
creation and lifecycle callers. Supplying it requires `pre|prod`; invalid
values fail before any external or persistence side effect.

The production OCB adapter serializes `target_env` as the Java DTO field
`env`. `ApplyAgentPassportRequestDTO` gains the field; `BotRequestDTO` already
has it. The repair service supplies `target_env` to all initial queries, first
apply, and verification queries. Local implementations and contract tests
preserve the same fallback and validation semantics.

### tcauthmng target-environment execution

tcauthmng resolves the execution environment once at each facade entry:

```text
non-empty request.env -> normalized request.env
empty request.env     -> current deployment environment
```

All recursive first-apply stages consume that resolved value. They must not
re-read `EnvToolUtil` independently:

- `getOrRefreshToken`;
- `getOrRefreshUserPassport`;
- `getOrRefreshAgentPassport`; and
- `getOrRegisterAgentCode`.

For a pre-production call with `env=prod`, every lookup and write uses
`agentId=prod|<owner>|default`. New Agent Passport, User Passport, and Token
records store `env=prod`; Agent Registry is isolated by the same prefixed
agent ID. `queryToken`, `queryAgentPassport`, and `queryAuthStatus` use the
same resolver so apply and verification cannot select different namespaces.

AgentHub registration is part of the same logical environment selection.
When a new agent code is needed, `HubService.registerAgentV2` receives the
resolved environment and submits `AppEnv.PROD` for `prod` rather than deriving
`AppEnv.PRE` from the pre-production process.

ACM integration remains unchanged. `AcmClientServiceImpl`, its singleton
client, configured domain, and the calls to `registerAgentPrincipal`,
`issueDelegationCredential`, `issueExecutionCredential`, and
`getDelegationCredentialStatus` are not made environment-aware. They operate
on the confirmed shared ACM state using the target-scoped agent identity.

## Phase 1 Repair Workflow

1. Validate `target_env` at the HTTP and service boundaries against
   `pre|prod`.
2. Load exact live target `(target_env, target_user_id, default)`.
3. Derive owner/entity metadata, active engine, Bot name/description,
   workspace, target-env MCP codes, and default CLI items from stored state.
4. Query the existing Agent Passport, authorization status, and token with
   `target_env`.
   - If token, agent code, credential ID, and issued status are proven, reuse
     it.
   - Otherwise call `apply_first_agent_passport` once with `target_env` and the
     same metadata rules as normal default-Bot creation.
   - Never call `apply_agent_passport`.
5. Require a non-empty token and agent code. An iframe/redirect response with
   no token is a hard `PASSPORT_FIRST_APPLY_FAILED` error.
6. Re-query `query_agent_passport`, `query_auth_status`, and `query_token` with
   the same `target_env`. Require a credential ID, matching agent code, issued
   status, and token.
7. Merge `ext.passport.agent_code` into the latest stored `ext`, write through
   the exact-env method, then read it back and compare.
8. Query the relationship by
   `(target_env, agent_code, target_user_id)`.
   - Reuse a matching relationship.
   - Otherwise create it with the target owner as `work_no` and the
     authenticated administrator as operator.
   - Re-query and require a matching relationship. A create response alone is
     not proof.
9. Return success only when Passport, target-env owner relationship, and
   database read-back are verified. Always return
   `runtime.restart_required=true`.

## Phase 2 Online Runtime Reconciliation

After phase 1 succeeds, restart the Bot through the existing online lifecycle.
For the current BaaS personal Bots, the existing BaaS in-place restart
preserves the logical Bot/binding identity. The online container startup then:

1. calls the prod `/api/v1/devices/callback/bootstrap-auth`;
2. resolves the repaired agent code from `ac_bots.ext` and queries the token;
3. updates all BaaS-managed physical devices' outbound rules;
4. returns agent code to `start_service.sh`;
5. writes `AGENT_CODE` into `/home/admin/.credentials`; and
6. exports it before runtime processes start.

The online restart is not implemented or triggered by this endpoint.

## Idempotency and Concurrency

- Query-before-apply avoids replacing a complete existing Passport.
- First apply is treated as an ensure operation and is always verified by
  query.
- Relationship query-before-create plus re-query tolerates already-existing
  relationships and concurrent creators.
- `ext` is merged from the latest row and the write requires exactly one row.
- A per-process keyed lock on `(target_env, owner, default)` avoids duplicate
  work inside one application instance. Cross-replica correctness relies on
  query/ensure/re-query semantics, not that lock.
- Retry never calls `apply_agent_passport`, BaaS, hot update, or restart.

## Logging and Security

Every stage logs request ID, operator, target owner, target env, fixed Bot ID,
action, and verification outcome. Logs and responses must not contain the raw
Passport token, cookies, or authorization URLs.

## Test Plan

Tests are written before implementation.

### Core service

- rejects target env outside `pre|prod`;
- always targets `bot_id=default` and only the requested env;
- fails before external mutation for zero or duplicate target rows;
- reuses a complete Passport without either apply call;
- calls only `apply_first_agent_passport` when Passport is missing/incomplete;
- passes `target_env` to every Passport query and first-apply call;
- treats token-empty plus authorization URL as a hard failure;
- rejects missing/mismatched agent code, credential ID, status, or token;
- merges `ext.passport.agent_code` while preserving unrelated fields;
- propagates write errors and rejects failed read-back;
- reuses an existing relationship or creates then re-queries a missing one;
- passes target env to every env-aware dependency;
- never calls BaaS, device hot update, restart, or container filesystem APIs;
- supports retry after each partial-failure boundary; and
- returns `runtime.restart_required=true` for repaired and verified results.

### Repository and plugin contracts

- exact-env Bot read never falls back to `get_current_env()`;
- pre/prod rows with the same owner/Bot ID remain isolated;
- exact-env `ext` update affects exactly one selected live row;
- zero/multiple update matches fail;
- target-env AuthRelationship calls select the requested endpoint per call;
- existing current-env relationship callers retain their behavior;
- Passport calls without `target_env` retain current-environment behavior;
- Passport calls with `target_env=prod` serialize `env=prod` for tcauthmng;
- tcauthmng first apply uses `prod|owner|bot` for every recursive stage;
- tcauthmng query and apply paths resolve the same target environment;
- tcauthmng writes target `env` rather than the process environment;
- AgentHub receives the target `AppEnv`; and
- invalid target environments fail before DB, AgentHub, or ACM calls.

### HTTP and architecture

- validates required `target_user_id` and enum `target_env`;
- denies non-super-admin callers;
- forwards authenticated operator identity;
- exposes no caller-supplied Bot ID;
- maps typed service errors to the documented response;
- exposes no raw token or authorization URL;
- Service API and repository/plugin protocols conform; and
- backend architecture boundary and forbidden-import tests pass.

## Acceptance

Run one canary owner before the remaining owners.

### Phase 1 in pre-production

1. Use ODC to capture the exact prod/default Bot row, including status,
   binding, and `ext`.
2. Invoke the pre-production endpoint with `target_env=prod` and retain request
   ID and non-secret response.
3. Require Passport, relationship, and database verification fields to pass,
   and `runtime.restart_required=true`.
4. Confirm in tcauthmng that Agent Registry, Agent Passport, User Passport,
   and Token state use `agentId=prod|<owner>|default`; all records with an
   `env` column use `prod`, and no new `pre|<owner>|default` identity exists.
5. Confirm through ODC that only the exact prod/default Bot row changed and
   unrelated `ext` fields were preserved.
6. Invoke again and require `action=verified`, unchanged Passport identity,
   and no duplicate relationship.

### Phase 2 online

1. Restart that Bot through the existing online lifecycle.
2. Wait for Bot and binding status to return to `ACTIVE`.
3. Verify online bootstrap logs contain the expected non-empty agent code and
   token-present result.
4. Verify BaaS outbound-rule update succeeded.
5. Verify the new runtime has the same non-empty `AGENT_CODE` in
   `/home/admin/.credentials`.

After the canary passes both phases, repeat for owners `317312`, `136450`,
`382425`, and `210231`.
