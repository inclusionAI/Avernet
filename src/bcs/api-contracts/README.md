# BCN API Contracts

`v1/openapi.yaml` is the source of truth for the versioned BCN OpenAPI. Domain
models and resource path items live in separate YAML fragments so a domain can
evolve without creating one monolithic file.

The first implementation batch contains exactly five Group operations:

- `GET /openapi/v1/bots/collaboration/{bot_uuid}/groups`
- `POST /openapi/v1/groups`
- `GET /openapi/v1/groups/{group_id}`
- `PATCH /openapi/v1/groups/{group_id}`
- `DELETE /openapi/v1/groups/{group_id}`

Validate the contract:

```bash
uv run --with pyyaml python src/bcs/scripts/validate_openapi_contract.py \
  --root src/bcs/api-contracts/v1
```

Build a deterministic, self-contained OpenAPI document for Swagger UI, Redoc,
Gateway aggregation, or client generation:

```bash
uv run --with pyyaml python src/bcs/scripts/bundle_openapi_contract.py \
  --root src/bcs/api-contracts/v1 \
  --output-dir /tmp/bcn-openapi
```

Run the contract tests without changing the repository-wide Python
dependencies:

```bash
uv run --with pytest --with pyyaml \
  pytest src/bcs/tests/openapi/test_group_v1_contract.py -q
```

Generated files are build artifacts and are not committed. The candidate YAML
is reviewed before implementation; compatibility checks compare later
revisions against an approved baseline.
