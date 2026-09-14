# Avernet 内部流量 JWT 认证契约（Internal JWT Wire Contract）

**状态：** 草案 / 待评审
**日期：** 2026-09-14
**范围：** 所有**不经过 gateway** 的组件间（east-west）调用
**相关组件：** `gateway` / `backend` / `bcs`(BCN) / `baas` / `engine` / `evolverun` / `bcsfuse` / `proxy`
**约束：** 仓库架构宪法 `docs/arch/arch.rules.md`（Rule 1 契约即权威 / Rule 7 交付层薄 / Rule 14 配置驱动装配）

---

## 0. 这份文档要解决什么

今天已经有一套签名 JWT 在跑，但它**只覆盖 gateway → 组件这一跳（north-south）**，
由 gateway 单点签发。组件之间互相调用（east-west）目前是**五套互不相干的机制**并存：

| 调用路径 | 现有机制 | 位置 |
| --- | --- | --- |
| gateway → backend / bcs / baas / engine | **签名 JWT**（本文档要推广的那套） | `gateway/.../plugins/principal_signer/bare/_plugin.py` |
| backend → BCN（好友审批回调） | **JWT 改签**（唯一一条已经是 east-west JWT 的路径） | `backend/.../core/gateway_principal/signer.py` |
| → backend `/api/internal/skill-center/*`、`/api/internal/dormant/*` | 静态共享 Bearer token，字符串比对 | `backend/.../adapters/http/skill_center/internal_auth.py`、`.../bot_dormant/auth.py` |
| → evolvetrace `/api/internal/*` | Ed25519 分离签名 + 时间戳 | `evolverun/evolvetrace/server/middleware/signature.ts` |
| baas nginx proxypass | 只有 `target` + `exp` 的 HS256 JWT（无 `iss`/`aud`） | `baas/.../core/utils/secret_utils.py:15` |
| → bcsfuse | 静态 Bearer token，dev 模式直接放行 | `bcsfuse/src/infra/public/auth/simple_token_auth_provider.py` |
| → bcs-http（如 bot visibility PUT） | mock-auth，**完全不带凭证** | — |

**目标：把上表除第一行外的全部收敛到一套契约。** 本文档定义这套契约的线上格式，
并分别从**发送方**（§3）和**接收方**（§4）两个视角给出可直接照抄的实施规范。

> **务必先读 §1.2。** 现有 JWT 承载的是**调用者（最终用户）身份**，不是**组件身份**。
> 要覆盖"所有内部流量"，必须补上组件身份这一类主体 —— 这是当前代码里还不存在的形态。

---

## 1. 核心概念

### 1.1 一句话说清这套 token 是什么

它是一个**短生命周期、HMAC-SHA256 签名、单一受众（single-audience）的 JWT**，
放在 HTTP header `X-Avernet-Principal` 里，随请求传递。

- `iss`（issuer）= **签发这个 token 的组件自己**
- `aud`（audience）= **这个 token 要发给的那个组件**
- `principals` = **这次调用代表的身份集合**

`aud` 单一受众是整套设计的安全支点：一个签给 `backend` 的 token **在 `bcs` 上必须验不过**。
没有这条，共享密钥就等于人人可以扮演任何人调用任何组件。

### 1.2 两类 token：不要混为一谈

| | **A. 用户主体透传（Principal Propagation）** | **B. 组件自身身份（Service Identity）** |
| --- | --- | --- |
| 语义 | "我代表**用户 X** 调你" | "我是**组件 backend** 在调你" |
| `principals` 内容 | `user` / `app` / `bot` / `access_key` | `service`（**待新增**，见 §6.1） |
| 授权依据 | 目标组件按**用户**鉴权 | 目标组件按**组件白名单 + 能力**鉴权 |
| 现状 | ✅ 已实现（gateway 签发 + backend 改签） | ❌ **尚未实现** |
| 签发方式 | **改签（re-sign）**，见 §3.3 | **自签（mint）**，见 §3.4 |

**绝大多数内部调用是 A 类。** 一个请求进了 backend，backend 再去调 bcs，
bcs 需要知道的是**发起这次操作的人是谁**，而不是"backend 在调我"——
否则 bcs 侧所有按用户做的权限判断全部失效，变成"只要是 backend 来的就放行"。

B 类只用于**确实没有用户上下文**的调用：定时任务、后台补数、健康检查、组件间的数据同步。

### 1.3 密钥模型（当前状态与目标状态）

**当前：单一共享 HMAC 密钥（HS256）**，gateway / backend / bcs 持有同一份 secret。

这意味着：
- `aud` 是组件之间**唯一的隔离边界**；
- 任何持有该密钥的组件，**技术上都能伪造任意身份、签给任意组件**。

这正是 `signer.py` 明确**只做改签、拒绝做通用签名器**的原因 —— 原文：

> 一个会对传进来的任意字符串签名的函数，对每个信任该共享密钥的上游而言，
> 就是一个可达的 token 铸造预言机（token-minting oracle）。

**推广到全量内部流量后，这个风险面会线性放大。** §7.3 给出了向非对称密钥（EdDSA/`kid` 分发）
演进的路径。在完成之前，§3.6 的红线是强制的，不是建议。

---

## 2. 线上契约（Wire Contract）

### 2.1 传输

| 项 | 值 |
| --- | --- |
| Header 名 | `X-Avernet-Principal`（HTTP header 大小写不敏感，实现内部一律按小写 `x-avernet-principal` 比较） |
| Header 值 | 一个 **raw compact JWT**，**不带** `Bearer ` 前缀 |
| 出现次数 | **恰好一次**。出现 0 次或 ≥2 次一律拒绝（见 `bcs/.../gateway_principal/verifier.rs`：`"Gateway Principal header must occur exactly once"`） |
| 与 `Authorization` 的关系 | **互不复用**。`Authorization` 留给外部凭证；内部主体一律走本 header |

