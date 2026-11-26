# Stateless model completion

The optional Service API `ModelCompletionService.complete_model` is defined in
`core/models/completion.py`; OpenClaw consumes the independent optional Plugin
API `OpenClawCompletionPort` from `plugin_api/openclaw/completion.py`.
`ModelsService` and model-list/default-model contracts remain compatible.

## HTTP contract

`POST /api/engine/models:complete` requires the `models.complete` capability.
Like other Engine APIs this is an internal runtime surface: deploy it behind
runtime/network admission, not as a public credential-free inference endpoint.
Backend's Evolution callback performs operator admission before relaying here.

Input (unknown fields are rejected):

```json
{
  "messages": [{"role": "user", "content": "Synthetic test"}],
  "model": null,
  "max_tokens": 64
}
```

- `messages`: 1–20 text messages; roles system/user/assistant only; total content
  length at most 70,000 characters. No tools, attachments or session identifiers.
- `model`: omitted/null selects the **current gateway-configured default**, not
  a session-specific override. An explicit value selects a configured
  `provider/model`. It never supplies a provider URL, key or custom headers.
- `max_tokens`: integer 1–8192, default 4096; booleans are rejected.
- Success uses the existing Engine envelope: `success: true`, `data` containing
  only nonempty `text`, qualified `model`, and positive integer `tokens` from
  upstream `usage.total_tokens` (plus the existing capability `warning`).
- Unsupported capabilities/providers/auth return 501; upstream or invalid
  completions return sanitized 502. No upstream body/configuration is echoed.

## OpenClaw implementation boundary

Each call reads `config.get` for the live default and provider routing. The
runtime reads that snapshot's local file only to recover its redacted literal
API key. Non-secret routing must agree between the snapshot and file. This is
not a cached boot template, environment-wide credential, or session turn.

Supported: standalone JSON, explicitly configured HTTPS OpenAI-compatible
`openai-completions` models, literal API-key/Bearer authentication. JSON5,
includes/duplicate keys, environment secret references, OAuth/auth profiles,
custom provider/model headers and other API dialects are not supported and do
not silently fall back. Unsupported Bot deployments can use an explicitly
configured platform manual provider instead.

The request performs one non-streaming `/chat/completions` inference; no chat
history, filesystem/tool execution, configuration writes or Bot mutation. The
whole native operation is bounded at 180 seconds, raw configuration/response at
2 MiB each, returned text at 250,000 characters. Redirects and environment proxies
are disabled. Credentials remain inside the Bot runtime.

## Verification and deployment limits

Consumer/optional-port, routing changes and key rotation, unsupported auth,
strict wire validation and safe errors are covered by
`plugins/openclaw/tests/test_completion.py` and
`api/tests/test_model_completion.py`.
These tests use isolated fixtures, not a live Bot/provider. Existing runtime
images do not gain the capability until upgraded through their supported
artifact-preserving deployment path; upgrading Evolution alone is insufficient.
