# MCP scoped URL rules

Status: approved product contract; implementation target `REL20261009`.
This extends the existing scoped MCP Header aggregate; see
[ADR 0016](../../adr/0016-mcp-url-and-explicit-headers-are-independent.md).

## HTTP contract

Both existing product entry points retain their methods, authentication and
envelopes:

- Internal: `GET /api/mcp/config-groups?server_code=...` and
  `POST /api/mcp/config-groups`.
- OpenAPI: `GET`/`PUT /openapi/v1/bots/mcp/servers/{server_code}/config-groups`
  with the authenticated personal user's `user_id` query.

Read and write data gain `url_rules: [{"url": "https://example.test/mcp",
"bots": []}]`. An empty `bots` array means user default, and a nonempty
array lists owned Bot IDs. GET returns only explicitly stored rules, with no
inherited Bot copies. It may group Bots sharing an identical URL.

`params` remains a required complete Header snapshot; `params: []` clears
only user/Bot explicit Headers. `url_rules` is optional on write:

- omitted: preserve existing user and Bot URLs;
- `[]`: remove all user and owned-Bot custom URLs;
- nonempty: replace the complete explicit URL snapshot.
Explicit `null` is invalid; it is not an alias for omission or clear.

Both HTTP adapters translate the same request into one Service API command.
Unknown fields still fail validation. Old user-only endpoints retain their
request/response shapes and must preserve the new user URL when updating other
fields.

## Validation and effective state

URLs must be nonempty HTTP(S) addresses. Reject more than one user-default
URL, duplicate/overlapping Bot-specific URL rules, and non-owned Bot IDs.
Global and Bot-specific URLs may overlap: Bot wins. Reject unknown MCP codes,
LOCAL MCPs, and unavailable explicit Center environment/protocol combinations
before mutation, as the existing aggregate does.

The selected URL is Bot > user > Center. The effective user-explicit Headers
merge user + Bot per case-insensitive name regardless of URL source; Bot wins
on a collision. Center-bound legacy `api_key`, platform defaults and
platform-managed credentials are excluded from custom-URL server entries.
The selected `transport_protocol` is not inferred from an arbitrary URL.

Persist the user URL in `ac_user_mcp_config.extra_config.url` and Bot URLs
in `ac_bot_mcp_config.config.url`. The aggregate updates both tables in one
transaction, preserving unrelated fields. DB failure rolls back the command;
per-Bot delivery after commit is best-effort and reported through the existing
`sync_results`/`sync_summary` fields. URL rules do not create installations.

Online device projection and restart/whole-artifact composition resolve the
same URL and Header precedence. Each path reads the user-default row once per
MCP, so its user Header and user URL come from the same snapshot; Bot overrides
are read separately under the existing concurrency semantics. The OCB enterprise build consumes this
Avernet code through its pinned `submodules/avernet` gitlink; source, corp
adapter tests, and runtime readback are separate acceptance stages.
