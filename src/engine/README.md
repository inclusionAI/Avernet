# OpenClaw Engine — Community (Open-Source) Distribution

This is the open-source **community** build of the OpenClaw engine adapter: a
Python/FastAPI service that exposes a uniform WebSocket + HTTP surface and
translates it to an AI coding engine (claude_code, openclaw) over an
Anti-Corruption Layer. It ships **without** any internal (corp) code — the
`engine/corp/` tree is physically absent from this distribution, and the
community runtime imports zero corp modules.

This README is staged to the distribution root by
`scripts/build_community_dist.py` so the assembled community tree `uv sync`s and
builds standalone (the `pyproject.toml` here declares `readme = "README.md"`).

## Run it

See the full getting-started guide (both engines, config, troubleshooting):

- [`docs/open-source-guide.md`](../../../docs/open-source-guide.md)
- 中文版：[`docs/open-source-guide.zh-CN.md`](../../../docs/open-source-guide.zh-CN.md)

Quick start (claude_code, the out-of-the-box engine):

```bash
pip install .            # or: uv sync
# start the vendored gateway (community/claude_code_gateway), then:
CHAT_ENGINE=claude_code ENGINE_PROFILE=community python start.py --port 20003
```

## Layout

```
src/engine/community/    # api → core → plugin_api → kernel + community impls
  ├── api/               # FastAPI app, WS endpoints (/api/{engine}/ws)
  ├── di/                # composition root (community profile)
  ├── engines/           # claude_code + openclaw (WS clients + ACL)
  ├── plugins/           # transport leaves
  └── claude_code_gateway/  # vendored Node gateway for claude_code
```

## Engine capability matrix

Each engine declares an `EngineCapabilities` (`core/engine/capability.py`).
`docs/engine-capability-matrix.md` is generated from those declarations — never
edit it by hand:

```bash
# print the current matrix (markdown / json / csv)
python scripts/gen_capability_matrix.py
python scripts/gen_capability_matrix.py --format json

# refresh the checked-in doc after changing an engine's capabilities
python scripts/gen_capability_matrix.py --source static \
  -o docs/engine-capability-matrix.md
```

The generator reads the live declarations when the engine's dependencies are
installed and falls back to an `ast` scan of the tree otherwise, so it also runs
in a bare checkout. A test keeps the checked-in doc from going stale.

The repo carries a second, unrelated per-engine declaration: each adapter under
`src/frontend/src/adapters/engine/` declares a `BotFeatures` record that gates
the UI. `--target frontend` tabulates that one — it covers every adapter the
frontend factory registers, including engines whose backend packages are not in
this tree:

```bash
python scripts/gen_capability_matrix.py --target frontend
python scripts/gen_capability_matrix.py --target all        # both matrices

# refresh the checked-in frontend doc
python scripts/gen_capability_matrix.py --target frontend \
  -o ../frontend/docs/engine-feature-matrix.md
```

The two vocabularies are kept in separate tables on purpose: the engine's
`Capability` enum and the frontend's `BotFeatures` flags describe different
things, and a merged grid would imply a mapping that does not exist.
