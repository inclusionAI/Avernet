# Tasks: Connection Endpoint — Gateway URL and Query-Parameter Credential

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

All paths are relative to `src/backend/`. Source package is
`src/agentclaw/community/`, abbreviated `…/` below.

**Scope guardrail on every task:** `src/baas/**`, `…/core/grt_chat/**`,
`…/core/devices/services/device_service.py`,
`…/core/devices/services/baas_conn_info.py` and `src/frontend/**` are not
touched. The internal console must stay byte-for-byte unaffected.

---

## Task 1: Add the `gateway` host configuration  `[ ]`
- **Goal:** Give the backend a place to learn where the gateway is, following
  the same pattern as every other upstream host in this service. Nothing
  consumes it yet.
- **Files:**
  - `…/di/config.py`
  - `…/di/modules/config_module.py`
  - `…/configs/application-community.yaml`
  - `…/configs/application-singlebox.yaml`
- **Done when:**
  - [ ] `GatewayConfig` is a frozen dataclass beside `BcnConfig:64-81`, with
        `base_url: str = ""` and `base_url_pre: str = ""`. Its docstring says
        what an empty value means: this deployment has no gateway.
  - [ ] A `@singleton @provider` in `config_module.py` reads `_block("gateway")`
        with the dataclass defaults, shaped like the `ecb` reader at `:322-327`.
  - [ ] `application-community.yaml` carries a neutral `gateway` block (both
        values `""`) under `user_config:`, beside `ecb:69-70`.
  - [ ] `application-singlebox.yaml` carries `gateway.base_url:
        "http://127.0.0.1:9999"`, beside `agentclawproxy:122-123`.
  - [ ] No corp pre/prod hosts appear anywhere in this repo — those land in the
        cob overlay, owned separately.
  - [ ] Existing config tests pass unmodified; the golden singlebox config
        snapshot is regenerated if it pins the `user_config` tree.
- **Depends on:** —

## Task 2: Compose the gateway URL and carry the credential in its query  `[ ]`
- **Goal:** The behavioural change. `EngineConnectionService` publishes a
  gateway URL under `/engine` with the credential in the query string instead of
  an engine-proxy URL with the credential in a header.
- **Files:**
  - `…/core/engine_runtime/connection.py`
  - `tests/community/core/engine_runtime/test_connection.py`
- **Done when:**
  - [ ] `_PROXY_TOKEN_HEADER:51` becomes `_PROXY_TOKEN_PARAM` (same
        `x-proxypass-token` string, now a query key); `_ENGINE_PREFIX =
        "/engine"` is added near `:48`.
  - [ ] `__init__:120-133` takes `gateway_config: cfg.GatewayConfig` and no
        longer takes `sandbox_client`. DI needs no edit — the service is bound to
        itself with `@inject` (`di/modules/engine_runtime_module.py:33-35`).
  - [ ] `_ws_base:288-313` is replaced by `_gateway_ws_base()`: select
        `base_url_pre` when `get_current_env() == "pre"` else `base_url`
        (matching `di/modules/http_client_module.py:60`), `rstrip("/")`, apply
        the existing `https→wss` / `http→ws` rewrite (`:312`), and raise
        `EngineUpstreamError` naming the config block when the selected value is
        empty.
  - [ ] `_socket_url:270-286` takes the credential and resolves in this order:
        (1) `type == "local"` → `ws://{target}{path}`, **no credential**, and the
        gateway config is never read; (2) otherwise →
        `{gateway}/engine/{target}{path}`, with `?x-proxypass-token=…` appended
        **only when a credential exists**; (3) a provider URL that is present but
        not the `/proxypass/` shape → `EngineUpstreamError` (the Risk 1 guard).
  - [ ] The credential is percent-encoded with `quote(token, safe="")`; the
        target segment is **not** encoded — `@` and `:` are legal `pchar` and
        production carries them raw.
  - [ ] Token extraction at `:191` (`ws_token or token`) and its comment at
        `:187-190` are **unchanged** — the precedence and its reason still hold.
  - [ ] `build` no longer builds a `headers` dict; `SocketInfo` is constructed
        without it. (The field still exists until Task 3.)
  - [ ] The module docstring at `:9-11` no longer claims nothing exposes a
        target or a bare token — the URL now visibly carries both.
  - [ ] Rewritten tests: `:157` and `:179` assert the credential in the URL's
        query while still pinning `ws_token`-before-`token`; `:227` asserts the
        gateway host, the `/engine` prefix and the query credential; `:212` and
        `:283` take their failure from an empty `gateway` block rather than
        `sandbox_client`; `:149` and `:235` keep their inputs but now assert
        `EngineUpstreamError` and are renamed, since what they pin is inverted.
  - [ ] The `_svc` helper `:103-109` drops `sandbox` and gains a `GatewayConfig`;
        the `_Sandbox` stub `:83` goes.
  - [ ] New tests: a local device publishes `ws://{target}{path}` with no
        credential and never reads the gateway config; a provider issuing no
        credential publishes a URL with **no** query string, not a trailing
        `?x-proxypass-token=`; a credential needing escaping is percent-encoded;
        the target's `@` and `:` survive **unencoded**; an empty `gateway` block
        raises before any credential is embedded in a URL; `base_url_pre` is
        selected on `pre` and `base_url` otherwise.
  - [ ] Still passing unmodified: the bot-type and sharing gates `:331-413`,
        `test_the_provider_is_asked_exactly_once:419`, the expiry tests
        `:288-316`, `test_result_carries_no_target_type_or_bare_token:322`, and
        `test_chat_path_follows_the_engine:256`.
