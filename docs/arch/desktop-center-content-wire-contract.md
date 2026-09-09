# Desktop Center Content Wire Contract

This contract extends the existing Backend-to-Engine
`POST /api/skills/mappings/apply` command. It does not add a product HTTP API,
change Mapping v2/v3 identity, or change cloud mount ownership.

## Request v1

`center_content` is present only when the owner-qualified `DeviceContext` says
the target Bot is `desktop` and the current desired mappings contain Center
items. The same resolved `DeviceContext` must be used for classification and
the Engine invocation.

```json
{
  "mappings": [],
  "retired_mappings": [],
  "source_layout": "legacy",
  "center_content": {
    "contract_version": 1,
    "packages": []
  }
}
```

Each package is keyed by the desired Mapping v3 identity
`skill_uuid + sc_version_number` and has exactly one state:

- `READY`: requires `package_sha256`, `package_size`, `signed_url`, and
  `expires_at`.
- `PENDING`: has no URL, digest, or error fields.
- `UNAVAILABLE`: requires a stable `code` and `retryable` boolean and has no
  URL or digest fields.

Packages are deduplicated by exact identity. Descriptors outside the desired
Center mapping set, duplicates, unknown versions, missing required fields, and
state-field mixtures are invalid requests. Retired-only identities are never
packaged or signed.

## Response evidence

A Downloaded content Adapter that actually interprets v1 reports:

```json
{
  "evidence": {
    "center_content": {
      "contract_version": 1,
      "mode": "DOWNLOAD",
      "ready": 1,
      "pending": 0,
      "unavailable": 0,
      "packages": [
        {
          "skill_uuid": "00000000-0000-4000-8000-000000000001",
          "sc_version_number": "7",
          "status": "READY"
        }
      ]
    }
  }
}
```

The Router must not echo this evidence mechanically. Backend requires the
exact desired identity set once each, with each content status agreeing with
the corresponding content preparation result. `PENDING` content requires a
pending mapping and `UNAVAILABLE` content requires a degraded mapping. `READY`
content may still have a pending or degraded mapping because cache readiness
does not prove that the active link was changed. Missing, duplicate,
contradictory, or wrong-mode evidence degrades the projection.
`UNAVAILABLE` with `retryable=true` becomes a pending mapping issue;
`retryable=false` becomes degraded, so the recovery caller can reschedule only
work that can make progress.

## Delivery and compatibility

- Cloud and other mounted runtimes receive no `center_content`; the Mounted
  Adapter preserves existing mount inspection and mapping behavior.
- A downloaded runtime selects its Adapter from runtime-owned agentbox
  configuration, never from request fields.
- A cold distribution package is looked up without reading Canonical content.
  The explicit `prepare(exact identity)` Interface performs deterministic ZIP
  creation and immutable publication outside the mounted Canonical namespace.
- Signed GET URLs last 3600 seconds and are transient request data. They must
  not be stored in task payloads or logged from validation responses. The URL
  signer is a separate injected capability from the object writer so Corp can
  sign directly for its Desktop-reachable HTTPS endpoint without changing the
  cloud write endpoint or rewriting an already-signed URL.
- A cache miss starts one bounded, deduplicated background preparation and the
  current apply reports that exact identity as pending. The background worker
  only publishes the verified cache; a later apply with the current desired
  snapshot creates the active link. Slow downloads therefore do not hold the
  mapping transaction lock or block unrelated Local/Repo mappings and exits.
  Engine reconstruction in the same runtime process reuses that coordinator,
  so restart cannot multiply the concurrency budget or duplicate an in-flight
  exact download. Process exit may interrupt daemon preparation; atomic cache
  publication keeps partial content invisible and a later apply starts or
  reuses preparation again.
- The Engine downloads directly over HTTPS, verifies compressed size and
  SHA-256, rejects unsafe paths and archive symlinks, bounds entry count and
  expanded bytes, requires a non-empty root `SKILL.md`, and atomically exposes
  a complete cache directory.
- Downloaded Adapter selection happens in the Engine composition root. Public
  Desktop deployments must set `ENGINE_CENTER_CONTENT_ALLOWED_HOSTS` to the
  exact comma-separated object hosts they trust; deployment-owned internal
  defaults may supply the same typed setting. Wildcards, URL userinfo,
  non-HTTPS ports, redirects, and hosts resolving to non-public addresses are
  rejected before package bytes are requested. The default client ignores
  environment proxies and connects to an address from that validated DNS set
  while retaining the original hostname for HTTP Host and TLS SNI/certificate
  verification. Temporary DNS failure remains retryable.
- Cache readiness and mapping activation are distinct. A pending replacement
  retains the previous link; a cache completion callback never activates it.
- An explicit Center retirement may remove a retained older exact version only
  when the active symlink is managed under the same Center `skill_uuid`; it
  never removes a user directory or a different Center identity.
- An old standard route may retain the existing health-confirmed legacy
  fallback only for requests without this extension. A request containing v1
  is never stripped, split, or retried through the old write protocol.

Conformance coverage lives in Backend distribution/runtime tests, Engine HTTP
and Adapter contract tests, and OCB Corp wrapper/DI tests. Passing repository
tests is not evidence of deployment or a real Desktop installation.