> ⚠️ 已知不一致：`backend/.../singlebox_engine_adapter.py` 里有一处把 token 放在
> `Authorization: Bearer` 发给 BCS。BCS 的 `PrincipalVerifier` **只读 `x-avernet-principal`**，
> 这处需要纠正。新接入方一律以本表为准。

### 2.2 JOSE Header

```json
{ "alg": "HS256", "typ": "JWT", "kid": "bare" }
```

| 字段 | 要求 | 说明 |
| --- | --- | --- |
| `alg` | **必须** `HS256` | 接收方**钉死**在白名单，绝不读 token 自称的 `alg`（§4.2.1） |
| `typ` | **必须** `JWT` | BCS 会显式校验；接入方一律带上 |
| `kid` | **必须** `bare` | 当前单密钥部署的固定值。BCS 严格比对，不匹配在验签之前就拒（`verifier.rs:127`） |

### 2.3 Claims

```json
{
  "iss": "backend",
  "aud": "bcs",
  "iat": 1789388560,
  "exp": 1789388620,
  "principals": [
    {
      "type": "user",
      "subject": {
        "id": "u-10086",
        "username": "alice",
        "display_name": "Alice",
        "full_name": null,
        "tenant_id": "teamclaw"
      }
    }
  ]
}
```

| Claim | 必填 | 类型 | 规则 |
| --- | --- | --- | --- |
| `iss` | ✅ | **单个字符串** | 签发组件名。**不允许数组**（BCS 显式拒绝数组与非字符串，`verifier.rs:183`） |
| `aud` | ✅ | **单个字符串** | 目标组件名。同样不允许数组 |
| `iat` | ✅ | 整数（Unix 秒） | 必须 `iat < exp`，且 `iat <= now + 5` |
| `exp` | ✅ | 整数（Unix 秒） | 必须 `now < exp + 5` |
| `principals` | ✅ | 非空数组 | 身份集合，见 §2.5 |

**TTL：默认 60 秒**（`gateway` 侧 `PrincipalSignerConfig.ttl_seconds = 60`）。
内部调用不应超过 **300 秒**。TTL 越长，token 泄露后的重放窗口越大。

**时钟偏移（leeway）：固定 5 秒**，两端一致（backend `_LEEWAY_SECONDS = 5`；
bcs `CLOCK_SKEW_SECONDS = 5`）。**这个值不做成配置项** —— 可配置的偏移量意味着
有人可以把它调成几小时。

### 2.4 组件命名（`iss` / `aud` 取值表）

组件名的**权威来源是 gateway 的 `configs/application.yaml` 里 `servers:` 下的 key**。
gateway 签发时直接用 `audience=server.name`（`gateway/.../adapters/web/_forward.py:252`），
所以这一个拼写同时是：签给该组件的 `aud`、以及该组件自签时用的 `iss`。

| 组件 | 规范名 |
| --- | --- |
| API 网关 | `gateway` |
| 主后端 | `backend` |
| BCN / 协作服务 | `bcs` |
| BaaS | `baas` |
| Engine | `engine` |
| Evolvetrace | `evolvetrace` |
| BCSFuse | `bcsfuse` |

**新增组件必须先在 gateway 的 `servers:` 注册名字**，再在各接收方的信任配置里登记，
否则会出现两边拼写不一致而全量 401 的情况。

### 2.5 `principals` 数组

每个元素是一个以 `type` 为判别标签（discriminated union）的对象。
权威 schema：`backend/.../core/gateway_principal/models.py`，
Rust 侧镜像：`bcs/.../gateway_principal/wire.rs`。

**`user` —— 最终用户**
```json
{ "type": "user",
  "subject": { "id": "u-1", "username": "alice",
               "display_name": null, "full_name": null, "tenant_id": null } }
```
- `subject.id`、`subject.username` **必填且非空**；其余可为 `null` 或缺失。
- **`user` 主体本身不携带外层 `tenant`**：用户凭证无法证明这个人此刻代表哪个租户。
- `subject.tenant_id` 是**归属信息（attribution）**，**不是隔离键** —— 任何组件都不得拿它做数据隔离。

**`app` —— 第三方应用**
```json
{ "type": "app", "tenant": "t-1",
  "app": { "app_id": 42, "app_name": "demo", "owners": "team-x",
           "tenant": "t-1", "app_type": "UNKNOWN" } }
```

**`bot` —— Bot / Agent 自身**
```json
{ "type": "bot", "tenant": "t-1",
  "bot": { "bot_uuid": "b-1", "owner_id": "u-1", "app_id": 42,
           "agent_code": "c-1", "tenant": "t-1" } }
```

**`access_key` —— 访问密钥**
```json
{ "type": "access_key", "tenant": "t-1",
  "access_key": { "access_key": "ak-1", "expire_at": "2026-09-14T10:00:00Z" } }
```

**约束（两端一致）：**
- 每种 `type` **最多出现一次**，重复即整体拒绝；
- **未知 `type` 整体拒绝**（不是忽略）；
- 未知**字段**忽略（允许向前兼容地加字段），但**重命名/删除**已声明的必填字段会导致解析失败 → 请求失败关闭；
- `bot`、`app`、`access_key` 的外层 `tenant` **必填非空**；
- **所有出现的外层 `tenant` 必须一致**，不一致整体拒绝；
- `bot.token`、`access_key.access_key_token` 这类**活凭证不得投影进接收方内部模型** ——
  接收方只需要知道"调用者是谁"，不持有秘密是最省事的不泄漏方式。

---

## 3. 发送方：我该怎么签这个 JWT

### 3.1 第一步：先判断你属于哪种场景

