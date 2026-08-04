# Principal 签发与转发（网关侧）— 实现计划

> **⚠️ 配置部分已过时。** 本文中的 `AVERNET_PRINCIPAL_SIGNING_KEY` / `_KID` / `_TTL`
> env 读取是这份计划落地当时的实现记录，PR #673 之后网关已改由 `SecretResolver` +
> `user_config.principal_signer` 取配置，那些 env 变量不再被读取。**不要照本文配置部署**
> —— 当前契约见同目录 `spec.md` 的「配置来源（PR #673 起）」。其余内容（SPI 形状、转发
> 接缝、`aud` 取自 `server.name`）仍然成立。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 网关把解析出的 `dict[PrincipalType, Principal]` 签成短时 JWT，以 `X-Avernet-Principal` 头注入转发请求，去掉空占位接缝。

**Architecture:** 新增 `PrincipalSigner` SPI（`async sign(principals, audience) -> str`）+ bare HMAC(HS256) 实现；转发接缝 `_attach_identities` 由 no-op 改为真注入，并剔除入站伪造头；签名器经 bootstrap 组装后挂到 `app.state.principal_signer`，`aud` 取自 `server.name`。

**Tech Stack:** Python 3.12+ / FastAPI / SQLAlchemy（复用）/ pyjwt（新增）/ pytest(asyncio_mode=auto)。

**Spec:** `src/gateway/specs/2026-07-29-principal-signer/spec.md`

---

## File Structure

- Create `src/gateway/community/spi/principal_signer/_ports.py` — `PrincipalSigner` Protocol
- Create `src/gateway/community/spi/principal_signer/__init__.py` — 导出 `PrincipalSigner`
- Create `src/gateway/community/plugins/principal_signer/__init__.py` — 包标记
- Create `src/gateway/community/plugins/principal_signer/bare/__init__.py` — 导出 bare 实现
- Create `src/gateway/community/plugins/principal_signer/bare/_plugin.py` — `PrincipalSignerConfig` / `BarePrincipalSigner` / `load_signer_config`
- Create `src/gateway/community/bootstrap/_principal_signer.py` — `build_principal_signer()`
- Modify `pyproject.toml` — 加 `pyjwt>=2.8.0`
- Modify `src/gateway/community/bootstrap/__init__.py` — 导出 `build_principal_signer`
- Modify `src/gateway/community/adapters/web/app.py` — 构建并挂 `app.state.principal_signer`
- Modify `src/gateway/community/adapters/web/_forward.py` — `_attach_identities` 改异步注入、剔除入站头、`forward_request` 接线 + 500 失败兜底
- Create `tests/contracts/spi/test_principal_signer_port.py` — SPI 冒烟
- Create `tests/unit/plugins/test_principal_signer.py` — bare 签发器单测
- Modify `tests/test_forward_seam.py` — 替换为「注入签名头」的新行为测试
- Create `tests/integration/test_forward_signs_principal.py` — 端到端：签名注入 + 入站剔除 + 签名失败 500

所有命令在 `src/gateway` 目录下用 `.venv/bin/python -m pytest ...` 与 `.venv/bin/python -m ruff ...` 执行。

---

### Task 1: 引入 pyjwt 依赖

**Files:**
- Modify: `pyproject.toml`（`[project].dependencies` 列表）

- [ ] **Step 1: 在 `dependencies` 中加入 pyjwt**

在 `pyproject.toml` 的 `dependencies = [ ... ]` 列表里，于 `"sqlalchemy>=2.0.0",` 这一行之后插入：

```toml
    "pyjwt>=2.8.0",
```

- [ ] **Step 2: 安装并校验可导入**

Run:
```bash
uv sync
.venv/bin/python -c "import jwt; print(jwt.__version__)"
```
Expected: 打印一个 `2.8` 以上的版本号，无异常。

- [ ] **Step 3: 提交**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add pyjwt for principal signing"
```

---

### Task 2: PrincipalSigner SPI 端口

**Files:**
- Create: `src/gateway/community/spi/principal_signer/_ports.py`
- Create: `src/gateway/community/spi/principal_signer/__init__.py`
- Test: `tests/contracts/spi/test_principal_signer_port.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/contracts/spi/test_principal_signer_port.py`：

```python
"""Smoke test: the PrincipalSigner SPI port is importable."""

from __future__ import annotations

