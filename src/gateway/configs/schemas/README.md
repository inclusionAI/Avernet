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
- `bots.openapi.json` — Backend public OpenAPI spec (currently the
  `/openapi/v1/bots`, `/openapi/v1/org/user`, `/openapi/v1/org/dept`,`/openapi/v1/org`, `/openapi/v1/bots/spaces`,
  `/openapi/v1/bots/work-orders`, and `/openapi/v1/bots/work-order-notifications`
  surfaces, narrowed to public paths and the components they reference)

## Future: build-time generation

When the pipeline is ready, generated artifacts will land here:

- `single-box/` — produced by the backend `dump_openapi`
- `enterprise/` — pulled from the object store by the schema catalog

Generated directories will be gitignored.