```
                这次出站调用，有没有一个"最终用户"在背后？
                                 │
          ┌──────────────────────┴──────────────────────┐
          │ 有                                          │ 没有
          ▼                                             ▼
  入站请求带了 X-Avernet-Principal 吗？          场景 C：组件自身身份
          │                                             （定时任务/后台补数/同步）
   ┌──────┴───────┐                                     → §3.4 自签 service token
   │ 带了          │ 没带（如从 DB/MQ 恢复的上下文）
   ▼              ▼
场景 A：改签      场景 B：受限自签
→ §3.3           → §3.5（**默认禁止**，需走豁免评审）
```

**最重要的一条规则：能改签就绝不自签。**

### 3.2 为什么不能直接把入站 header 原样转发

因为 `aud` 是单一受众。你收到的 token 写着 `aud=backend`，
把它原封不动转给 BCN，BCN 的受众校验**每一次都会失败**，重试多少次都没用。
这正是好友审批回调曾经踩过的坑（见 `backend/.../core/work_orders/callbacks.py:126-129`）。

所以必须**改签（re-address）**：同一批身份、同样的生命周期、换一个信封。

### 3.3 场景 A：改签（re-sign）—— 内部调用的默认做法

**参考实现：`backend/.../core/gateway_principal/signer.py::resign_principal_token`**

#### 铁律：改签之前，必须先完整验证入站 token

```
入站 token ──▶ [完整验签 §4] ──▶ 通过？ ──否──▶ 401，不签任何东西
                                    │
                                   是
                                    ▼
                          [身份集合准入检查]  ← 不能跳过
                                    ▼
                          [替换信封三件套] ──▶ 出站 token
```

**必须先验证，且必须跑完整的身份准入检查。** 否则这个函数就退化成了 §1.3 说的铸造预言机 ——
任何能打到你的调用方，都能借你的手向所有信任共享密钥的组件签发任意身份。
`resign_principal_token` 里那句"结果被刻意丢弃"的 `caller_from_claims(claims)`
就是这个准入闸门，它存在的唯一目的就是不让这件事发生。

#### 哪些 claim 改、哪些不改

| Claim | 动作 | 理由 |
| --- | --- | --- |
| `principals` | **原样保留** | 目标组件要授权的就是这个身份。整件事的意义就是让下游看到和你看到的是同一个调用者 |
| `iat` / `exp` | **原样保留** | 副本和原件**同时过期**。重新打时间戳等于每跳都把 60 秒的凭证续成新的，这是没人批准过的生命周期延长 |
| 其它未知 claim | **原样保留** | 你看不懂的 claim，目标组件可能看得懂。丢掉它是你没有依据做的决定 |
| `aud` | **替换**为目标组件名 | 这就是"改签"本身 |
| `iss` | **替换为你自己的组件名** | 里面的断言仍然是 gateway 的，但**这份副本上的签名是你的**。写 gateway 等于冒领一个你没有的出处 |
| `kid` | **按目标要求设置**，不要从入站 token 复制 | 它指的是"目标应该用哪把钥匙验"，这把钥匙是你的，不是入站 token 有资格断言的 |

#### Python 参考实现

```python
from dataclasses import dataclass
import jwt

_ALGORITHM = "HS256"   # 钉死，不从配置读

@dataclass(frozen=True)
class PrincipalSignerConfig:
    signing_key: str   # 共享 HMAC 密钥
    audience: str      # 目标组件名，如 "bcs"
    issuer: str        # 我自己的组件名，如 "backend"
    key_id: str        # 目标要求的 kid，当前固定 "bare"

def resign_principal_token(token, *, verifier, signer) -> str:
    # 1) 没有密钥就拒签 —— 用空密钥签出来的不是"弱凭证"，是"可伪造凭证"
    if not signer.signing_key:
        raise PrincipalVerificationError(
            f"no principal signing key configured; cannot re-address to aud={signer.audience!r}"
        )

    # 2) 完整验签（签名 / iss / aud / exp / iat / kid / alg）
    claims = decode_principal_token(token, verifier)

    # 3) 身份准入闸门 —— 返回值刻意丢弃，只要它抛异常的副作用
    #    一个我们自己会回 401 的身份集合，绝不能被改签递给下游
    caller_from_claims(claims)

    # 4) 换信封：只动 aud / iss，其余全留
    payload = dict(claims)
    payload["aud"] = signer.audience
    payload["iss"] = signer.issuer

    return jwt.encode(payload, signer.signing_key,
                      algorithm=_ALGORITHM,
                      headers={"kid": signer.key_id})
```

#### TypeScript 参考实现

```ts
import jwt from "jsonwebtoken";

export function resignPrincipalToken(
  token: string,
  verifier: VerifierConfig,
  signer: { signingKey: string; audience: string; issuer: string; keyId: string },
): string {
  if (!signer.signingKey) throw new PrincipalError("no signing key configured");

  const claims = decodePrincipalToken(token, verifier);  // 完整验签
  assertAdmissiblePrincipals(claims.principals);         // 准入闸门

  const payload = { ...claims, aud: signer.audience, iss: signer.issuer };
  return jwt.sign(payload, signer.signingKey, {
    algorithm: "HS256",
    header: { alg: "HS256", typ: "JWT", kid: signer.keyId },
    noTimestamp: true,   // 关键：不要让库覆盖 iat
  });
}
```

> ⚠️ **`noTimestamp: true` 不能漏。** `jsonwebtoken` 默认会自动重写 `iat`，
> 那就破坏了"副本与原件同时过期"这条规则。Python 的 PyJWT 不会覆盖已存在的 `iat`，
> 但**显式传入**仍然更安全。

### 3.4 场景 C：自签组件身份 token（service token）

用于**确实没有用户上下文**的调用。

