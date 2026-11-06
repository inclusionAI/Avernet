# avernet-common

Python code shared by the Avernet services. It is a library, not a service: each
app that needs it installs it into its own venv through a uv path dependency.

```toml
# apps/<app>/pyproject.toml
dependencies = [..., "avernet-common"]

[tool.uv.sources]
avernet-common = { path = "../../middleware/python/avernet-common", editable = true }
```

Then run `uv lock && uv sync` in that app. A Docker image builds this package
into a wheel in its own stage (`uv build --wheel`), syncs the app with
`--no-install-package avernet-common`, then installs the wheel. See
`docker/services/backend.dockerfile`.

CI and the pre-push hook run a consuming app's tests when this directory
changes; add new consumers to `.github/workflows/unit-tests.yml` and
`devops/hook/pre_push.sh` the same way backend is.

Put only code here that more than one service needs and that owns no service
state: wire contracts and their helpers. Business logic stays in the service
that owns it.

## Modules

### `avernet_common.principal_token`

The HS256 envelope of the `X-Avernet-Principal` token (auth design §7.1). The
gateway signs it for an upstream, and services sign it when they call each other.

- `PrincipalSignerConfig(signing_key, issuer, audience, key_id)` describes a
  token being *sent*. `issuer` is the signing component, while `audience` and
  `key_id` are what the receiver requires.
- `PrincipalVerifierConfig(signing_key, audience, issuer, verify_audience=True)`
  describes a token being *received*. `audience` names this component, and
  `issuer` is one trusted signer or a tuple of them.
- `issue_principal_token(claims, signer, ttl_seconds=...)` mints a fresh token:
  it stamps `iss`/`aud`/`iat`/`exp` and sets `kid`.
- `sign_principal_claims(claims, signer)` re-signs existing claims under a new
  envelope: it replaces `iss`/`aud`, sets `kid`, and leaves `iat`/`exp`
  untouched.
- `decode_principal_token(token, verifier)` checks the signature, pinned `alg`,
  `iss`, `aud`, required `exp`/`iat`/`iss`, and expiry with 5s leeway. It
  returns the claims.
- `key_fingerprint` / `is_weak_signing_key` / `MIN_SIGNING_KEY_BYTES` are the
  boot-log diagnostics every component on the contract emits.

Every failure raises `PrincipalTokenError`. The module checks only the
envelope. What the `principals` claim means, and which identity sets a service
admits, is decided by each service (for example, backend's
`core/gateway_principal`).

## Tests

```bash
cd middleware/python/avernet-common
uv sync --frozen
uv run pytest
```
