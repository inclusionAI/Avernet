# Gateway Principal Contract for BCS V1

`X-Avernet-Principal` carries one raw compact JWT. The verifier requires
`alg=HS256`, `typ=JWT`, `kid=bare`, an `iss` claim matching any configured
issuer (defaults `gateway` or `backend`), `aud=bcs`, integer `iat` and
`exp`, and a non-empty `principals` array. It allows one each of `user`, `bot`,
`app`, and `access_key`.

The outer `tenant` of a `user` Principal is optional and may be a non-blank
string, JSON `null`, or absent. BCS does not fabricate a tenant for a
tenantless User. The outer `tenant` of `bot`, `app`, and `access_key`
Principals remains a required non-blank string. Every outer tenant that is
present must agree; therefore a tenant-bearing Bot/App/AccessKey may establish
the normalized tenant when it accompanies a tenantless User. A User
`subject.tenant_id` is optional identity metadata: it must be non-blank and
equal the outer User tenant when both are present, but it does not establish
the caller tenant when the outer field is null or absent.

Known Principal types may add fields compatibly. Unknown Principal types,
duplicate types, removed required fields, mixed tenants, invalid time claims,
and invalid signatures fail the whole request. BCS never projects `bot.token`
or `access_key_token` into its internal caller.

Verification warnings correlate failures with the first 16 hexadecimal
characters of SHA-256 over the complete compact JWT. They may report an exact
schema path such as `principals[0].tenant`, but must not log any compact-JWT
segment, decoded payload, signature, signing key, credential, or claim value.

This contract is preparatory: BCS V1 is not production-mounted by this change.

## Authority-mounted consumer routes

The Bot owner/manager model consumes the User Principal through the
following mounted surfaces:

- The manager, ownership and transfer route families under
  `/openapi/v1/collaboration/bots/...` (`mine`, `{bot_id}/managers[/{user_id}]`,
  `{bot_id}/ownership[/-transfers...]`) accept ONLY a Human Principal. A
  Bot/App/AccessKey-only Principal set is rejected `403` without an
  authority read; a User Principal is necessary but NOT sufficient —
  management rights resolve from the CURRENT role facts (the approved owner
  edge or an active manager source) in the server's authority store,
  re-verified per request. No `created_by`, legacy `is_creator`, Bot-id
  suffix, or signed owner claim substitutes for that live decision.
- The trusted-platform team-manager slice
  (`PUT /api/v1/bots/{bot_id}/manager-sources/teams/{team_id}` and its
  member-repair routes) does NOT authenticate a Gateway Principal at all:
  it requires the platform's service credential (`Authorization: Bearer`
  of a purpose-bound `team_manager_sync` credential) and exists only when
  the deployment's `[team_manager_sync]` section resolved its signing key
  at startup. Missing credentials answer `401`, unverifiable or
  out-of-scope credentials answer `403 invalid_manager_sync_source`.
- The Workbench WebSocket (cookie identity) and the group-session
  connection-token router bind the verified Human to the selected view;
  every protected frame re-authorizes that binding against the live
  authority facts at enqueue time and again immediately before the socket
  send. A revoked binding drops its protected backlog without closing the
  connection's public control lane.