> **注意：这需要先落地 `service` 主体类型（§6.1），当前代码里还没有。**
> 在它合入之前，无用户上下文的内部调用请继续用现有的静态 Bearer token 方案，
> 不要自行发明 principal 形态。

目标形态：

```json
{
  "iss": "backend",
  "aud": "bcs",
  "iat": 1789388560,
  "exp": 1789388620,
  "principals": [
    { "type": "service",
      "service": { "name": "backend", "purpose": "bot-dormant-sweep" } }
  ]
}
```

```python
def mint_service_token(cfg: PrincipalSignerConfig, *, purpose: str, ttl: int = 60) -> str:
    now = int(time.time())
    claims = {
        "iss": cfg.issuer,          # 我是谁
        "aud": cfg.audience,        # 我发给谁
        "iat": now,
        "exp": now + ttl,           # 短！默认 60s，上限 300s
        "principals": [{
            "type": "service",
            "service": {"name": cfg.issuer, "purpose": purpose},
        }],
    }
    return jwt.encode(claims, cfg.signing_key, algorithm="HS256",
                      headers={"kid": cfg.key_id})
```

`purpose` 是**审计字段，不是权限字段**。接收方按 `service.name` + 具体路由授权，
不得把 `purpose` 当作能力声明来信任。

### 3.5 场景 B：有用户上下文但入站没有 token —— 默认禁止

典型情形：从数据库或消息队列里恢复出"这条任务属于用户 X"，然后要代表 X 调用下游。

**默认禁止自签一个 `user` 主体。** 因为那等于凭空断言一个你并没有验证过的用户身份 ——
这正是共享密钥模型下最危险的一步。

可选的替代方案，按优先级：
1. **把原始凭证随任务一起持久化**（注意 TTL：60 秒的 token 存不住，需要单独的长时凭证设计）；
2. **改用 service token（场景 C）**，由接收方按"组件 + 明确的代办能力"授权，
   在业务参数里显式传 `on_behalf_of_user_id`，由接收方自己去查这个用户的权限；
3. 确实无法避免时，走**架构豁免评审**，并在 `docs/arch/waivers` 下登记。

### 3.6 发送方红线（不可协商）

| ❌ 禁止 | 原因 |
| --- | --- |
| 提供一个"签任意 claims"的通用函数并暴露给业务层 | 铸造预言机（§1.3） |
| 改签前不验签、或跳过身份准入检查 | 同上 |
| 用空密钥签名 | PyJWT 会老老实实用空密钥算 HMAC，签出来的"签名"任何人都能复现 |
| 把 `iss` 写成 `gateway`（除非你就是 gateway） | 冒领出处，破坏审计链 |
| 改签时重打 `iat` / `exp` | 无授权的生命周期延长 |
| 一个 token 写多个 `aud`（数组） | 单一受众是唯一的隔离边界；BCS 会直接拒绝数组 |
| 把 token 写进日志 | 只记 §4.4 的指纹 |
| 把 token 放进 URL query | 会进 access log、Referer、浏览器历史 |
| TTL > 300 秒 | 重放窗口 |
| 向同一个请求塞多个 `X-Avernet-Principal` | 接收方必拒 |
| 转发入站请求时不剥离调用方自带的 `X-Avernet-Principal` | 伪造防护，见 §4.5 |

---

## 4. 接收方：我该怎么校验这个 JWT

### 4.1 核心原则

> **绝不信任未经验证的 `X-Avernet-Principal` header。**
> 它是一个普通的 HTTP header，任何能打到你的进程的人都能设置它。

**没有部分成功，没有降级回退。** 任何一步失败 → 不产出调用者 → 请求 `401`。

### 4.2 校验清单（顺序重要）

```
 0. 密钥已配置？        ── 否 ──▶ 拒绝全部（不是放行！）
 1. header 恰好一次？   ── 否 ──▶ 401
 2. token 非空？        ── 否 ──▶ 401
 3. JOSE header 可解析？── 否 ──▶ 401
 4. alg == HS256（白名单，不读 token 自称）── 否 ──▶ 401
 5. typ == "JWT"        ── 否 ──▶ 401
 6. kid == 期望值       ── 否 ──▶ 401   ← 在验签之前
 7. 验签（共享密钥）     ── 否 ──▶ 401
 8. iss ∈ 信任发行方列表 ── 否 ──▶ 401
 9. aud == 我自己的组件名 ── 否 ──▶ 401
10. exp / iat 必须存在且为整数，leeway=5s ── 否 ──▶ 401
11. principals 解析到强类型模型 ── 否 ──▶ 401
12. 租户一致性检查      ── 否 ──▶ 401
13. 身份集合准入检查    ── 否 ──▶ 401/403
──────────────────────────────────────────
14. 业务授权（这是另一件事，见 §4.6）
```

#### 4.2.1 第 4 步：`alg` 必须钉死

```python
_ALGORITHMS = ("HS256",)   # 硬编码常量，不从配置读
jwt.decode(token, key, algorithms=list(_ALGORITHMS), ...)
```

这是**经典的 JWT 降级攻击**：伪造一个 header 声明 `alg: none`
（或声明某个非对称算法、而把我们的公开材料当成"密钥"），就能验证通过。
**永远不要把 token 自称的 `alg` 喂回给校验库。**

#### 4.2.2 第 6 步：`kid` 在验签之前比对

BCS 的做法（`verifier.rs:127`）是在 `decode` 之前就检查 `kid`。
好处是一个 `kid` 明显不对的 token 根本不进入密码学路径。

注意：**`kid` 是未验证的调用方输入**。它只能用作"便宜的否定判断"和辅助线索，
**绝不能当成来源证明**。看到 `kid=bare` 就推断"是我们的 gateway、只是密钥错了"，
恰恰是伪造者免费获得的推论 —— 在遭遇伪造流量时会误导运维去轮换一把根本没坏的密钥。

