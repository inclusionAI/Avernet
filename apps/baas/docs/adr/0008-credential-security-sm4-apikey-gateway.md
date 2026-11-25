# ADR-0008: Credential security — SM4 encryption at rest and API-key gateway

## Status

accepted

## Date

2026-10-26

## Applies to

all

## Context

BaaS stores third-party platform credentials (sandbox/PaaS API keys, tokens)
to act on behalf of tenants, and exposes an external-call API for
programmatic bot invocation. Credentials at rest and unauthorized API calls
are the two dominant security surfaces. Compliance requires the national
GMSSL standard (SM4) inside the company, while the OSS build must ship a
safe, dependency-free default.

## Decision

Split credential handling into two independent decisions:

1. **At rest**: credential storage goes through the `secret` SPI
   (`spi/secret`). The enterprise option implements GMSSL/**SM4**
   encryption/decryption in `secret/real` (the same SM4 utilities the legacy
   monolith used); the community ships a deterministic `stub`/`env` secret
   suitable for local development. Provider credentials are never stored or
   logged in plaintext by `core/` services.
2. **In transit / at the door**: external programmatic calls enter through
   the API gateway domain (`api/api_gateway` + `core/service/api_gateway`):
   callers authenticate with **API keys** whose secret material is verified
   via `_jwt.py` and scoped by `_permission.py` / `_policy.py` under
   per-tenant resource keys (`_resource_key.py`); keys are activated,
   deactivated, and revoked through the key lifecycle API.

The two mechanisms are deliberately decoupled: rotating the SM4 key set never
affects API-key validation, and vice versa.

## Consequences

**Positive:** SM4 compliance is isolated in one replaceable plugin; the OSS
build has no crypto burden; external access is policy-scoped instead of
network-hoping on trust; revocation is an API call, not a redeploy.

**Negative:** two security mechanisms to operate; stub secret in community
means local dev data is not strongly protected (documented, accepted for
non-production data).

**Risks:** a future backend bypassing the gateway would create an unauthenticated surface — reviewed changes must keep `api_gateway` the only external door for bot invocation.

## Evidence

- community: `src/secbaas/community/spi/secret/` — secret contract
- community: `src/secbaas/community/plugins/secret/stub/` — community secret options
- community: `src/secbaas/community/api/api_gateway/` — `_jwt.py`, `_permission.py`, `_policy.py`, `_resource_key.py`
- community: `src/secbaas/community/core/service/api_gateway/` — gateway service domain
- enterprise: `src/secbaas/enterprise/plugins/secret/real/` — SM4 (`GMSSL`) option