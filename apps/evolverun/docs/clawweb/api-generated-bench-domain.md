# Generated Bench Domain task context

`POST /api/bench/domains` retains its existing payload and Skill transport. An
upgraded Plan uploader may additionally supply these two optional string fields:

- `sourceTaskId`: the evolution task producing the Domain.
- `sourceStepId`: the Plan Step producing the Domain.

Supply both or neither. Do not send a space identifier as authority. These IDs
are resource references, not authentication credentials: the server still uses
the existing authenticated Skill channel, verifies the runtime Owner and the
Plan Step/task relationship, and reads the persisted task configuration.

Only tasks tagged by the server-side space-aware creation adapter with
`config.entryPoint = "teamclaw"` enter the new binding path. The marker is not a
space snapshot and must not be copied from arbitrary browser or Skill input.
The Host resolves the exact Bot Owner, environment and tenant to its current
structured space assignment. Unassigned Bots use the runtime Owner's real
personal space from the existing space service; missing/ambiguous assignments
fail rather than producing a synthetic personal identifier.

The first successful Domain creation stores the resolved ID in
`cm_bench_domains.space_id`. The Owner/Domain namespace is unchanged. A retry
returns the existing 409 conflict; the normal uploader may verify/reuse that
Domain, but must not reassign its space when the Bot moves. ZIP upload, publish,
callbacks and their URLs remain unchanged. No additional tables are introduced.

Compatibility:

- Requests without context use the original handler.
- Valid context referring to a legacy task also uses the original handler.
- Incomplete context is 400; missing identity is 401; foreign/mismatched source
  references are 404. An unavailable Host space lookup fails closed.
- Source context does not grant access to other users' tasks or override legacy
  authorization. This adapter is not a replacement for transport authentication.
- The nullable column migration must be applied before enabling space-aware
  creation in a managed database. Existing legacy queries and INSERTs do not
  require the new column; a failed new write never falls back to personal.

This is the generated-Domain adapter only. The space-aware task creation routes,
resource listing/authorization and frontend integration are separate V2 work;
this adapter alone does not enable or complete those features.