#### 4.2.3 第 8/9 步：`iss` 白名单 与 `aud` 精确匹配

- **`aud` 必须精确等于本组件名**，且必须真的开启校验。
- **`iss` 用白名单（数组）**，不要用单值。BCS 已经做对了：
  `default_gateway_principal_issuers()` 返回 `["gateway", "backend"]`（`bcs/.../config.rs:540`）。
  推广到全量内部流量后，每个接收方都会有多个合法上游，单值配置必然不够用。
- `iss` 和 `aud` **必须是单个字符串**，数组或非字符串一律拒绝。

**配置形态建议：**
```yaml
gateway_principal:
  issuers: ["gateway", "backend", "engine"]   # 白名单，允许多个
  audience: "bcs"                             # 精确一个：我自己
  key_id: "bare"
```

> ⚠️ **当前存在一处未被强制的耦合：** backend 把 `_ISSUER = "gateway"` 硬编码在代码里
> （`backend/.../utils/gateway_principal_config.py`），而 gateway 侧的
> `user_config.principal_signer.issuer` 是**可配置的**（默认 `gateway`）。
> 改了一边不改另一边，`/openapi/v1` 的**每一个请求都会 401**。
> 新接入方请一律把 issuers 做成**配置项**，不要硬编码。

#### 4.2.4 第 10 步：时间校验

```rust
fn validate_times(iat: u64, exp: u64, now: u64) -> Result<(), Error> {
    let latest_allowed_iat = now.saturating_add(CLOCK_SKEW_SECONDS);   // 5
    let expiration_with_skew = exp.saturating_add(CLOCK_SKEW_SECONDS); // 5
    if iat >= exp                    // iat 必须严格早于 exp
        || iat > latest_allowed_iat  // 不接受来自"未来"的 token
        || now >= expiration_with_skew {
        return Err(Error::InvalidClaims);
    }
    Ok(())
}
```

三条都要有。只查 `exp` 是不够的：`iat > now` 的 token 说明对面时钟有问题或者在做手脚。

#### 4.2.5 第 11 步：`principals` 解析到强类型

用**判别联合（discriminated union）**解析，让非法状态不可表达：

- Python：`pydantic` + `Field(discriminator="type")`
- Rust：`serde` 的 tagged enum + `serde_path_to_error`（能报出精确的 schema 路径）

关键点：**未知 `type` 必须整体失败，不能静默匹配到某个成员**。

#### 4.2.6 第 12 步：租户一致性

```
收集所有"断言了租户"的主体的外层 tenant
  ├─ 集合为空        → 只有 user 主体 → 落到默认内部租户
  ├─ 集合只有一个值  → 就是它
  └─ 集合有多个值    → 拒绝（矛盾的租户）
```

补充规则：
- `user` 主体**不提供**外层 `tenant`；它的 `subject.tenant_id` 是归属信息，**不建立调用者租户**；
- `subject.tenant_id` 若与外层 tenant 同时存在，两者必须相等，否则拒绝；
- 带租户的 `bot`/`app`/`access_key` **可以**为一个无租户的 `user` 确立租户；
- 落到默认内部租户是**接收方自己的决定**（源于"没有断言"），不是 token 提供的值 —— 这个区别在审计时很重要。

#### 4.2.7 第 13 步：准入（admission）

**"签名有效"不等于"这个调用者能用这个接口"。**

backend 的分层做法值得照抄（`verifier.py::_require_admissible_principal`）：

- **传输无关层只设地板**：拒绝一个"既没有终端用户、也没有应用"的身份集合；
  `access_key` 和 `bot` 在公开面被直接拒绝。
- **每条路由的准入表**决定更细的策略：某个操作是否允许"应用单独调用"（没有用户）。
  **表里没有的操作一律拒绝** —— 默认安全，新写的路由不会因为作者没想到而变得可达。

设计要点：**一条路由不能因为"没声明"而意外变得可达，只能因为显式声明了准入依赖才可达。**

### 4.3 失败处理

| 对**调用方** | 对**运维** |
| --- | --- |
| 全部失败模式收敛成**同一个 `401 Unauthorized`** | 日志里写清楚**具体是哪一步、和什么比对失败** |
| 不透露是签名错、过期、还是受众不对 | 因为不告诉伪造者该改什么，但运维需要能诊断 |

未配置密钥时的行为，**按环境分档（fail-closed 的两种形态）**：

| 环境 | 行为 | 理由 |
| --- | --- | --- |
| `pre` / `prod` | **拒绝启动（boot 失败）** | 一个"看起来健康、但对所有请求回 401"的部署不是降级，是坏了。要在发布时暴露，而不是变成工单 |
| `dev` / `local` / `singlebox` / 测试 | **启动成功，但拒绝每一个请求** | 这些环境合法地没有密钥；保持可启动，同时不放行任何无法验证的请求 |

> **绝不能"未配置就放行"。** 这正是 evolvetrace 的 Ed25519 中间件修掉的漏洞
> （commit `e809cd7b`）：密钥没配时它 `return next()`，导致 `/api/internal/*` 在生产上
> 变成一条**完全无认证的写入路径**。修复后改为非 dev 环境一律 403。
>
> 同理：**不要抄 bcsfuse 的 dev 模式直接放行**（`simple_token_auth_provider.py:47`，
> dev 模式下接受任意 token 包括空串）。要放行也只能由**部署配置**决定，
> 不能由请求内容决定。

### 4.4 日志规范

**永远不要记录：** compact JWT 的任何一段、解码后的 payload、签名、密钥、任何 claim 的值。

**应该记录：**

