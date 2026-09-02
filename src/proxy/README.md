# sandboxproxy-community

sandbox-proxy community edition — a standalone reverse proxy that delegates
client traffic to Aliyun ACK pods, hiding the pods behind a single authenticated
entry point.

```
client ──▶ sandbox-proxy ──▶ aliyun_ack pods (BaaS / engine / sandbox)
```

Parity with the internal `sandboxproxy` for the aliyun_ack delegation path only:
JWT auth, `ARCA_`/`TECLAW_`/`LOCAL_` target resolution, HTTP + WebSocket
forwarding, and relay-session route management. Pure Python (FastAPI), no
nginx/OpenResty, no SOFA/MOSN/Layotto.

## Quick Start

```bash
# Install dependencies
uv sync

# Run in bare mode (standalone, no sidecar)
python src/sandboxproxy/community/main.py --mode bare --config configs/
```

## Runtime Modes

| Mode | Description |
|------|-------------|
| `bare` | Standalone FastAPI server, no MOSN/Layotto sidecar (the only open-source mode) |

## Architecture

```
src/sandboxproxy/community/
├── spi/          # Protocol contracts (TargetResolver, RelayApiClient, forwarder, jwt-verifier)
├── core/         # Domain logic — authn, forwarding, relay
├── plugins/      # Concrete implementations selected via config (bare/stub/noop)
├── adapters/     # FastAPI app + WebSocket routes
├── bootstrap/    # Composition root — DI container wiring + lifecycle
├── config/       # Config loader + typed models
└── main.py       # Entry point (--mode bare)
```

The pattern mirrors `gateway-community`:

```
SPI Protocol  →  Bare/Stub Implementation  →  PluginContainer Selector
```

Plugins select from `user_config.plugins.*` in `application.yaml`:

| Selector     | Config Key             | Bare Default | Purpose                              |
|--------------|------------------------|--------------|--------------------------------------|
| `resolver`   | `plugins.resolver`     | `bare`       | Target resolution (ARCA/TECLAW/LOCAL) |
| `relay_client` | `plugins.relay_client` | `bare`       | Upstream BaaS relay-sessions HTTP client |

## Configuration

See `configs/application.yaml`. Environment-specific overlays live in
`configs/overlays/`.

| Variable | Purpose |
|----------|---------|
| `SERVER_ENV` | Environment overlay (dev/prod/etc.) |
| `COMMUNITY_DEPLOY` | When set, its value replaces `SERVER_ENV` for naming the `application-<value>.yaml` overlay (community deployments set `COMMUNITY_DEPLOY=community`) |
| `SOFAPY_CONFIG_OVERLAY` | Named overlay `configs/overlays/{name}.yaml` |
| `SANDBOXPROXY_CONFIG_PATH` | Explicit config path (dir or file) |
| `SANDBOXPROXY_PORT` | Override listen port |

## Development

```bash
just lint          # ruff + mypy/pyright
just format        # ruff format
just test-ut       # unit tests
just test-it       # integration tests
just test-arch     # architecture tests
just test-e2e      # end-to-end tests (in-process ASGI)
just test-docker   # docker image build + prod-mode health probe
just test          # full CI pipeline
```

`scripts/ci_test.sh` enforces the changed-line coverage gate (threshold 90).