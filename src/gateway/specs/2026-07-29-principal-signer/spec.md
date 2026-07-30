# Principal 签发与转发（网关侧）— 设计 spec

- 日期：2026-07-29
- 状态：草案（待评审）
- 关联：`docs/2026-07-21-auth-design.md` §7.1；接缝 `adapters/web/_forward.py::_attach_identities`

## 1. 背景与目标

认证管线 `Authenticator.authenticate(...)` 产出 `dict[PrincipalType, Principal]`，
当前在转发接缝 `_attach_identities` 处是一个**故意留空的 no-op**：解析出的身份集合
虽在此可用，但**不注入任何身份头**，请求原样转发上游。这是「密码学签名尚未落地」期间
的安全占位——因为组件绝不能信任一个裸的 `Principal` 头（§7.1）。

本工作项落地**网关侧签发**：把解析出的身份集合用短时 JWT 签名后，作为
`X-Avernet-Principal` 头注入出站请求，使下游组件退化为「验签 + 反序列化」即可
（即 `auth.mode=none` 的确切含义）。

### 范围

- **纳入**：`PrincipalSigner` SPI；`bare` 味型（HMAC HS256）实现；env 驱动的密钥/TTL 配置；
  接缝 `_attach_identities` 由 no-op 改为真注入；入站 `X-Avernet-Principal` 头剔除
  （防调用方伪造）；pyjwt 依赖；单元/集成测试。
- **不纳入**（独立 workstream / 下游组件仓库）：组件侧 `PrincipalVerifier`（验签 +
  `aud`/`exp` 校验 + 投影成域 DTO）；非对称 `sofa` 味型（RS256 + KMS/密钥轮换）——
  本期只预留 SPI，不实现；`jti` + nonce 缓存等更强防重放。

### 已确认的设计取舍

1. 范围：仅网关侧签发，验签在下游组件仓库。
2. 载荷：单 JWT 承载全部在场身份。
3. 签名实现：引入 pyjwt，bare 走 HMAC HS256；非对称 sofa 仅预留 SPI。
4. 防重放：仅 `exp` + `aud`，不绑定 `method`/`path`/`body`。

## 2. 数据流与接线

```
forward_request(request):
  server     = domain_map.resolve(path)                       # 已有；server.name 用作 aud
  identities = await authenticator.authenticate(method, path, bundle)   # 已有
  forward    = ForwardRequest(method, url, headers=caller_headers_minus_host_and_principal, body)
  forward    = await _attach_identities(forward, identities,
                                        signer=request.app.state.principal_signer,
                                        audience=server.name)   # ← 真正注入
  stream forwarder.forward(forward) … 略
```

### 接缝 `_attach_identities`（由 no-op → 真注入）

签名改为：

```python
async def _attach_identities(
    forward: ForwardRequest,
    identities: dict[PrincipalType, Principal],
    *,
    signer: PrincipalSigner,
    audience: str,
) -> ForwardRequest: ...
```

行为：

- `identities` 为空 → 原样返回，不加头。
- 非空 → `token = await signer.sign(identities, audience=audience)`；用
  `dataclasses.replace(forward, headers={**forward.headers, _PRINCIPAL_HEADER: token})`
  装回 frozen 的 `ForwardRequest`。
- `signer.sign` 抛错 → 网关自身失败，返回 `_error(500, 1, "principal signing failed")`，
  **不调用 forwarder**（fail-closed）。

### 安全要点

- **入站防伪**：构造出站 `ForwardRequest.headers` 时，连同 `host` 一并剔除入站
  `x-avernet-principal`，防止调用方伪造身份头注入下游。
- **后端契约**：组件绝不能信任裸 `X-Avernet-Principal` 头，必须验签（属组件侧实现，
  本设计不交付，但在 spec/README 中写明契约）。

## 3. SPI

新包 `gateway.community.spi.principal_signer`：

```python
# _ports.py
from typing import Mapping, Protocol
from gateway.community.spi.authn import Principal, PrincipalType

class PrincipalSigner(Protocol):
    """Sign the resolved identity set into a short-lived token for `audience`.

    Returns a compact JWS (JWT) string. Implementations MUST NOT raise on
    normal input; signing-failure is a gateway-internal error handled by the
    caller (fail-closed: do not forward).
    """

    async def sign(
        self, principals: Mapping[PrincipalType, Principal], *, audience: str
    ) -> str: ...
```

- `sign` 为 `async`，与 `find_bot_by_token` 等既有端口一致；为将来 KMS/HSM 异步签名预留。
- `PrincipalType` / `Principal` 复用 `spi.authn`。

`__init__.py` 导出 `PrincipalSigner`。

## 4. bare 插件与配置

`gateway.community.plugins/principal_signer/bare/_plugin.py`：