from gateway.community.spi.principal_signer import PrincipalSigner


def test_protocol_is_importable() -> None:
    assert PrincipalSigner is not None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/contracts/spi/test_principal_signer_port.py -q`
Expected: FAIL（`ModuleNotFoundError: gateway.community.spi.principal_signer`）

- [ ] **Step 3: 写 SPI**

创建 `src/gateway/community/spi/principal_signer/_ports.py`：

```python
"""Principal-signer SPI — sign the resolved identity set for a downstream audience.

The authn pipeline resolves a ``dict[PrincipalType, Principal]``; the gateway
signs that set into a short-lived token (auth design §7.1) so downstream
components can run ``auth.mode=none`` and merely verify the signature. Concrete
flavors (``bare`` HMAC, ``sofa`` asymmetric + KMS) implement this interface.
"""

from __future__ import annotations

from typing import Mapping, Protocol

from gateway.community.spi.authn import Principal, PrincipalType


class PrincipalSigner(Protocol):
    """Sign the resolved identity set into a short-lived token for ``audience``.

    Returns a compact JWS (JWT) string. Implementations SHOULD NOT raise on
    normal input; a signing failure is a gateway-internal error the caller maps
    to a fail-closed response (do not forward). The returned token is the sole
    trust-bearer — components must never trust a bare ``X-Avernet-Principal``.
    """

    async def sign(
        self, principals: Mapping[PrincipalType, Principal], *, audience: str
    ) -> str: ...
```

创建 `src/gateway/community/spi/principal_signer/__init__.py`：

```python
"""Principal-signer SPI — the ``PrincipalSigner`` contract.

See ``_ports`` for the protocol. The canonical bare (HMAC) impl lives in
``plugins/principal_signer/bare``; the adapter injects the built signer and
calls it at the forwarder seam.
"""

from ._ports import PrincipalSigner

__all__ = ["PrincipalSigner"]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/contracts/spi/test_principal_signer_port.py -q`
Expected: PASS

- [ ] **Step 5: ruff + 提交**

```bash
.venv/bin/python -m ruff format src/gateway/community/spi/principal_signer tests/contracts/spi/test_principal_signer_port.py
.venv/bin/python -m ruff check .
git add src/gateway/community/spi/principal_signer tests/contracts/spi/test_principal_signer_port.py
git commit -m "feat(spi): add PrincipalSigner port"
```

---

### Task 3: bare HMAC 签发器 + 配置

**Files:**
- Create: `src/gateway/community/plugins/principal_signer/__init__.py`
- Create: `src/gateway/community/plugins/principal_signer/bare/__init__.py`
- Create: `src/gateway/community/plugins/principal_signer/bare/_plugin.py`
- Test: `tests/unit/plugins/test_principal_signer.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/plugins/test_principal_signer.py`：

```python
"""Unit tests for the bare (HMAC) PrincipalSigner."""

from __future__ import annotations

import jwt
import pytest

from gateway.community.plugins.principal_signer.bare import (
    BarePrincipalSigner,
    PrincipalSignerConfig,
    load_signer_config,
)
from gateway.community.spi.authn import AppPrincipal, ThirdPartyApp

_PRINCIPAL_HEADER = "X-Avernet-Principal"  # noqa: F841  (documented contract)
_FIXED_NOW = 1_700_000_000


def _cfg(key: str = "k") -> PrincipalSignerConfig:
    return PrincipalSignerConfig(signing_key=key, kid="bare", ttl_seconds=60)


def _app_principal() -> AppPrincipal:
    return AppPrincipal(
        tenant="t",
        app=ThirdPartyApp(app_id="app-1", app_name="Demo", owners="org-1", tenant="t"),
    )


async def test_sign_returns_decodable_jwt_with_expected_claims() -> None:
    signer = BarePrincipalSigner(_cfg(), clock=lambda: _FIXED_NOW)
    token = await signer.sign({"app": _app_principal()}, audience="secbaas")

    decoded = jwt.decode(
        token, "k", algorithms=["HS256"], audience="secbaas", issuer="gateway"
    )
    assert decoded["iss"] == "gateway"
    assert decoded["aud"] == "secbaas"
    assert decoded["iat"] == _FIXED_NOW
    assert decoded["exp"] == _FIXED_NOW + 60
    assert decoded["principals"] == [_app_principal().model_dump(mode="json")]


