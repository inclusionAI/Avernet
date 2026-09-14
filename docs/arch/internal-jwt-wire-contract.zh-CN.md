# 内部调用 JWT 认证（Internal JWT）

组件之间互相调用（不经过 gateway）时，用一个短生命周期的签名 JWT 表明"这次调用代表谁"。

- Header：`X-Avernet-Principal`，值是 raw JWT（**不带** `Bearer ` 前缀），只能出现一次
- `iss` = 签发的组件自己，`aud` = 要发给的组件
- **`aud` 是组件间唯一的隔离边界**：签给 `backend` 的 token 在 `bcs` 上必须验不过

```json
// header
{ "alg": "HS256", "typ": "JWT", "kid": "bare" }

// payload
{
  "iss": "backend",           // 我是谁
  "aud": "bcs",               // 发给谁
  "iat": 1789388560,
  "exp": 1789388620,          // 默认 +60s，最长 +300s
  "principals": [             // 这次调用代表谁
    { "type": "user",
      "subject": { "id": "u-1", "username": "alice",
                   "display_name": null, "full_name": null, "tenant_id": null } }
  ]
}
```

组件名取 gateway `configs/application.yaml` 里 `servers:` 的 key：
`gateway` / `backend` / `bcs` / `baas` / `engine`。

---

## 一、发送方

### 情况 1：我在代表用户调下游（绝大多数情况）

入站请求带了 `X-Avernet-Principal` → **改签（re-sign）**，不要自己造一个。

因为 `aud` 是单一受众：你收到的 token 写着 `aud=backend`，直接转发给 BCN 必然 401。

```python
import jwt

def resign(token: str, *, verifier, signer) -> str:
    """把入站 token 改寄给下一个组件。"""
    if not signer.signing_key:
        raise PrincipalVerificationError("no signing key")

    # 1) 必须先完整验证入站 token（见下面「接收方」）
    claims = decode_principal_token(token, verifier)

    # 2) 必须跑身份准入检查。返回值不用，只要它抛异常的副作用。
    #    少了这两步，这个函数就变成了"帮任何人签任何身份"的铸造机。
    caller_from_claims(claims)

    # 3) 只换信封：aud / iss / kid
    #    principals、iat、exp、以及所有看不懂的 claim 全部原样保留
    #    —— 重打 iat/exp 等于每跳都把 60 秒的凭证续一次命
    payload = {**claims, "aud": signer.audience, "iss": signer.issuer}
    return jwt.encode(payload, signer.signing_key,
                      algorithm="HS256", headers={"kid": signer.key_id})
```

| claim | 改不改 |
|---|---|
| `principals` / `iat` / `exp` / 其它 | **原样保留** |
| `aud` | 换成目标组件名 |
| `iss` | 换成**我自己**的组件名（签名是我盖的） |
| `kid` | 按目标要求设，不要从入站 token 抄 |

TypeScript 版注意 `noTimestamp: true`，否则库会偷偷重写 `iat`：

```ts
const payload = { ...claims, aud: signer.audience, iss: signer.issuer };
jwt.sign(payload, signer.signingKey, {
  algorithm: "HS256",
  header: { alg: "HS256", typ: "JWT", kid: signer.keyId },
  noTimestamp: true,
});
```

参考实现：`src/backend/src/agentclaw/community/core/gateway_principal/signer.py`

### 情况 2：没有用户上下文（定时任务、后台补数）

```python
def mint_service_token(cfg, *, purpose: str, ttl: int = 60) -> str:
    now = int(time.time())
    claims = {
        "iss": cfg.issuer,       # 我自己
        "aud": cfg.audience,     # 目标组件
        "iat": now,
        "exp": now + ttl,
        "principals": [{"type": "service",
                        "service": {"name": cfg.issuer, "purpose": purpose}}],
    }
    return jwt.encode(claims, cfg.signing_key,
                      algorithm="HS256", headers={"kid": cfg.key_id})
```

