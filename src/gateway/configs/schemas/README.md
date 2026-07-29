# OpenAPI Schemas

Schemas are checked in as code. No build-time generation pipeline exists yet.

## Current schemas

- `baas.openapi.json` — BaaS service OpenAPI spec
- `bots.openapi.json` — Bots service OpenAPI spec

## Future: build-time generation

When the pipeline is ready, generated artifacts will land here:

- `single-box/` — produced by the backend `dump_openapi`
- `enterprise/` — pulled from the object store by the schema catalog

Generated directories will be gitignored.