async def test_kid_is_carried_in_jose_header() -> None:
    signer = BarePrincipalSigner(
        PrincipalSignerConfig(signing_key="k", kid="rot-7"), clock=lambda: _FIXED_NOW
    )
    token = await signer.sign({}, audience="secbaas")
    assert jwt.get_unverified_header(token)["kid"] == "rot-7"


async def test_wrong_audience_rejected_on_decode() -> None:
    signer = BarePrincipalSigner(_cfg(), clock=lambda: _FIXED_NOW)
    token = await signer.sign({}, audience="secbaas")
    with pytest.raises(jwt.InvalidAudienceError):
        jwt.decode(
            token, "k", algorithms=["HS256"], audience="engine", issuer="gateway"
        )


async def test_wrong_key_rejected_on_decode() -> None:
    signer = BarePrincipalSigner(_cfg(), clock=lambda: _FIXED_NOW)
    token = await signer.sign({}, audience="secbaas")
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(
            token, "other", algorithms=["HS256"], audience="secbaas", issuer="gateway"
        )


async def test_empty_principals_still_signs() -> None:
    signer = BarePrincipalSigner(_cfg(), clock=lambda: _FIXED_NOW)
    token = await signer.sign({}, audience="secbaas")
    decoded = jwt.decode(
        token, "k", algorithms=["HS256"], audience="secbaas", issuer="gateway"
    )
    assert decoded["principals"] == []


def test_load_signer_config_reads_env() -> None:
    cfg = load_signer_config(
        {
            "AVERNET_PRINCIPAL_SIGNING_KEY": "envk",
            "AVERNET_PRINCIPAL_SIGNING_KID": "k7",
            "AVERNET_PRINCIPAL_SIGNING_TTL": "30",
        }
    )
    assert cfg.signing_key == "envk"
    assert cfg.kid == "k7"
    assert cfg.ttl_seconds == 30


