# Caller IAM multi-target refresh

## Goal

Extend the existing IAM Caller refresh path so one authenticated request can
refresh the eligible `CALLER_SERVICE` runtime for the requested stage and the
current user's active `CALLER_INSTANCE`, while preserving the existing
Session Files runtime-binding resolver.

## Scope

- Reuse `RuntimeBindingResolutionService` for all binding lookups.
- Add an explicit resolver target so a Caller Bot can resolve either its
  shared `CALLER_SERVICE` stage binding or the current user's
  `CALLER_INSTANCE` binding.
- Use the existing Bot collaboration lock snapshot to decide whether the
  current actor may update `CALLER_SERVICE`.
- Update `CALLER_INSTANCE` when the current user has an active Expert Chat
  instance, independently of the service lock.
- Treat the IAM refresh as successful when at least one target is updated.
- Return a controlled error only when no target is available or updated.

## Explicit non-goals

- Do not add an Agent Run or binding-level execution lock.
- Do not change relay/WebSocket/chat.send concurrency behavior.
- Do not change the BaaS append implementation; its existing idempotent
  semantics remain the lower-layer contract.
- Do not accept a client-supplied binding id.

## Existing chain and allowed changes

```text
GET /api/v1/token/iam
  -> CallerIamTokenService
  -> CallerIdentityService / runtime binding resolver
  -> Caller runtime updater (BaaS)
```

Allowed files are the runtime-binding models/service, IAM application service,
its DI wiring, and focused unit tests. The HTTP router remains a thin adapter.
Unrelated relay, engine, BaaS transport, and Session Resource behavior remain
unchanged.

## Target rules

`CALLER_SERVICE` uses the requested IAM stage:

| stage | target |
| --- | --- |
| `draft` | service draft binding |
| `verify` | service verify binding |
| `online` | service online binding |

The service target is eligible when either:

1. a collaboration lock exists and its holder is the current actor; or
2. no collaboration lock exists and the current actor is the Bot owner.

The `CALLER_INSTANCE` target is eligible when the current actor's
`ac_expert_chat_instance` is `success`, its `ext.binding_id` is valid, and the
binding is active and scoped to the current Bot, owner, environment, and actor.

For a Caller Service request, an IAM refresh succeeds when the service target
or the caller-instance target is updated. It fails only when neither target is
updated.

## Security constraints

- Resolve actor identity from the authenticated request context, never from a
  query/body user id.
- Resolve Bot owner and binding scope server-side.
- Never expose binding ids, device ids, IAM tokens, Caller Tokens, cookies, or
  raw outbound headers in responses or logs.
- A service binding must not be updated when another collaborator holds the
  collaboration lock.

## Acceptance checks

- A Caller Bot can resolve `CALLER_SERVICE` explicitly even when it also has a
  Caller Instance.
- An active Caller Instance is resolved independently of the service stage.
- Owner + no lock updates the service target.
- Current lock holder updates the service target.
- Non-holder does not update the service target.
- Caller Instance updates when present, regardless of service-lock outcome.
- One successful target is sufficient for IAM success; zero successful targets
  returns the controlled target-unavailable error.
- Existing Session Files resolver behavior remains unchanged.
