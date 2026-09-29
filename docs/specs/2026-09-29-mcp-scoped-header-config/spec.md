# MCP scoped Header configuration

Status: approved design; implementation in progress. Target: `dev`. Issue: #2498.

## Problem and authority

The MCP detail page currently saves one user-level Header map. A user needs to declare a Header value for all owned Bots or override that Header on selected Bots, without installing an MCP or changing Bot URL/API-key settings. [ADR 0015](../../adr/0015-merge-user-and-bot-mcp-headers-per-key.md) is the domain decision. The existing user-only config routes retain their contracts.

`ac_user_mcp_config.extra_config.headers` is the user default. `ac_bot_mcp_config.config.headers` is the explicit per-Bot map. For normal MCP Center endpoints, effective Headers merge case-insensitively by Header name, with a Bot value replacing a user value of the same name and unrelated user names inherited. Engine/platform defaults remain below user values and platform-managed Headers retain their existing final authority. For a Bot custom URL, inherited user/default/managed credentials remain excluded; only Bot-explicit Headers can be sent to that URL. No new table or configuration provenance field is introduced.

Manifest Apply still replaces the target Bot's complete explicit MCP config. It does not change the user row. An explicit empty Bot Header map now means no Bot Header override, so user defaults are inherited; this intentionally changes the previous explicit-empty blocking behavior. Absent config, empty config, MCP installation/removal, and Skill dependency semantics otherwise remain as documented in the Manifest contract.

## HTTP contract

Provide one aggregate command/query in core, exposed through two thin HTTP surfaces:

- Public: `GET`/`PUT /openapi/v1/bots/mcp/servers/{server_code}/config-groups` using the standard OpenAPI envelope and authenticated user identity.
- Internal: `GET`/`POST /api/mcp/config-groups` using the existing internal success/data envelope. GET takes `server_code` as a query parameter; POST includes `server_code` in the body. It uses the authenticated staff user, not the legacy `bot_id=default` parameter.

The editable data is:

```json
{
  "endpoint_env": "PROD",
  "transport_protocol": "SSE",
  "params": [
    {"key": "X-Region", "value": "default", "bots": []},
    {"key": "X-Region", "value": "east", "bots": ["bot-x"]}
  ]
}
```

`params` is required on every write and is the complete Header-rule snapshot. `params: []` clears all user-default and Bot-explicit Header rules for this user/server but preserves API key, Bot URL, and Bot-specific environment/protocol. Omission is invalid. `bots: []` means user default. A Bot may be selected without currently installing the MCP. `endpoint_env` is required (`PROD` or `PRE`); `transport_protocol` is required but may be null to clear the user preference and use the MCP Center default. These semantics apply only to the new aggregate routes; the existing user-only routes keep null-as-unchanged and whole-map write contracts. API key is not accepted or echoed by the aggregate routes.

GET returns the same editable fields plus `server_code`, formed from explicit user and Bot maps; it must not duplicate inherited values into Bot groups. Equal user and Bot values remain separate explicit groups. It may combine identical key/value Bot rules into one group with deterministically sorted Bot IDs. Write responses return the persisted editable view and best-effort per-Bot projection outcomes/summary, without echoing credentials in logs.

Backend validation rejects missing/blank Header names, overlapping Bot scopes for the same case-insensitive name, conflicting values for the same name/Bot, duplicate user defaults for the same name, Bot IDs not owned by the caller, unknown MCP codes, unavailable explicit Center environment/protocol selections, and invalid Header values under existing length rules. An empty string is an explicit Header value; deleting a group removes that rule. The product UI also validates overlaps, but the backend remains authoritative. The first iteration does not support a Bot-specific negative rule to suppress an inherited user Header.

## Control-plane write and projection

The aggregate command validates the complete candidate before mutation, then replaces the user Header map and all owner-qualified Bot Header maps for this server in one real DB transaction, preserving all unrelated fields in both tables. A DB write failure rolls back the entire command. The command does not create an MCP Installation or change SkillSet membership. A Bot config row with no remaining fields may be removed. Concurrent complete-snapshot saves are last-write-wins; the client reloads after success.

After commit, only Bots whose effective MCP set includes this server are projected. Offline/unbound/failed device delivery is reported per Bot and never rolls back accepted control-plane state. Bots with no effective MCP are not probed. The same effective Header resolver serves online projection, full artifact composition, and restart/republication. Existing user-only writes and later Manifest Applies use the same new per-key resolution without requiring a product-form save.

## Compatibility and verification

Before deployment, audit existing Bot `headers` rows, especially `{}`, because the new resolver may expose previously blocked user Headers. No DDL is required. The old `/api/mcp/user/config` and `/openapi/v1/bots/mcp/servers/{server_code}/config` remain user-only and preserve their request/response shapes. New HTTP adapters share one core operation. OCB consumes Avernet through its `ocb-public` gitlink; verify corp DI and E2E with the exact updated gitlink before claiming enterprise integration.

Tests should cover aggregate GET/PUT round-trip through both adapters; one-transaction rollback on injected DB failure; owner, overlap, and Center validation; empty/equal-value/clear behavior; per-key merge and custom-URL isolation; Manifest re-Apply; user-only route compatibility; projection failure retaining control-plane state; and whole-artifact/restart composition.