> ⚠️ **`service` 这个 principal 类型目前还不存在**，需要先加（见文末）。在它落地之前，
> 无用户上下文的调用继续用现有的静态 Bearer token，不要自己发明 principal 形态。

### 情况 3：有用户、但入站没 token（如从 DB/MQ 恢复的任务）

**不要自签一个 `user` 主体** —— 那是凭空断言一个你没验证过的身份。
改用情况 2 的 service token，在业务参数里显式传 `on_behalf_of_user_id`，让接收方自己查这个用户的权限。

---

## 二、接收方

```python
_ALGORITHMS = ("HS256",)   # 钉死。绝不能用 token 自称的 alg，那是经典降级攻击
_LEEWAY = 5                # 时钟偏移，两端一致，不要做成配置项

def decode_principal_token(token: str, cfg) -> dict:
    # 没配密钥 = 分不清真假 token。唯一安全的解读是"谁都不信"，不是"都放行"
    if not cfg.signing_key:
        raise PrincipalVerificationError("no signing key configured")
    if not token:
        raise PrincipalVerificationError("empty token")

    try:
        return jwt.decode(
            token, cfg.signing_key,
            algorithms=list(_ALGORITHMS),
            issuer=cfg.issuer,          # 白名单
            audience=cfg.audience,      # 必须开！精确等于我自己的组件名
            leeway=_LEEWAY,
            options={"require": ["exp", "iat", "iss"]},
        )
    except jwt.PyJWTError as exc:
        # 日志里写清楚己方配置（可信），方便运维定位；对外一律 401
        raise PrincipalVerificationError(
            f"rejected: {exc} [key fp={cfg.key_fingerprint}, "
            f"expects aud={cfg.audience!r} iss={cfg.issuer!r}]"
        ) from exc


def verify_principal_token(token: str, cfg) -> VerifiedCaller:
    """HTTP 层只该调这个：验签 + 准入。"""
    return caller_from_claims(decode_principal_token(token, cfg))


def caller_from_claims(claims) -> VerifiedCaller:
    principals = _parse_principals(claims.get("principals"))  # 强类型解析，未知 type 直接拒
    _reject_contradictory_tenant(principals)                  # 租户必须一致
    _require_admissible_principal(principals)                 # 这个调用者能不能用这个接口
    return VerifiedCaller(principals=principals)
```

FastAPI 里这么用：

```python
async def require_caller(
    x_avernet_principal: str = Header(...),
) -> VerifiedCaller:
    try:
        return verify_principal_token(x_avernet_principal,
                                      get_principal_verifier_config())
    except PrincipalVerificationError:
        raise HTTPException(401, "Unauthorized")   # 所有失败都收敛成同一个 401


@router.post("/openapi/v1/something")
async def handler(caller: VerifiedCaller = Depends(require_caller)):
    # caller.tenant / caller.user_id / caller.app_id
    ...
```

配置长这样（`iss` 用**数组白名单**，因为一个组件通常有多个合法上游）：

```yaml
gateway_principal:
  issuers: ["gateway", "backend"]   # 信任谁签的
  audience: "bcs"                   # 我自己，精确匹配
  key_id: "bare"
```

参考实现：`.../core/gateway_principal/verifier.py`（Python）、
`src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/verifier.rs`（Rust，额外在验签前校验了 `typ` 和 `kid`）

### 转发请求时记得剥掉调用方自带的 header

```python
STRIP = {"host", "x-avernet-principal"}
headers = {k: v for k, v in request.headers.items() if k.lower() not in STRIP}
# 然后再塞入自己签发的那个
```

否则外部调用方自己设一个 `X-Avernet-Principal` 就能穿过你打到下游。

### 验签通过 ≠ 有权限

这个 token 只回答"调用者是谁"，**不带 scope、不带权限**。
拿到 `VerifiedCaller` 之后仍然要按 `tenant` 做数据隔离、按 `user_id` 判归属。

