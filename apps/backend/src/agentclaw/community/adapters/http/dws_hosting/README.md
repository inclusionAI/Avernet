# DWS Management Proof Verification

`POST /internal/dws-hosting/verify-command` checks the exact signed command
issued after the public DWS route's Owner check. It returns only `{"verified":
true}`; it cannot mint proofs, execute operations, or return user data.
The proof itself is the credential. It expires after 30 seconds.

The existing boot-resolved Gateway principal signing secret stays in Backend.
Domain-separated per-Bot/Owner signing keys are never distributed to containers.
No DWS-specific environment secret is required.

Adapter calls only its deployment-configured Backend over verified TLS (loopback
HTTP is allowed for local tests), rejects errors/redirects, checks its local
Bot/Owner scope and timestamp, then consumes the nonce before executing.
Unavailable verification fails closed without changing ordinary chat behavior.

## Context Boundary

```yaml
purpose: Verify short-lived DWS management command proofs without disclosing credentials.
provides:
  - verify_command
consumes:
  - DwsManagementConfig
internal_dependencies:
  - agentclaw.community.di
```

### Change impact

Only the opt-in DWS management Adapter uses this endpoint. Browser routes and
their Owner-only authorization are unchanged. Old per-container HMAC deployments
must update the DWS Adapter alongside this Backend change.