| 字段 | 算法 | 用途 |
| --- | --- | --- |
| `token_fingerprint` | 整个 compact JWT 的 SHA-256 前 16 个十六进制字符 | 跨组件关联同一个 token 的处理轨迹 |
| `key_fingerprint` | 密钥的 SHA-256 前 8 个十六进制字符 | **两端在启动时各打一条，密钥是否一致就变成 diff 两行日志** |
| 期望值 | 本进程配置里的 `aud` / `iss` / `kid` | 可信，是诊断的依据 |
| 观测值 | token 的 JOSE header，**截断 + `repr` 转义** | 不可信，必须标注为"调用方提供" |

```python
def key_fingerprint(key: str) -> str:
    if not key:
        return "unset"          # 不要把空串的哈希当成一把配置好的密钥展示
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
```

> **`key_fingerprint` 的算法本身是跨组件契约。** 两端必须算得一模一样，
> 否则"我们是不是持有同一把密钥"这个比对会静默失效。两边的测试里都钉了 golden value。

三个必须遵守的细节：
1. **截断 + `repr` 转义**未验证的 header 字段（如 `kid`）。否则精心构造的 `kid`
   可以在日志里伪造出额外的日志行。
2. **密钥指纹可以进日志，但绝不可以进 HTTP 响应** —— 那会让未认证的调用方
   在线确认一次密钥猜测。
3. 密钥短于 **32 字节**（RFC 7518 §3.2）时**必须告警**：对一把弱密钥，
   截断的摘要就是一个离线字典验证器，而能确认共享密钥就意味着能伪造任意调用者身份。

### 4.5 转发时必须剥离入站 header

网关的做法（`gateway/.../adapters/web/_forward.py`）是在转发前**剥掉调用方自带的
`X-Avernet-Principal`**，再塞入自己签发的那个。注释原文：`forgery guard`。

**每一个会转发请求的组件都必须这么做。** 否则一个外部调用方只要自己设一个
`X-Avernet-Principal`，就可能穿过你到达下游。

### 4.6 认证 ≠ 授权

这套 token 只解决**"调用者是谁"**。它**不携带 scope、不携带权限**。

**资源级授权留在各组件的 core 层**（架构设计 §11）。
接收方拿到 `VerifiedCaller` 之后，仍然要：
- 按 `tenant` 做数据隔离；
- 按 `user_id` / `app_id` 做归属判断；
- 按路由做能力判断。

> 一个具体的反面教材：如果 bcs 看到 `iss=backend` 就放行一切，
> 那 backend 侧任何一个越权入口都会直通 bcs。**`iss` 只回答"谁签的"，不回答"能干什么"。**

### 4.7 Python 参考实现

```python
_ALGORITHMS = ("HS256",)
_LEEWAY_SECONDS = 5
_REQUIRED_CLAIMS = ("exp", "iat", "iss")

def decode_principal_token(token: str, config: PrincipalVerifierConfig) -> dict:
    # 0) 没密钥 = 无法区分真 token 和伪造 token。"未配置"的唯一安全解读是"谁都不信"
    if not config.signing_key:
        raise PrincipalVerificationError("no principal signing key is configured")
    if not token:
        raise PrincipalVerificationError("empty principal token")

    try:
        options = {"require": list(_REQUIRED_CLAIMS)}
        claims = jwt.decode(
            token,
            config.signing_key,
            algorithms=list(_ALGORITHMS),   # 钉死
            issuer=config.issuer,
            audience=config.audience,       # 必须开启
            leeway=_LEEWAY_SECONDS,
            options=options,
        )
    except jwt.PyJWTError as exc:
        # 给运维的诊断信息：己方配置（可信）+ 调用方 header（不可信，需标注）
        # 这些都不会到达调用方 —— 所有失败对外统一收敛成 401
        raise PrincipalVerificationError(
            f"principal token rejected: {exc} "
            f"[verifier key fp={config.key_fingerprint}, "
            f"expects aud={config.audience!r} iss={config.issuer!r}; "
            f"{_unverified_token_header(token)}]"
        ) from exc
    return claims


def verify_principal_token(token: str, config) -> VerifiedCaller:
    """完整流程 = 密码学验证 + 身份准入。HTTP 接缝层只应该调这个。"""
    return caller_from_claims(decode_principal_token(token, config))


def caller_from_claims(claims: Mapping[str, Any]) -> VerifiedCaller:
    """前置条件：claims 已经被签名背书过。"""
    principals = _parse_principals(claims.get("principals"))
    _reject_contradictory_tenant(principals)
    _require_admissible_principal(principals)
    return VerifiedCaller(principals=principals)
```

> **拆成两半是有意的**：`decode_principal_token` 只做密码学验证，
> 返回的是**"已认证但尚未准入"**的 claims —— 改签场景需要原始 claims。
> 但**任何 HTTP 接缝层都必须用 `verify_principal_token`**：
> 一个只解码不准入的调用方，验证的只是一个签名，不是一个调用者。

---

## 5. 密钥管理

| 项 | 规范 |
| --- | --- |
| 算法 | HS256（当前）；目标态见 §7.3 |
| 长度 | **≥ 32 字节**（RFC 7518 §3.2）。低于此值必须告警 |
| 来源 | 各组件的 `SecretResolver` / 密钥库。**仓库里不得提交任何默认密钥** |
| 社区版环境变量 | backend: `AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE`；bcs: `AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE` |
| 解析时机 | **启动时一次**，不做请求级的密钥库往返 |
| 轮转 | **两端都需要重启**。当前无热重载 —— 这是已知限制，轮转要走发布流程 |
| 启动日志 | 必须打印 `key fp` / `key len` / `aud` / `iss`（见 §4.4） |