def test_load_signer_config_dev_fallback_when_unset() -> None:
    cfg = load_signer_config({})
    assert cfg.signing_key  # dev fallback present
    assert cfg.kid == "bare"
    assert cfg.ttl_seconds == 60
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/unit/plugins/test_principal_signer.py -q`
Expected: FAIL（`ModuleNotFoundError: gateway.community.plugins.principal_signer.bare`）

- [ ] **Step 3: 写 bare 实现**

创建 `src/gateway/community/plugins/principal_signer/__init__.py`：

```python
"""Principal-signer plugins (bare HMAC flavor)."""
```

创建 `src/gateway/community/plugins/principal_signer/bare/_plugin.py`：

```python
"""BarePrincipalSigner — single-box HMAC (HS256) signer for the community edition.

Signs the resolved identity set as a short-lived JWT with ``iss/aud/iat/exp`` +
a ``principals`` claim, keyed by a shared HMAC secret (kid in the JOSE header
for rotation). NOT production-grade — sofa uses asymmetric signing + KMS
(auth design §7.1).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import jwt

from gateway.community.spi.authn import Principal, PrincipalType

_KID_ENV = "AVERNET_PRINCIPAL_SIGNING_KID"
_KEY_ENV = "AVERNET_PRINCIPAL_SIGNING_KEY"
_TTL_ENV = "AVERNET_PRINCIPAL_SIGNING_TTL"
_DEV_FALLBACK_KEY = "avernet-dev-signing-key-NOT-FOR-PROD"


@dataclass(frozen=True)
class PrincipalSignerConfig:
    """Runtime config for :class:`BarePrincipalSigner`."""

    signing_key: str
    kid: str = "bare"
    issuer: str = "gateway"
    ttl_seconds: int = 60


class BarePrincipalSigner:
    """HMAC HS256 signer — the bare flavor of the :class:`PrincipalSigner` SPI."""

    def __init__(
        self,
        config: PrincipalSignerConfig,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cfg = config
        self._clock = clock

    async def sign(
        self, principals: Mapping[PrincipalType, Principal], *, audience: str
    ) -> str:
        now = int(self._clock())
        claims = {
            "iss": self._cfg.issuer,
            "aud": audience,
            "iat": now,
            "exp": now + self._cfg.ttl_seconds,
            "principals": [p.model_dump(mode="json") for p in principals.values()],
        }
        return jwt.encode(
            claims,
            self._cfg.signing_key,
            algorithm="HS256",
            headers={"kid": self._cfg.kid},
        )


def load_signer_config(
    env: Mapping[str, str] | None = None,
) -> PrincipalSignerConfig:
    """Build :class:`PrincipalSignerConfig` from env, with a dev fallback key.

    A missing ``AVERNET_PRINCIPAL_SIGNING_KEY`` falls back to a fixed dev secret
    and logs a warning — fine for single-box/tests, NOT for production. sofa
    (asymmetric + KMS) is a separate workstream and will require a real key.
    """
    env = os.environ if env is None else env
    key = env.get(_KEY_ENV, "")
    kid = env.get(_KID_ENV, "") or "bare"
    ttl_raw = env.get(_TTL_ENV, "")
    ttl = int(ttl_raw) if ttl_raw.isdigit() else 60
    if not key:
        from gateway.community.logger import get_logger

        get_logger("principal_signer").warning(
            "AVERNET_PRINCIPAL_SIGNING_KEY unset — using dev fallback key "
            "(NOT for production)."
        )
        key = _DEV_FALLBACK_KEY
    return PrincipalSignerConfig(signing_key=key, kid=kid, ttl_seconds=ttl)
```

创建 `src/gateway/community/plugins/principal_signer/bare/__init__.py`：

```python
from gateway.community.plugins.principal_signer.bare._plugin import (
    BarePrincipalSigner,
    PrincipalSignerConfig,
    load_signer_config,
)

__all__ = ["BarePrincipalSigner", "PrincipalSignerConfig", "load_signer_config"]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/unit/plugins/test_principal_signer.py -q`
Expected: PASS（7 passed）

- [ ] **Step 5: ruff + 提交**

```bash
.venv/bin/python -m ruff format src/gateway/community/plugins/principal_signer tests/unit/plugins/test_principal_signer.py
.venv/bin/python -m ruff check .
git add src/gateway/community/plugins/principal_signer tests/unit/plugins/test_principal_signer.py
git commit -m "feat(principal_signer): add bare HMAC signer + env config"
```

---

### Task 4: bootstrap 组装 + 挂到 app.state

**Files:**
- Create: `src/gateway/community/bootstrap/_principal_signer.py`
- Modify: `src/gateway/community/bootstrap/__init__.py`
- Modify: `src/gateway/community/adapters/web/app.py`

- [ ] **Step 1: 新建 bootstrap 组装函数**

创建 `src/gateway/community/bootstrap/_principal_signer.py`：

```python
"""Composition root for the PrincipalSigner (auth design §7.1).

Builds the bare (HMAC) signer from env. The adapter receives the built signer
via ``app.state.principal_signer`` and calls it at the forwarder seam.
"""

from __future__ import annotations

from gateway.community.plugins.principal_signer.bare import (
    BarePrincipalSigner,
    load_signer_config,
)
from gateway.community.spi.principal_signer import PrincipalSigner


def build_principal_signer() -> PrincipalSigner:
    """Build the PrincipalSigner from env (bare HMAC flavor)."""
    return BarePrincipalSigner(load_signer_config())
```

- [ ] **Step 2: 从 bootstrap 包导出**

修改 `src/gateway/community/bootstrap/__init__.py`，变为：

```python
"""Bootstrap — dependency injection and application lifecycle.

