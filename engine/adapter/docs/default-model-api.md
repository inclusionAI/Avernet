# Bot default model — engine API contract

**Status:** engine side implemented for `openclaw` and `claude_code` (community
engines in this repo). Corp engines (`aicoding` / TeamClaw relay, `hermes`,
`deepseek_harness`) implement the same contract on their side; §4 records where
each engine keeps the value.

## 1. Why

`/api/models` lists every model the bot's engine can route to, but a picker
also needs to know which one is *in effect* when nothing is selected. That value
lives in engine-specific configuration (OpenClaw's `openclaw.json`, Claude
Code's `settings.json`, …), so the backend asks the engine adapter rather than
guessing. This document is the contract between the backend (and TeamClaw /
AICoding, who consume the same relay protocol) and every engine adapter.

## 2. HTTP contract (engine adapter, port 20003)

```
GET /api/models/default
```

Declared **before** `GET /api/models/{model_id:path}`, so the literal segment
`default` is never treated as a model id. Takes no parameters. Capability gate:
`model.default.get` (`Capability.MODEL_DEFAULT_GET`).

### 200 — a default is configured

```json
{
  "success": true,
  "data": {
    "id": "openai-compatible/glm-5.2",
    "provider": "openai-compatible",
    "provider_id": "glm-5.2",
    "name": "GLM 5.2",
    "display_name": "GLM 5.2",
    "description": null,
    "enterprise_enabled": true,
    "enterprise_default": false,
    "capabilities": { "context_window": 200000, "max_output_tokens": 4096, "vision": false,
                       "function_calling": true, "reasoning": false, "streaming": true, "json_mode": true },
    "source": "openclaw.config.get",
    "source_detail": "/home/admin/.openclaw/openclaw.json#agents.defaults.model",
    "fallbacks": ["openai/gpt-4"]
  },
  "message": null,
  "warning": null
}
```

`data` is **one `/api/models` entry plus three default-only keys**, so a client
maps both responses with the same function and pre-selects the picker by `id`
equality:

| Key | Type | Meaning |
|---|---|---|
| `id`, `provider`, `provider_id`, `name`, `display_name`, `description`, `capabilities`, `pricing`, `enterprise_*`, … | as in `/api/models` | When the configured id matches a catalogue entry, this *is* that entry (same display name / capabilities). Otherwise a minimal entry synthesised from the configured id (`name` / `display_name` = the bare model segment). |
| `source` | string | Short, stable, engine-specific token naming where the value was read. Current values: `openclaw.config.get`, `openclaw.json`, `env`, `settings.json`, `model-provider-settings-source`, `fallback`, `local`. New engines add their own; clients must not enumerate. |
| `source_detail` | string \| null | Human-readable location for debugging: `<path>#<key>` for a file, the env var name, or a free-text note. |
| `fallbacks` | string[] | The engine's configured fallback chain, in order, when it has one (OpenClaw `agents.defaults.model.fallbacks`). Empty otherwise. |

### Other answers

| Status | When | Body |
|---|---|---|
| 404 | the engine has no explicit default configured (OpenClaw with `agents.defaults.model` unset) | `{"detail": "No default model configured"}` |
| 501 | the active engine does not declare `model.default.get` | `{"detail": "Capability 'model.default.get' is not supported by engine '<name>'"}` |
| 200 + `success: false` | the engine was asked but failed (gateway down, RPC error) | `{"success": false, "message": "获取默认模型失败: <cause>"}` — same shape `/api/models` uses |

A `warning` string is attached when the engine declares the capability as
`limited`.

## 3. Plugin contract (inside the adapter)

```python
class ModelsService(Protocol):
    async def get_default_model(self, auth: AuthContext | None = None) -> DefaultModel | None: ...

class DefaultModel(BaseModel):
    model: Model                 # catalogue entry or synthesised minimal entry
    source: str
    source_detail: str | None = None
    fallbacks: list[str] = []
```

`None` ⇒ 404. Upstream failures raise and surface as `success: false`. Each
engine's native port returns a primitive dict; the ACL adapter matches it
against `list_models()` (`find_catalogue_model`: exact id, then bare id) and
builds the DTO. The HTTP layer never reads engine config itself.

## 4. Where each engine keeps its default model

| Engine | Source of truth | Key | How this adapter reads it | Status |
|---|---|---|---|---|
| **OpenClaw** | `openclaw.json` (`OPENCLAW_CONFIG_PATH` → `$OPENCLAW_STATE_DIR/openclaw.json` → `~/.openclaw/openclaw.json`) | `agents.defaults.model.primary` (+ `.fallbacks`), or `agents.defaults.model` as a plain `provider/model` string | Gateway RPC `config.get` (live snapshot: `config` runtime-shaped → `resolved` → `parsed`), file fallback when the RPC fails. `source` = `openclaw.config.get` / `openclaw.json`. Per-agent `agents.list[*].model` overrides are **not** consulted (Avernet runs one agent per gateway). | ✅ implemented |
| **Claude Code** (community relay `claude_code_gateway`) | the relay's resolution chain | `RELAY_DEFAULT_MODEL` env → `<CLAUDE_CONFIG_DIR>/settings.json` `env.ANTHROPIC_MODEL` → `RELAY_MODEL_SETTINGS_SOURCE` file `env.ANTHROPIC_MODEL` → hard-coded `claude-sonnet-4-5` | New relay RPC `models.default` → `{ model, source, path? }` (see `claude_code_gateway/docs/websocket-protocol.md` §10.4). Always answers; `source` ∈ `env` \| `settings.json` \| `model-provider-settings-source` \| `fallback`. | ✅ implemented |
| **AICoding / TeamClaw relay** (corp) | same v3 relay protocol as Claude Code | — | Implement the `models.default` RPC with the payload in §5; the Python side (`ClaudeCodeModelsPort.models_default` → adapter) is reusable as-is. | ⏳ their side (excluded from this change by request) |
| **Hermes** (corp engine) | `$HERMES_HOME/config.yaml` (default `~/.hermes/config.yaml`) | `model.default` (+ `model.provider`, `model.base_url`, `model.api_mode`); fresh installs ship `model: ""` until `hermes setup` / `hermes model` runs | Read the YAML, or shell out to `hermes config get model`, which prints the resolved value. Precedence per Hermes docs: CLI args > `config.yaml` > `.env` > built-ins. | 📍 located, not implemented (engine lives in the corp tree) |
| **DeepSeek Harness (DSH)** (corp engine) | `$DSH_HOME/profiles/<profile>/cordis.patch.yml` (default `~/.dsh/profiles/<profile>/`; Avernet's BCN installer uses profile `web`) | the Cordis row `id: agent-default-model` → `config.provider` + `config.model` | Parse the patch list for that row, or run `dsh --profile <p> --dump-config` for the composed profile (the same command the BCN installer uses to validate writes). | 📍 located, not implemented (engine lives in the corp tree) |
| **Codex** (not an Avernet engine; listed because singlebox's Bailian compat configures it) | `~/.codex/config.toml` | `model` + `model_provider` | — | n/a |

Evidence for the corp-engine rows: `singlebox/compat/bailian/configure.py` writes
every engine's default-model knob for the live CI smoke (OpenClaw
`agents.defaults.model.primary`, DSH `agent-default-model`, Claude
`env.ANTHROPIC_MODEL`, Codex `model`); `apps/bcs/crates/plugins/deepseek-harness-channel-bcn/src/configure.ts`
resolves `DSH_HOME`/profiles; Hermes keys are from the upstream Hermes Agent
configuration docs (`hermes-agent` repo, `website/docs/user-guide/configuration.md`).

## 5. Relay RPC contract (for Claude-Code-style relays: TeamClaw / AICoding)

```
→ { "type": "req", "id": "…", "method": "models.default", "params": {} }
← { "type": "res", "id": "…", "ok": true,
    "payload": { "model": "claude-sonnet-4-5", "source": "settings.json", "path": "/home/admin/.claude/settings.json" } }
```

| Field | Type | Rule |
|---|---|---|
| `model` | string, required | Same id namespace as `models.list` entries (bare ids are fine). |
| `source` | string, required | Short token naming the step of the relay's resolution chain that won. |
| `path` | string, optional | Absolute path of the file the value came from; omit for env / fallback. |

Resolve on every call (do not cache at boot) so a settings edit shows without
a relay restart. Answer `ok: false` only if the relay truly has no default; the
adapter maps that to 404. Relays that predate the method answer with an unknown
method error; the adapter treats that as "no default" too.
