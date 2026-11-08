# Default-model read contract

`GET /openapi/v1/bots/{bot_id}/engine/default-model` reads the addressed
bot's selected live runtime through `GET /api/engine/default-model`. The public
route uses the existing operator (MEMBER), application-grant, stage and device
selection rules. It does not grant access to the bot's full configuration.

Both routes return their existing response envelope with
`data: {"model_id": "provider/model"}`. `model_id` is an engine-owned string or
`null` when the engine has no configured default. It need not occur in the model
catalogue. Configuration, provider credentials and filesystem paths are never
part of this response. Session and individual request overrides are excluded.

The optional core `DefaultModelService` extension has one read method,
`get_default_model(auth=None) -> str | None`. It shares the models plugin slot,
but is independent of `ModelsService`'s catalogue operations. Existing
catalogue-only plugins continue to implement the original protocol.
`MODEL_DEFAULT_GET` (`models.default.get`) must be declared only by providers
implementing this extension. An unsupported read returns 501. Provider failures
or malformed responses fail explicitly (502), rather than returning success
with `null`. Runtime availability and timeouts retain the existing transport
error contract. An old adapter's 404 is not interpreted as an unset default.

OpenClaw implements the optional Plugin API `OpenClawDefaultModelPort` through
the existing default gateway connection's `config.get` request. Core validates
`exists`/`valid` and extracts `parsed.agents.defaults.model` (string) or its
`primary` member. It does not use `config`/`runtimeConfig`, factory templates,
catalogue flags, agent-local overrides or fallback lists. Invalid configuration responses and
invalid field types fail; an absent source file or default field reads as null.
The native config.get response stays inside the adapter boundary and must never be logged.

Other engines implement the same optional core contract against their own
authoritative default source. Engines that already define an implicit default
may return that identifier. They must document that engine-specific meaning,
and must not silently substitute a per-session model.

The OpenClaw port reads configuration through `config.get` on every call; it
does not retain a fixed snapshot. A failed gateway connection or RPC remains a
read failure. There is no direct-file fallback: path selection, parsing and
validation stay with the addressed OpenClaw gateway, avoiding a second source
of configuration truth in the adapter.