```python
@dataclass(frozen=True)
class PrincipalSignerConfig:
    signing_key: str
    kid: str = "bare"
    issuer: str = "gateway"
    ttl_seconds: int = 60

class BarePrincipalSigner(PrincipalSigner):
    def __init__(self, cfg: PrincipalSignerConfig) -> None: ...

    async def sign(self, principals, *, audience) -> str:
        now = self._now()                       # 注入式时钟，测试可固定
        claims = {
            "iss": self._cfg.issuer,
            "aud": audience,
            "iat": now,
            "exp": now + self._cfg.ttl_seconds,
            "principals": [p.model_dump(mode="json") for p in principals.values()],
        }
        return jwt.encode(claims, self._cfg.signing_key, algorithm="HS256",
                          headers={"kid": self._cfg.kid})
```

### Claims 约定

| claim | 含义 |
| --- | --- |
| `iss` | 签发方 = 网关（默认 `gateway`，可配） |
| `aud` | 目标服务 = `server.name`（如 `secbaas`/`agentclaw`）——绑定受众 |
| `iat`/`exp` | 短 TTL，默认 60s，可配 |
| `kid` | 密钥 id，支持轮换；**置于 JOSE header**（JWT 惯例），不在 claims 里重复 |
| `principals` | 各 `Principal` 的 `model_dump(mode="json")` 列表（判别联合按 `type` tag，下游可逐项反序列化） |

- `jti` 不加（YAGNI——选择「仅 exp+aud」防重放）。
- `principals` 用列表而非按 `PrincipalType` 锚定的字典，以便下游直接用
  `TypeAdapter(Principal)` 逐项反序列化，且与 `type` tag 不冲突。

### 配置来源

沿用 `DATABASE_URL` 的 env 约定：

- `AVERNET_PRINCIPAL_SIGNING_KEY`：HMAC 密钥（必填，bare 见下）。
- `AVERNET_PRINCIPAL_SIGNING_KID`：密钥 id，默认 `bare`。
- `AVERNET_PRINCIPAL_SIGNING_TTL`：TTL 秒，默认 `60`。

**未设密钥时**的取舍（已确认）：bare 组合根使用一个固定 **dev fallback** 密钥并
`logger.warning(...)`（明示「非生产」），保证单盒/测试可跑。sofa 非对称味型将**强制**
真实密钥/KMS——本期不实现。

### 依赖

`pyproject.toml` 增加 `pyjwt>=2.8.0`。HS256 无需 `cryptography`；将来实现非对称 sofa
味型时再补 `cryptography`。

## 5. 组装与接线落地

- `bootstrap` 新增 `build_principal_signer() -> PrincipalSigner`：从 env 构造
  `PrincipalSignerConfig`，返回 `BarePrincipalSigner`。在 `create_app` 中挂到
  `app.state.principal_signer`，与 `app.state.authenticator` 并列。
- `_attach_identities` 改 `async`，增加 `*, signer, audience` 入参；`forward_request`
  改为 `await`，并传入 `audience=server.name`。
- 出站 `ForwardRequest.headers` 构造时，连同 `host` 一并剔除入站 `x-avernet-principal`。
- `PrincipalSigner` SPI 已就位，后续 sofa/KMS 实现替换 bare，不动接线（flavor 中立）。

## 6. 测试

- **替换** `tests/test_forward_seam.py`：旧「签名落地前不得注入身份头」的不变量回归
  （其本就是占位不变量）改为新行为测试：
  - `identities` 非空 → 注入 `X-Avernet-Principal`；`jwt.decode(token, key,
    algorithms=["HS256"], audience=server_name)` 成功；`principals` 与输入一致；
    `exp - iat == ttl`；`iss/aud/kid` 正确。
  - `identities` 空 → 不加头、其余不变。
  - 入站自带 `x-avernet-principal` → 被剔除、不透传到出站。
- **新建** `tests/unit/plugins/test_principal_signer.py`：裸签发器单测——claims 断言、
  TTL、`kid` 入 JOSE header、不同 `audience` 隔离、`model_dump(mode="json")` 往返一致性。
- **集成**：用一个捕获 `ForwardRequest` 的 fake forwarder，断言转发链路端到端带上可验签
  的头、`aud == server.name`。
- **异常路径**：注入一个抛错的 signer → `forward_request` 返回 500、不调用 forwarder。

## 7. 非目标 / 后续

- 组件侧 `PrincipalVerifier`（下游仓库）。
- 非对称 `sofa` 味型（RS256 + KMS + JWKS + `kid` 缓存轮换）。
- `method+path` / body 摘要 防重放（当前仅 exp+aud）。
- `jti` + nonce 缓存防重放。
- 出站是否继续透传调用方 `Authorization` 等原始凭据——属另一议题，本期不动。