**排查"两边密钥不一致"的标准流程：**
1. 比对两端启动日志里的 `key fp`；
2. 不一致 → 密钥不同（注意 `key len` 能暴露多余空白字符导致的差异）；
3. 一致但仍然 401 → 去查 `aud` / `iss` / `kid` 的配置，而不是去轮换密钥。

> ⚠️ **仓库里仍有一个硬编码的开发密钥** `avernet-dev-signing-key-NOT-FOR-PROD`
> （`backend/.../singlebox_engine_adapter.py`）。它只允许出现在 singlebox / e2e 路径，
> **绝不能进入任何共享环境**，新接入方不得复制这个模式。

---

## 6. 推广到全量内部流量：待办与已知缺口

### 6.1 缺口 1：没有"组件身份"主体类型（阻塞项）

当前 `principals` 只有 `user` / `bot` / `app` / `access_key` 四种，
而 backend 的准入检查明确要求身份集合里**必须有 user 或 app**。
**一个没有用户上下文的组件间调用，今天在契约里无法表达。**

**建议：新增第五种主体 `service`。**

```json
{ "type": "service", "service": { "name": "backend", "purpose": "bot-dormant-sweep" } }
```

需要同步落地的内容：
- [ ] `backend/.../core/gateway_principal/models.py` 加 `ServicePrincipal`
- [ ] `bcs/.../gateway_principal/wire.rs` 加对应的 Rust 镜像
- [ ] `bcs/api-contracts/v1/gateway-principal/contract.md` 更新契约
- [ ] 各接收方的准入表明确**哪些路由接受 service 主体**（默认全部拒绝）
- [ ] `service` 主体**不得**被投影成某个用户；`purpose` 只用于审计，不作为权限依据

### 6.2 缺口 2：baas 不校验 `aud`，也不校验 `iss`（安全问题）

`baas/.../api/api_gateway/_jwt.py:31` 传了 `options={"verify_aud": False}`，
而 `baas/.../adapters/web/routers/gateway/dependencies.py`（全文 166 行）**从未检查 `iss`**，
只取 `principals[0].access_key` 和 `tenant`。

**后果：一个签给 `aud=backend` 的 token，baas 会接受。**
整套设计赖以成立的组件间隔离在这个面上不成立。

- [ ] 开启 `verify_aud`，`audience = "baas"`
- [ ] 增加 `iss` 白名单校验
- [ ] 补一条回归测试：签给其它 `aud` 的 token 必须被 baas 拒绝

### 6.3 缺口 3：`iss` 硬编码耦合

backend 的 `_ISSUER = "gateway"` 是代码常量，而 gateway 的
`user_config.principal_signer.issuer` 是配置项。两者必须同版本一起改，否则全量 401。

- [ ] 把 backend 的 issuer 改成**可配置的白名单**（对齐 BCS 的 `issuers: Vec<String>`）

### 6.4 缺口 4：header 位置不一致

`singlebox_engine_adapter.py` 用 `Authorization: Bearer` 发 principal token，
而 BCS 只读 `x-avernet-principal`。

- [ ] 统一到 `X-Avernet-Principal`

### 6.5 各存量机制的迁移路径

| 现有机制 | 迁移动作 |
| --- | --- |
| backend `/api/internal/skill-center/*`、`/api/internal/dormant/*` 静态 Bearer | 换成 `service` token（§3.4）。静态 token 无过期、无受众、泄露后永久有效 |
| evolvetrace `/api/internal/*` Ed25519 签名 | 可保留（它已经是**非对称**的，安全性反而更强）。中期与 §7.3 的非对称方案合流 |
| baas proxypass JWT（仅 `target` + `exp`） | 补 `iss` / `aud` / `kid` / `principals`，或明确划为 nginx 层的独立契约、不纳入本文档范围 |
| bcsfuse 静态 token + dev 放行 | 换成 `service` token；**删除"dev 模式接受任意 token"** |
| bcs-http mock-auth（无凭证） | 必须加上校验。这是目前风险最高的一条 |

### 6.6 迁移策略（灰度）

为避免一刀切造成全线 401，按接收方逐个推进：

```
阶段 1  接收方同时接受【旧机制】和【新 JWT】，新 JWT 验证成功则记一条 metric
阶段 2  发送方切到新 JWT；监控旧机制的调用量降到 0
阶段 3  接收方关闭旧机制（配置开关，可回滚）
阶段 4  删除旧机制代码
```

**阶段 1 绝不能让新 JWT 验证失败时静默回退到旧机制** ——
否则一个伪造的 token 只要故意构造成验不过，就能退回到更弱的那条路径。
两条路径必须**各自独立判定**，任一通过即放行，但失败原因要分别记录。

---

## 7. 验收与演进

### 7.1 每个接入组件必须有的测试

**接收方：**
- [ ] 合法 token → 200，且解析出的调用者身份正确
- [ ] `alg: none` / `alg: RS256` 的伪造 token → 401
- [ ] 签给**别的 `aud`** 的 token → 401 ← **这条最关键**
- [ ] `iss` 不在白名单 → 401
- [ ] `kid` 不匹配 → 401
- [ ] `typ` 不是 `JWT` → 401
- [ ] 已过期 / `iat` 在未来 / `iat >= exp` → 401
- [ ] 错误密钥签名 → 401
- [ ] 未知 principal `type` → 401
- [ ] 重复 principal `type` → 401
- [ ] 矛盾的 tenant → 401
- [ ] header 出现 0 次 / 2 次 → 401
- [ ] 未配置密钥：`prod` 启动失败；`dev` 启动成功但全量 401
- [ ] `key_fingerprint` 的 golden value（跨组件契约，两端钉死）