- **Depends on:** Task 1

## Task 3: Remove `headers` from the contract and retighten `expires_at`  `[ ]`
- **Goal:** Delete the socket `headers` field rather than publish it empty, and
  fix the expiry wording that reads as though a live socket dies at expiry.
- **Files:**
  - `…/core/engine_runtime/models.py`
  - `…/adapters/http/openapi_v1/engine_runtime/connection/schemas.py`
  - `…/adapters/http/openapi_v1/engine_runtime/connection/router.py`
  - `tests/community/core/engine_runtime/test_connection.py`
  - `tests/community/adapters/http/openapi_v1/engine_runtime/test_connection.py`
- **Done when:**
  - [ ] `SocketInfo:86-96` has no `headers` field; the `field` import survives
        only if `ConnectionResult.sockets` still needs it.
  - [ ] `Socket:28-30` has no `headers` field, and both examples
        (`:15-19`, `:42-47`) show a gateway URL carrying the query credential.
  - [ ] `router.py:71` constructs `Socket(kind=…, url=…)`.
  - [ ] `expires_at` in `schemas.py:53-57` states that it bounds **opening** a
        socket, that an already-open socket survives it, that a caller fetches a
        fresh credential before connecting or reconnecting, and that a caller
        should not poll on a timer to keep a live socket alive. `models.py:107-109`
        says the same.
  - [ ] A regression test asserts neither `SocketInfo` nor `Socket` has a
        `headers` attribute, so the credential cannot quietly reappear in two
        places.
  - [ ] `/openapi/v1/bots/{bot_id}/connection` is confirmed still absent from
        `src/gateway/configs/schemas/bots.openapi.json`, so the removal does not
        need `--allow-breaking`.
- **Depends on:** Task 2

## Task 4: Verification against the spec  `[ ]`
- **Goal:** Confirm the feature meets every spec acceptance criterion, and that
  the console is untouched.
- **Files:** none (verification only); `spec.md` checkboxes are ticked.
- **Done when:**
  - [ ] All ten `spec.md` acceptance criteria are checked off, each against an
        observable behaviour or a named test.
  - [ ] `git diff dev --stat` touches no path under `src/baas/`,
        `…/core/grt_chat/`, `…/core/devices/`, or `src/frontend/` — the
        console-unaffected criterion, checked rather than asserted.
  - [ ] The backend module gates pass (`AGENTS.md` pre-push contract).
  - [ ] The published example in `schemas.py` is a URL a caller could actually
        open — gateway host, `/engine` prefix, query credential, no `headers`.
- **Depends on:** Tasks 1, 2, 3

---

## Groups

- **Group A — Gateway configuration:** Task 1
  - Theme: the config seam and its neutral community values. Lands on its own
    with nothing reading it yet, so it is reviewable purely as a config change.
- **Group B — URL and credential:** Tasks 2, 3
  - Theme: the behavioural change and the contract edit it enables. Task 2 stops
    populating `headers`; Task 3 deletes the field, so the two are reviewed in
    that order but form one coherent slice.
- **Group C — Verification:** Task 4
  - Theme: final spec acceptance check.

---

## Plan gaps this breakdown surfaced

1. **No dev/local fallback for the gateway host.** The plan's first draft read
   the address from an env var and carried a `SERVER_ENV in {dev, local}`
   default, following `data_proxy_service.py:65`. Moving to `application.yaml`
   dropped that: `get_current_env()` returns `""` when `SERVER_ENV` /
   `REAL_SERVER_ENV` / `ALIPAY_APP_ENV` are all unset
   (`utils/env_utils.py:7-15`), which selects `base_url` — empty in community.
   So a developer running the community profile locally gets
   `EngineUpstreamError` from this endpoint until they set a value by hand.
   That is arguably correct (community genuinely has no gateway, and it matches
   today's behaviour where `proxy_base_url()` raises), and singlebox has its own
   placeholder — but it is a behaviour change from the plan's first draft and
   nobody has explicitly chosen it. **Flagging rather than deciding.**

2. **The golden singlebox config snapshot may need regenerating.**
   `tests/community/config/golden/singlebox.json` pins a `user_config` tree that
   already includes the `agentclawproxy` block. Adding `gateway` beside it will
   fail that test until the golden file is updated. Task 1 covers it, but it is
   a step the plan did not name.
