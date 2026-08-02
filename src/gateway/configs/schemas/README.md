# OpenAPI Schemas

Each file here is an **upstream service's published description**, checked in as
the single-box artifact. The gateway does not hand these to clients directly: it
reads them through the schema catalog and generates the doc it serves
(`core/forwarding/_openapi.py`), narrowing to the public namespace and annotating
each operation with the auth its `route_security` rule requires.

## Current schemas

- `bots.openapi.json` — the backend's public `/openapi/v1` surface. Consumed by
  the `bots` domain.
- `baas.openapi.json` — the BaaS surface. Consumed by the `sessions`, `messages`
  and `runs` domains, which all point at this one file.

Which file a domain reads is the `schema:` block on that domain in
`configs/application.yaml`. A domain with no `schema:` contributes nothing to the
served document — that is why the socket-only `engine` domain has none.

## Regenerating

These are generated artifacts; hand-editing them means the next dump silently
reverts your change. Regenerate from the running upstreams:

```bash
# from src/gateway — dumps every upstream, gates each, publishes on pass
scripts/dump_and_publish.sh

scripts/dump_and_publish.sh --skip baas       # just the backend
scripts/dump_and_publish.sh --dry-run         # dump only, publish nothing
```

Each upstream owns its dump script (`src/backend/scripts/dump_openapi.py`,
`src/baas/scripts/dump_openapi.py`); both emit deterministic, sorted-key JSON so
diffs are reviewable.

## The compatibility gate

Publishing runs through `scripts/gate_and_publish_openapi.py`, which diffs the
candidate against the currently-published file and **fails the release** on any
backward-incompatible change — a removed operation or schema, a parameter that
became required, a dropped property. Clients are already relying on what the
published file promises, so this refuses by default.

For a change that is genuinely intended, pass `--allow-breaking` and **record
the reason in the PR**:

```bash
uv run python scripts/gate_and_publish_openapi.py \
    configs/schemas/bots.openapi.json /tmp/candidate.json --allow-breaking
```

Note that the gate compares against the *last published file*, not against the
previous release — so an artifact left un-regenerated for several merges will
report all of that accumulated drift at once. That is the gate working, not a
new break; read the list before waving it through.

## Deployed editions

A distributed deploy replaces the `source: file` block with `source:
object_store` and a URL, and adds an upload step after the gate. The gate logic
is identical either way; only where the artifact lands changes.
