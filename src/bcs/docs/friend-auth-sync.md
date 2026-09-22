# TC friend authorization synchronization

## Scope

Only the existing edge-permission Human → TC Bot friend lifecycle is mirrored:

- auto-approved connect → grant;
- manual connect approval → grant;
- unfriend through `ConnectService::revoke_friend` → revoke.

Bot ↔ Bot friendship is not mirrored. Legacy friend flows, historical backfill,
reconciliation and durable retries are outside this change.

## Identity and transport contract

BCS keeps the complete actor ID in its friend edges. The TC HTTP adapter accepts
an actor address consisting of exactly two non-empty, whitespace-free components:
`bot_id:workNo`. This is the TC address convention supplied by the caller contract;
it is not an independent provider-identity verification mechanism.
Non-matching native Bot IDs and malformed addresses are skipped without HTTP IO.

For `human_88123` befriending `abc123:85020`, TC receives:

```json
{
  "bot_id": "abc123",
  "owner_work_no": "85020",
  "human_work_no": "88123",
  "action": "grant"
}
```

The owner is the address suffix, not the legacy `created_by` metadata. Revoke uses
the same conversion. Grant keeps the existing optional request ID; revoke can
omit it. No database keys or edge identities are rewritten.

The configured `friend_work_order_base_url` selects the existing TC HTTP adapter;
without it the existing no-op wiring remains. The adapter calls
`POST /api/internal/bot-friend-auth/sync`, forwards only the existing gateway
principal and trace headers, and applies a 10-second request timeout.

Only a successful HTTP response with `synced: true` is acknowledged as synced.
HTTP errors, invalid responses and negative acknowledgements return port errors.
The existing application callers still log these errors and keep the local friend
operation successful (best effort); no automatic retry is introduced.
TC must report a failed authorization deletion as a sync error, not `deleted`.

## Verification and limitations

Adapter regression tests exercise actual loopback HTTP requests for grant,
revoke, malformed/non-TC skips and failed acknowledgements. Backend service
regressions cover a deletion plugin returning `False`.

The internal endpoint accepts only BCS calls carrying a principal that passes the
Backend gateway-principal verifier. A verified principal is the internal-call
boundary and is used for authentication and audit; missing or invalid principals
are rejected. BCS forwards this verified principal, while OpenAPI Authorization and
Cookie credentials are intentionally not forwarded.

This change does not redesign principal verification, serialize concurrent
grant/revoke calls, add persistent compensation, or integrate legacy authorization
rebuilds.
