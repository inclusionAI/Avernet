# Internal task reads: verified application callers

Approved by Shangjian on 2026-11-06 after the prepub regression review.

## Contract diff

The existing GET paths `/api/v1/collaboration/tasks/dashboard`,
`/api/v1/collaboration/tasks/{task_id}`, `/api/v1/collaboration/tasks/artifacts`,
and `/api/v1/collaboration/tasks/list` keep their query and response schemas.
They accept either the existing user-cookie authentication or an internal
application principal verified by the existing gateway-principal verifier.
`list` is the existing owner-metadata source used by the identity regression;
trajectory is not an owner-metadata or graph-output response.

No Relay report, state transition, object-file write, grant/revoke operation,
public API admission table or signing protocol changes.

## Authentication and isolation

- The caller presents its registered app credential to the configured gateway,
  using its existing `X-Avernet-App-Token` strategy. The gateway authenticates,
  strips/replaces caller-supplied principal headers and forwards a signed
  `X-Avernet-Principal` for `aud=backend`.
- Reuse `resolve_caller`: signature, issuer, audience, expiry and principal
  parsing are checked by the existing verifier. The backend never signs ECB
  identity and ECB must not receive the gateway signing key.
- A service caller must contain an app principal and belong to the internal
  `DEFAULT_AVERNET_TENANT`. Internal routes run under that tenant already;
  an external tenant must never fall through to internal data.
- A supplied invalid principal is rejected with 401, including when a valid
  cookie is also present. No downgrade after failed verification.
- A verified user-only browser principal still uses existing cookie auth.
  A user principal without a valid cookie is not treated as a service app.
- No header, an arbitrary app name or direct unauthenticated HTTP does not
  authenticate a service. Writes retain their existing authentication.

## Caller/deployment requirements

Deploy both the backend read-auth change and the ECB caller changes. The
operator must supply an existing registered internal app credential and the
correct authenticated gateway origin through secret/config management.
Do not copy browser cookies or signing secrets into regression YAML.
Missing configuration blocks affected ECB cases; it does not pass them.

The expired dispatch generation is an independent fixture issue: provisioning a
live isolated task/generation is required for the positive dispatch regression.
Do not rotate an old generation, replay a real task, or accept 409 as success.

## Verification

The task HTTP adapter and existing principal-seam suites pass (364 tests). New route tests use signed
principal tokens and cover valid app/no cookie, user cookies, forwarded browser
users, forged/expired/wrong issuer/wrong audience tokens, external tenants,
missing signing key, unsigned identity hints and unchanged write authentication.
Tests do not use live credentials, invoke real tasks or claim deployment success.
