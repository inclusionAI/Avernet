# Bot default model — engine API contract

**Status:** engine side implemented for `openclaw` and `claude_code` (community
engines in this repo), and the public Open API wrapper (§2b) in the backend. Corp engines (`aicoding` / TeamClaw relay, `hermes`,
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

### 200 — the engine's default

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
    "fallbacks": ["openai/gpt-4"]
  },
  "message": null,
  "warning": null
}
```

`data` is **one `/api/models` entry plus `fallbacks`**, so a client maps both
responses with the same function and pre-selects the picker by `id` equality.
Where the engine read the value from is deliberately not exposed (it is an
implementation detail; the adapter logs it at debug level):

| Key | Type | Meaning |
|---|---|---|
| `id`, `provider`, `provider_id`, `name`, `display_name`, `description`, `capabilities`, `pricing`, `enterprise_*`, … | as in `/api/models` | When the configured id matches a catalogue entry, this *is* that entry (same display name / capabilities). Otherwise a minimal entry synthesised from the configured id (`name` / `display_name` = the bare model segment). |
| `fallbacks` | string[] | The engine's configured fallback chain: the models it tries, in order, when the default fails (OpenClaw `agents.defaults.model.fallbacks`). Empty for engines without such a chain. **Pending review decision** on whether to keep this key. |

### Other answers

| Status | When | Body |
|---|---|---|
| 200 + `success: false` + `error: "default_model_not_configured"` | the engine cannot name a default (OpenClaw with `agents.defaults.model` unset; a relay too old to answer). Every engine is expected to have one, so this is an engine misconfiguration flagged by the `error` discriminator, not a 404 | `{"success": false, "error": "default_model_not_configured", "message": "引擎未配置默认模型: <cause>"}` |
| 200 + `success: false` | the engine was asked but failed (gateway down, RPC error) | `{"success": false, "message": "获取默认模型失败: <cause>"}` — same shape `/api/models` uses; `error` is null |
| 501 | the active engine does not declare `model.default.get` | `{"detail": "Capability 'model.default.get' is not supported by engine '<name>'"}` |

A `warning` string is attached when the engine declares the capability as
`limited`.

## 2b. Public Open API (backend)

```
GET /openapi/v1/bots/{bot_id}/models/default?stage=draft|live
```

Same gating as `GET …/models` (owner and member-level collaborators of the
addressed bot, device-wide). The backend relays to the engine route above and
answers `Envelope[DefaultModel]`, where `DefaultModel` is the public `Model`
(`model_id`, `name`, `provider`) plus `fallbacks`:

```json
{ "success": true, "data": { "model_id": "openai-compatible/glm-5.2", "name": "GLM 5.2",
                             "provider": "openai-compatible", "fallbacks": ["openai/gpt-4"] } }
```

| Status | When |
|---|---|
| 409 `The bot's engine has no default model configured` | the engine answered `error: "default_model_not_configured"` (relay raises `EngineDefaultModelNotConfiguredError`) |
| 501 | the engine does not declare `model.default.get` |
| 502 `Engine service error` | any other engine failure |
| 404 `Not found` | the bot is not the caller's (masked, as on every runtime route) |

## 3. Plugin contract (inside the adapter)

```python
class ModelsService(Protocol):
    async def get_default_model(self, auth: AuthContext | None = None) -> DefaultModel: ...

class DefaultModel(BaseModel):
    model: Model                 # catalogue entry or synthesised minimal entry
    fallbacks: list[str] = []

class DefaultModelNotConfiguredError(RuntimeError): ...   # core/models/exceptions.py
```

Every engine resolves a default; when it cannot, the port raises
`DefaultModelNotConfiguredError` (⇒ `success: false`,
`error: "default_model_not_configured"`). Other upstream failures raise as
they are and surface as `success: false`. Each engine's native port returns
a primitive dict (OpenClaw: `{"model": "<provider/model>", "fallbacks": [...]}`;
Claude Code: the relay's `{"model", "source", "path?"}`); the ACL adapter
matches `model` against `list_models()` with `find_catalogue_model` (exact
`id` or `provider_id`; a bare id also matches a single prefixed entry, never
an ambiguous one or another provider's) and builds the DTO. The HTTP layer
never reads engine config itself.

## 4. Where each engine keeps its default model

| Engine | Source of truth | Key | How this adapter reads it | Status |
|---|---|---|---|---|
| **OpenClaw** | `~/.openclaw/openclaw.json` (the deployment never relocates it; no env lookup) | `agents.defaults.model.primary` (+ `.fallbacks`), or `agents.defaults.model` as a plain `provider/model` string | Gateway RPC `config.get` (live snapshot: `config` runtime-shaped → `resolved` → `parsed`); when the RPC fails the file is read directly. Either way the value is what the user wrote in `openclaw.json`. Per-agent `agents.list[*].model` overrides are **not** consulted (Avernet runs one agent per gateway). | ✅ implemented |
| **Claude Code** (community relay `claude_code_gateway`) | the relay's resolution chain | `RELAY_DEFAULT_MODEL` env → `<CLAUDE_CONFIG_DIR>/settings.json` `env.ANTHROPIC_MODEL` → `RELAY_MODEL_SETTINGS_SOURCE` file `env.ANTHROPIC_MODEL` → hard-coded `claude-sonnet-4-5` | New relay RPC `models.default` → `{ model, source, path? }` (see `claude_code_gateway/docs/websocket-protocol.md` §10.4). Always answers; only `model` is surfaced over HTTP. | ✅ implemented |
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
| `source` | string, required | Short token naming the step of the relay's resolution chain that won. Kept between relay and adapter for debugging; not surfaced over HTTP. |
| `path` | string, optional | Absolute path of the file the value came from; omit for env / fallback. Not surfaced over HTTP. |

Resolve on every call (do not cache at boot) so a settings edit shows without
a relay restart. Answer `ok: false` only if the relay truly has no default; the
adapter maps that to `error: "default_model_not_configured"`. Relays that
predate the method answer with an unknown method error; the adapter treats
that the same way.
