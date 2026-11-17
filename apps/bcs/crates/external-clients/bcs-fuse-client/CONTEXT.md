# bcs-fuse-client Context

## Provides

- Transport-only Fuse v2 Card PUT/DELETE and search contracts.

- Pure HTTP wrapper for the remote BCSFuse service.
- Request/response DTOs for worker sync, worker recommendation, batch lookup, and fusion APIs.
- `FuseClientError` for transport and response parsing failures.
- Typed `RecommendWorkerFilters` on `RecommendWorkersRequest`. The only
  expressible recall filter is the controlled `availability` string list; raw
  provider JSON never enters the client contract. `filters: None` omits the
  field entirely so legacy request bodies stay byte-compatible, while an
  authorized recall sends `availability: [public, protected, private]`.

## Consumes

- `bcs-config-api` for `BcsFuseConfig`.
- HTTP endpoint, timeout, optional Bearer credential, and profile settings supplied by bootstrap or service wiring.

## Allowed dependencies

- `auxiliary/bcs-observability` for log-only operation observations and correlation.

- `bcs-config-api`
- HTTP, serialization, logging, and error helper crates

## Forbidden dependencies

- `bcs-service-api`
- `services/*`
- `adapters/*`
- `bootstrap/bcs`
- direct config file or env discovery

## Runtime ownership

This crate owns BCSFuse transport concerns only. Business implementations that consume this client live in `services/bcs-fusion`.

Worker lifecycle operations use the shared `/v1/workers/*` contract implemented
by both the internal and open-source BCSFuse deployments. Missing-worker
responses may use either the OSS `WORKER_NOT_FOUND` code or the internal
`BCSFUSE-DOM-WORKER-NOT-FOUND` code.

## Tests

- `cargo test --package bcs-fuse-client --manifest-path Cargo.toml`
- `cargo check --package bcs-fuse-client --all-targets --manifest-path Cargo.toml`

## Card write observations

PUT/DELETE log `fuse.card.write_started`, `fuse.card.write_succeeded` and
`fuse.card.write_failed` under `bcs_observation`. Success is logged only after
HTTP status, JSON decoding and saved/deleted acknowledgment checks. Summaries
allowlist resource ID, type, bounded name, skill count, visibility labels, status,
duration and Fuse card_version. They exclude Card details, credentials, source
URLs, raw errors and remote response bodies. Provider/ref and originating request
IDs may be unavailable in detached work; no lookup or propagation is added to
fill them. Existing operation-log and optional tracing contracts are unchanged.
