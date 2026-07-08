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