**发送方：**
- [ ] 改签保留 `principals` / `iat` / `exp` / 未知 claim 原封不动
- [ ] 改签只替换 `aud` / `iss` / `kid`
- [ ] 入站 token 无效时**不产出任何 token**
- [ ] 入站身份集合不可准入时**不产出任何 token**
- [ ] 无密钥时拒签而非用空密钥签
- [ ] 转发时剥离调用方自带的 `X-Avernet-Principal`

### 7.2 契约测试

按 Rule 1（契约即权威），`principals` 的 wire shape 必须有**跨语言的契约 fixture**，
Python 和 Rust 两侧都跑同一批样本。参考 `bcs/.../gateway_principal/tests.rs` 里的
`ContractFixture` 模式和 `docs/arch/protocol-contract-tests.md`。

### 7.3 演进方向：从共享 HMAC 到非对称签名

**共享 HMAC 的根本问题：验证方拥有伪造能力。** 组件数量越多，这个风险面越大 ——
任何一个组件被攻破，攻击者就获得了向**所有**组件伪造**任意**身份的能力。

目标形态：

| | 当前 | 目标 |
| --- | --- | --- |
| 算法 | HS256（对称） | **EdDSA / Ed25519**（非对称） |
| 密钥 | 一把共享 secret | **每个组件一对密钥，只发布公钥** |
| `kid` | 固定 `bare` | **每个组件 + 每代密钥一个 `kid`** |
| 公钥分发 | — | JWKS 端点 或 配置下发 |
| 轮转 | 两端重启 | 多 `kid` 并存，可在线滚动 |
| 组件被攻破的影响 | **可伪造任意身份给任意组件** | 仅限该组件自己的身份 |

evolvetrace 已经在用 Ed25519（`EVOLVETRACE_INTERNAL_PUBLIC_KEY_B64`），
可以作为这条路径的先行样板。

**契约设计上已经为此留了余地**：`kid` 从第一天起就在协议里，并且是**严格比对**的。
切换时只需要扩展信任配置从"一个 `kid` + 一把对称密钥"变成"`kid` → 公钥"的映射，
§2 / §3 / §4 的其余部分完全不变。

---

## 附录 A：Claim 速查

| Claim | 必填 | 发送方 | 接收方 |
| --- | --- | --- | --- |
| `alg`(header) | ✅ | 固定 `HS256` | 白名单钉死，不读 token 自称 |
| `typ`(header) | ✅ | 固定 `JWT` | 精确比对 |
| `kid`(header) | ✅ | 目标要求的值（当前 `bare`） | 验签**之前**精确比对 |
| `iss` | ✅ | **自己的**组件名 | 白名单（数组）；必须是单字符串 |
| `aud` | ✅ | **目标**组件名 | 精确 == 自己；必须是单字符串 |
| `iat` | ✅ | 改签时保留原值 | `iat < exp` 且 `iat <= now+5` |
| `exp` | ✅ | 默认 +60s，上限 +300s；改签保留 | `now < exp+5` |
| `principals` | ✅ | 改签保留；自签按 §2.5 | 强类型解析 + 租户一致 + 准入 |
| 其它 claim | — | 改签时**原样保留** | 忽略未知字段 |

## 附录 B：常见问题

**Q：我能不能把入站的 `X-Avernet-Principal` 直接透传给下游？**
不能。`aud` 是单一受众，下游必定拒绝。必须改签（§3.3）。

**Q：改签会不会延长 token 生命周期？**
不会，也不允许。`iat` / `exp` 原样保留，副本和原件同时死。

**Q：我的调用没有用户上下文怎么办？**
用 service token（§3.4）。但要注意 `service` 主体类型**尚未落地**（§6.1），
在此之前继续用现有的静态 token 方案，不要自行发明 principal 形态。

**Q：60 秒 TTL 太短，我的异步任务跑几分钟怎么办？**
不要靠延长 TTL 解决。异步任务属于场景 B（§3.5），应该用 service token
+ 在业务参数里显式传 `on_behalf_of_user_id`，由接收方自己校验该用户的权限。

**Q：验证失败了，我该查什么？**
先比对两端启动日志的 `key fp`。一致就去查 `aud` / `iss` / `kid` 配置，
**不要**去轮换密钥。日志里的 JOSE header 是调用方提供的，不能当证据。

**Q：为什么 `user` 主体不带 `tenant`？**
因为用户凭证无法证明这个人此刻代表哪个租户。租户由机器身份（app / bot / access_key）断言。

**Q：签名验过了，是不是就能放行了？**
不是。认证 ≠ 授权（§4.6）。还要做租户隔离、归属判断和路由能力判断。

## 附录 C：权威代码位置

| 内容 | 位置 |
| --- | --- |
| 签发（gateway，参考实现） | `src/gateway/src/gateway/community/plugins/principal_signer/bare/_plugin.py` |
| 注入 header + 剥离伪造 header | `src/gateway/src/gateway/community/adapters/web/_forward.py` |
| 改签（参考实现） | `src/backend/src/agentclaw/community/core/gateway_principal/signer.py` |
| 校验（Python 参考实现） | `src/backend/src/agentclaw/community/core/gateway_principal/verifier.py` |
| Principal wire schema（Python） | `src/backend/src/agentclaw/community/core/gateway_principal/models.py` |
| 校验（Rust 参考实现） | `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/verifier.rs` |
| Principal wire schema（Rust） | `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/wire.rs` |
| 已有契约文档 | `src/bcs/api-contracts/v1/gateway-principal/contract.md` |
| 配置绑定（backend） | `src/backend/src/agentclaw/community/utils/gateway_principal_config.py` |
| 信任配置（bcs） | `src/bcs/crates/bootstrap/bcs/src/config.rs` |
| 原始认证设计 | `src/gateway/docs/2026-07-21-auth-design.md` |
| 架构宪法 | `docs/arch/arch.rules.md` |