> 看到 `iss=backend` 就放行一切，等于 backend 侧任何一个越权入口都直通你。

---

## 三、密钥从哪来（MIST）

签发和校验用的是**同一把共享 HMAC 密钥**，存在 MIST 上：

```
other_manual_teamclawgw_principal_signing_key
```

**新组件接入前，要先把自己加进这个 key 的 MIST 应用列表**，否则拉不到值。
拉不到的后果不是报错，是解析成空字符串 —— 而空密钥被当作「谁都不信」，
于是服务看起来是健康的，但每个请求都 401。

各组件通过自己的配置项指向这个名字，值在**启动时解析一次**：

| 组件 | 配置项 |
|---|---|
| gateway | `user_config.principal_signer.secret_name` |
| backend | `secret_names.gateway_principal_signing_key` |
| bcs | `gateway_principal.signing_key_secret` |

> 仓库里提交的默认值是**通用名**（`principal_signing_key` 之类），
> 真正的 `other_manual_*` 名字只写在 corp 环境 overlay 里。不要把它硬编码进源码。

密钥本身要求 **≥32 字节**（RFC 7518 §3.2）。轮转要两端一起重启，没有热加载。

排查「两边验不过」：先比启动日志里的 `key fp=sha256(key)[:8]`，
两端 diff 这一行就知道密钥是不是同一把；一样的话就去看 `aud` / `iss` / `kid` 配置，
不要上来就轮换密钥。

---

## 四、红线

| ❌ | 为什么 |
|---|---|
| 提供一个"签任意 claims"的通用函数 | 谁都能借你的手签任意身份给任意组件 |
| 改签前不验签 / 跳过准入检查 | 同上 |
| 用空密钥签 | PyJWT 会照签，签出来的"签名"人人可复现 |
| 信任 token 自称的 `alg` | `alg: none` 直接绕过 |
| `aud` 写数组、或不校验 `aud` | 组件间隔离就没了 |
| 改签时重打 `iat` / `exp` | 无授权的生命周期延长 |
| 没配密钥就放行 | 这正是 `e809cd7b` 修掉的漏洞：`/api/internal/*` 曾是一条无认证写入路径 |
| token 进日志 / 进 URL query | 泄露。日志只记指纹：`sha256(token)[:16]` |
| TTL > 300s | 重放窗口 |

仓库里不准提交默认密钥。

---

## 五、已知缺口

推广到全量内部流量之前需要先解决：

1. **没有 `service` principal 类型**（阻塞）。现在 `principals` 只有
   `user`/`bot`/`app`/`access_key`，且准入检查要求必须有 user 或 app ——
   没有用户上下文的调用在契约里根本没法表达。
2. **baas 不校验 `aud` 也不校验 `iss`**。`api/api_gateway/_jwt.py` 传了
   `verify_aud: False`，`routers/gateway/dependencies.py` 从不看 `iss` ——
   签给 `aud=backend` 的 token 它照收。
3. **backend 的 `iss` 是硬编码的**（`_ISSUER = "gateway"`），而 gateway 那边是可配置的。
   改一边不改另一边，全量 401。
4. **header 位置不统一**。`singlebox_engine_adapter.py` 用 `Authorization: Bearer` 发，
   而 BCS 只读 `x-avernet-principal`。

## 代码位置

| | |
|---|---|
| 签发（gateway） | `src/gateway/.../plugins/principal_signer/bare/_plugin.py` |
| 改签 | `src/backend/.../core/gateway_principal/signer.py` |
| 校验（Python） | `src/backend/.../core/gateway_principal/verifier.py` |
| 校验（Rust） | `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/verifier.rs` |
| principal schema | `src/backend/.../core/gateway_principal/models.py` |
| 已有契约 | `src/bcs/api-contracts/v1/gateway-principal/contract.md` |
| 原始设计 | `src/gateway/docs/2026-07-21-auth-design.md` |