The composition root: wires concrete plugins and services into the app. Adapters
import the built objects from here (e.g. the ``Authenticator``) rather than
constructing plugins themselves.
"""

from ._authn import Authenticator, build_authenticator, build_database
from ._forwarding import Forwarding, build_forwarding
from ._principal_signer import build_principal_signer

__all__ = [
    "Authenticator",
    "Forwarding",
    "build_authenticator",
    "build_database",
    "build_forwarding",
    "build_principal_signer",
]
```

- [ ] **Step 3: 在 create_app 构建并挂 state**

修改 `src/gateway/community/adapters/web/app.py`。把 `create_app` 内的 lazy bootstrap 导入块改为：

```python
    from gateway.community.bootstrap import (
        build_authenticator,
        build_database,
        build_forwarding,
        build_principal_signer,
    )

    db = build_database()
    authenticator = build_authenticator(db)
    forwarding = build_forwarding()
    principal_signer = build_principal_signer()
```

然后在设置 `app.state` 的三行之后追加一行，使该段变为：

```python
    # Hand the composed subsystems to the delivery layer via app.state.
    app.state.authenticator = authenticator
    app.state.domain_map = forwarding.domain_map
    app.state.forwarder = forwarding.forwarder
    app.state.principal_signer = principal_signer
```

- [ ] **Step 4: 校验应用可建、state 已挂**

Run:
```bash
.venv/bin/python -c "
from gateway.community.adapters.web.app import create_app
app = create_app()
assert app.state.principal_signer is not None
print('ok', type(app.state.principal_signer).__name__)
"
```
Expected: 打印 `ok BarePrincipalSigner`，无异常。

- [ ] **Step 5: ruff + 提交**

```bash
.venv/bin/python -m ruff format src/gateway/community/bootstrap src/gateway/community/adapters/web/app.py
.venv/bin/python -m ruff check .
git add src/gateway/community/bootstrap src/gateway/community/adapters/web/app.py
git commit -m "feat(bootstrap): wire PrincipalSigner into app.state"
```

---

### Task 5: 接缝 `_attach_identities` 改为真注入

**Files:**
- Modify: `src/gateway/community/adapters/web/_forward.py`
- Modify: `tests/test_forward_seam.py`

- [ ] **Step 1: 替换 seam 行为测试（先写新测试）**

整体覆盖 `tests/test_forward_seam.py` 为：

```python
"""Tests for the forwarder identity seam (auth design §7.1).

