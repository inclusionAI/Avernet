# OpenAPI Schemas

Schemas are checked in as code, but they are **build outputs, not hand-written**:
each is produced by its upstream's `scripts/dump_openapi.py` and published here
through `scripts/gate_and_publish_openapi.py`, which refuses a
backward-incompatible change unless `--allow-breaking` is passed for a
coordinated one. `scripts/dump_and_publish.sh` runs both ends for every upstream.

Do not edit these files by hand — regenerate them. A hand edit is a description
of a surface the upstream does not serve, and nothing downstream would catch it.

## Current schemas

- `baas.openapi.json` — BaaS service OpenAPI spec
- `bcn.openapi.json` — BCN collaboration service public OpenAPI spec
- `bcn.internal.openapi.json` — BCN collaboration service internal API spec for `/internal-docs`
- `bcsfuse-fusion.openapi.json` — bcsfuse group-fusion endpoint served under `/openapi/v1/bcsfuse/groups/**`
- `bcsfuse-workers.openapi.json` — bcsfuse worker-config + fusable-query endpoints served under `/openapi/v1/bcsfuse/workers/**`
- `bots.openapi.json` — Bots service OpenAPI spec (the backend's public
  `/openapi/v1/bots` surface, narrowed to public paths and the components they
  reference)

## `served/` — the composed document, not an upstream's

`served/gateway.openapi.json` is a different kind of artifact from the files
above. Those are each upstream's description of its **own** surface and are the
catalog *input*; none of them carries auth, because no upstream ever sees a
caller's credential — the gateway mints a signed `X-Avernet-Principal` for them.

`served/gateway.openapi.json` is the document the gateway *composes* from those
inputs and serves at `/openapi.json`: the one a third-party client reads, and
the only place the credential a caller must present is written down
(`components.securitySchemes` + per-operation `security`). Regenerate it with
`scripts/dump_served_openapi.py`, which builds it through the real composition
root so it cannot drift from what a running gateway serves. It reflects *this*
deployment's configured strategies, so a flavor reading a different user-token
header publishes that header here.

`tests/unit/scripts/test_dump_served_openapi.py` fails when the committed copy
goes stale.

## Future: build-time generation

When the pipeline is ready, generated artifacts will land here:

- `single-box/` — produced by the backend `dump_openapi`
- `enterprise/` — pulled from the object store by the schema catalog

Generated directories will be gitignored.
