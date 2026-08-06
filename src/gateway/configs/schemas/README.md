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
- `bcn.openapi.json` — BCN collaboration service OpenAPI spec
- `bcsfuse.openapi.json` — bcsfuse fusion + fusable-query endpoints exposed under `/openapi/v1/bcsfuse`
- `bots.openapi.json` — Bots service OpenAPI spec

## Future: build-time generation

When the pipeline is ready, generated artifacts will land here:

- `single-box/` — produced by the backend `dump_openapi`
- `enterprise/` — pulled from the object store by the schema catalog

Generated directories will be gitignored.