The seam INJECTS the signed identity set as ``X-Avernet-Principal`` (the
PrincipalSigner workstream has landed). Components must verify the token —
never trust a bare header. Inbound ``X-Avernet-Principal`` is stripped at the
call site (covered by the integration test).
"""

from __future__ import annotations

import jwt

from gateway.community.adapters.web._forward import _PRINCIPAL_HEADER, _attach_identities
from gateway.community.spi.authn import AppPrincipal, ThirdPartyApp
from gateway.community.spi.forwarder import ForwardRequest


class _FixedSigner:
    """Signs with a fixed HMAC key for deterministic seam tests."""

    def __init__(self, key: str = "seam-key", *, kid: str = "bare") -> None:
        self._key = key
        self._kid = kid

    async def sign(self, principals, *, audience: str) -> str:
        payload = [p.model_dump(mode="json") for p in principals.values()]
        return jwt.encode(
            {"iss": "gateway", "aud": audience, "principals": payload},
            self._key,
            algorithm="HS256",
            headers={"kid": self._kid},
        )


def _app() -> AppPrincipal:
    return AppPrincipal(
        tenant="t",
        app=ThirdPartyApp(app_id="a", app_name="A", owners="o", tenant="t"),
    )


def _req() -> ForwardRequest:
    return ForwardRequest(
        method="GET", url="http://up/x", headers={"x-existing": "keep"}, content=b""
    )


async def test_seam_injects_signed_principal_header() -> None:
    out = await _attach_identities(
        _req(), {"app": _app()}, signer=_FixedSigner("k"), audience="secbaas"
    )
    token = out.headers[_PRINCIPAL_HEADER]
    decoded = jwt.decode(
        token, "k", algorithms=["HS256"], audience="secbaas", issuer="gateway"
    )
    assert decoded["aud"] == "secbaas"
    assert decoded["principals"] == [_app().model_dump(mode="json")]
    # The pre-existing header is preserved alongside the injected one.
    assert out.headers["x-existing"] == "keep"
    assert out.method == "GET"
    assert out.url == "http://up/x"


async def test_seam_no_header_when_identities_empty() -> None:
    out = await _attach_identities(
        _req(), {}, signer=_FixedSigner("k"), audience="secbaas"
    )
    assert _PRINCIPAL_HEADER not in out.headers
    assert out.headers == {"x-existing": "keep"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_forward_seam.py -q`
Expected: FAIL（`_attach_identities` 仍为旧 2 参 sync 签名 / `_PRINCIPAL_HEADER` 不存在）

- [ ] **Step 3: 重写 `_forward.py` 的接缝与调用点**

修改 `src/gateway/community/adapters/web/_forward.py`。

3a. 在文件顶部 import 区追加（紧跟 `from starlette.responses import Response` 之后）：

```python
from dataclasses import replace

from gateway.community.logger import get_logger
from gateway.community.spi.principal_signer import PrincipalSigner
```

并在 `from gateway.community.tracer import get_tracer_plugin` 之后加：

```python
logger = get_logger("forward")
```

3b. 用下面的版本替换 `_attach_identities` 与其上的 `_TENANT`/常量区（即替换从 `_attach_identities` 定义到 `return forward` 结束整段）：

```python
_PRINCIPAL_HEADER = "X-Avernet-Principal"
# Inbound headers that must NEVER pass through to the upstream (call site
# strips these when building ForwardRequest.headers). `host` is dropped so
# httpx sets it from the upstream URL; `X-Avernet-Principal` is dropped so a
# caller cannot forge the identity header (the gateway injects its own signed
# token).
_INBOUND_STRIP = frozenset({"host", "x-avernet-principal"})


async def _attach_identities(
    forward: ForwardRequest,
    identities: dict[PrincipalType, Principal],
    *,
    signer: PrincipalSigner,
    audience: str,
) -> ForwardRequest:
    """Inject the signed identity set into the forwarded request (auth §7.1).

    Components must verify this token — never trust a bare
    ``X-Avernet-Principal`` header. An empty identity set adds no header.
    """
    if not identities:
        return forward
    token = await signer.sign(identities, audience=audience)
    headers = {**forward.headers, _PRINCIPAL_HEADER: token}
    return replace(forward, headers=headers)
```

3c. 在 `forward_request` 中，把构建并调用接缝的那段（当前是 `body = await request.body()` 起到 `forward = _attach_identities(...)` 止）替换为带入站剔除、audience、签名失败兜底的版本：

```python
    body = await request.body()
    try:
        forward = await _attach_identities(
            ForwardRequest(
                method=request.method,
                url=_target_url(server.base_url, request),
                # Drop Host (httpx sets it from the upstream URL) and any
                # caller-supplied X-Avernet-Principal (forgery guard).
                headers={
                    k: v
                    for k, v in request.headers.items()
                    if k.lower() not in _INBOUND_STRIP
                },
                content=body,
            ),
            identities,
            signer=request.app.state.principal_signer,
            audience=server.name,
        )
    except Exception:
        logger.exception("principal signing failed")
        return _error(500, 1, "principal signing failed")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_forward_seam.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: 跑现有转发相关测试确认无回归**

Run: `.venv/bin/python -m pytest tests/ -q --deselect tests/e2e/baseline/test_health.py -k "forward or seam or identity_pipeline"`
Expected: PASS（无 e2e；集成 `test_identity_pipeline` 仍在——它走 `authenticate`，不触发签名失败路径）

- [ ] **Step 6: ruff + 提交**

```bash
.venv/bin/python -m ruff format src/gateway/community/adapters/web/_forward.py tests/test_forward_seam.py
.venv/bin/python -m ruff check .
git add src/gateway/community/adapters/web/_forward.py tests/test_forward_seam.py
git commit -m "feat(forward): inject signed principal at the seam"
```

---

### Task 6: 集成测试 — 端到端签名注入 + 入站剔除 + 签名失败

**Files:**
- Test: `tests/integration/test_forward_signs_principal.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/integration/test_forward_signs_principal.py`：

```python
"""Integration: the forward path signs the resolved identity and strips inbound fakes."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Mapping

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from gateway.community.adapters.web.app import create_app
from gateway.community.plugins.principal_signer.bare._plugin import _DEV_FALLBACK_KEY
from gateway.community.spi.authn import AppPrincipal, Principal, PrincipalType, ThirdPartyApp
from gateway.community.spi.forwarder import ForwardRequest, ForwardResponse

_PRINCIPAL_HEADER = "X-Avernet-Principal"


class _StubAuthenticator:
    """Returns a fixed app principal for any call; route_security unused here."""

    async def authenticate(
        self, method: str, path: str, creds: object
    ) -> dict[PrincipalType, Principal]:
        return {
            PrincipalType.APP: AppPrincipal(
                tenant="t",
                app=ThirdPartyApp(
                    app_id="a", app_name="A", owners="o", tenant="t"
                ),
            )
        }


class _CapturingForwarder:
    """Captures the outbound ForwardRequest; responds 200 with an empty body."""

    def __init__(self) -> None:
        self.captured: ForwardRequest | None = None

    @asynccontextmanager
    async def forward(self, request: ForwardRequest):
        self.captured = request

        async def _empty_body():
            if False:  # pragma: no cover
                yield

        yield ForwardResponse(status_code=200, headers=[], body=_empty_body())


class _BoomSigner:
    async def sign(
        self, principals: Mapping[PrincipalType, Principal], *, audience: str
    ) -> str:
        raise RuntimeError("boom")


@pytest.fixture
def app_with_capture() -> tuple:
    app = create_app()
    forwarder = _CapturingForwarder()
    app.state.authenticator = _StubAuthenticator()
    app.state.forwarder = forwarder
    return app, forwarder


async def test_forward_signs_principal_with_server_audience(app_with_capture) -> None:
    app, forwarder = app_with_capture
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/openapi/v1/bots/x")

    assert resp.status_code == 200
    assert forwarder.captured is not None
    token = forwarder.captured.headers[_PRINCIPAL_HEADER]
    # `bots` domain → server "agentclaw" (upstreams.yaml).
    decoded = jwt.decode(
        token,
        _DEV_FALLBACK_KEY,
        algorithms=["HS256"],
        audience="agentclaw",
        issuer="gateway",
    )
    assert decoded["aud"] == "agentclaw"
    assert len(decoded["principals"]) == 1
    assert decoded["principals"][0]["type"] == "app"
    assert decoded["principals"][0]["tenant"] == "t"


async def test_forward_strips_inbound_principal_header(app_with_capture) -> None:
    app, forwarder = app_with_capture
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/openapi/v1/bots/x",
            headers={_PRINCIPAL_HEADER: "forged-by-caller"},
        )

    assert resp.status_code == 200
    assert forwarder.captured is not None
    principal_headers = [
        v for k, v in forwarder.captured.headers.items() if k == _PRINCIPAL_HEADER
    ]
    assert len(principal_headers) == 1  # exactly one, and not the forged value
    assert principal_headers[0] != "forged-by-caller"


async def test_forward_returns_500_when_signing_fails() -> None:
    app = create_app()
    app.state.forwarder = _CapturingForwarder()
    app.state.principal_signer = _BoomSigner()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/openapi/v1/bots/x")

    assert resp.status_code == 500
```

- [ ] **Step 2: 跑测试确认通过（此前各 Task 已让接线就绪）**

Run: `.venv/bin/python -m pytest tests/integration/test_forward_signs_principal.py -q`
Expected: PASS（3 passed）。若失败，按报错核对接线（`server.name`、`app.state.principal_signer`）。

- [ ] **Step 3: ruff + 提交**

```bash
.venv/bin/python -m ruff format tests/integration/test_forward_signs_principal.py
.venv/bin/python -m ruff check .
git add tests/integration/test_forward_signs_principal.py
git commit -m "test(forward): integration-test signed principal injection"
```

---

### Task 7: 全量校验

- [ ] **Step 1: 全量 ruff**

Run: `.venv/bin/python -m ruff format --check . && .venv/bin/python -m ruff check .`
Expected: 161+ files already formatted；All checks passed。

- [ ] **Step 2: 全量测试（排除需运行中网关的 e2e）**

Run: `.venv/bin/python -m pytest -q --deselect tests/e2e/baseline/test_health.py`
Expected: 全绿；新增：`test_principal_signer_port.py`(1) + `test_principal_signer.py`(7) + `test_forward_seam.py`(2) + `test_forward_signs_principal.py`(3)。

- [ ] **Step 3: 若有改动收尾提交**

```bash
git status
# 如有未提交的格式化残渣：
git add -A && git commit -m "chore: ruff + green suite for principal signing" || true
```

---

## Self-Review notes

- spec §2 数据流/接线 → Task 4（app.state）+ Task 5（接缝注入 + 入站剔除 + 500）。
- spec §3 SPI → Task 2。
- spec §4 bare 插件/claims/配置/依赖 → Task 1 + Task 3。
- spec §6 测试 → Task 2 冒烟、Task 3 单测、Task 5 seam、Task 6 集成（含入站剔除与签名失败 500）。
- 类型一致：`_attach_identities` 签名（`forward, identities, *, signer, audience`）在 Task 5 定义并在 Task 5/6 一致使用；`build_principal_signer` 在 Task 4 定义并导出；`PrincipalSignerConfig` 字段（`signing_key/kid/issuer/ttl_seconds`）单测与实现一致；`_PRINCIPAL_HEADER = "X-Avernet-Principal"` 在 `_forward.py` 与测试一致。