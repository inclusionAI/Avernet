# `/openapi/v1` 公开 API 使用文档

[English](usage.md) | **简体中文**

_这是一份使用文档，不是 API 参考手册。_ 参考手册是网关对外提供的 OpenAPI 文档；
这份文件讲的是那份文档说不出来的东西：什么场景该带什么凭证、请求到达后端之前网关
对它做了什么、应用身份怎么拿到"代表用户操作其 Bot"的授权，以及调用被拒绝时该看
哪里。

相关文档：

- [`README.md`](README.md) —— 交付状态板与工程交接文档，面向开发这套接口的人。
- [`engine-surface.md`](engine-surface.md) —— engine runtime（Track C）分组的
  逐端点清单。
- `src/gateway/docs/2026-07-21-auth-design.md` —— 本实现所依据的认证设计。

---

## 1. 一次调用的完整形状

所有对外流量都走网关。`/openapi/v1` 只能经由网关访问：后端会拒绝任何不是经由网关
认证路径到达的请求——那样的请求没有可供验签的 principal。

```text
   你的客户端                 网关                              后端
   ──────────                 ────                              ────
   凭证（header） ─────────▶  1. 解析 domain
                                （/openapi/v1 之后的第一段
                                  → 目标 upstream）
                              2. 认证：按该路由声明的
                                 身份类型逐条跑 identity chain
                              3. 剥掉请求里自带的
                                 X-Avernet-Principal
                              4. 对解析出的身份集合签名
                                 （HS256，aud=backend，
                                  iss=gateway，TTL 60s）
                                    │
                                    ▼  X-Avernet-Principal: <jwt>
                                                   5. 验签（每请求仅一次）
                                                   6. 解析 tenant + caller
                                                   7. 准入：这个调用者
                                                      能不能访问这个操作？
                                                   8. 所有读写按
                                                      tenant + user_id 收敛
```

写客户端之前，有四条结论必须先记住：

1. **你永远不需要自己发 `X-Avernet-Principal`。** 网关会剥掉请求里自带的同名
   header，再注入自己签发的。带上它不是捷径，而是一个会被直接丢弃的伪造 header。
2. **网关只解析路由声明了的身份类型。** 如果某条路由的规则里没有 `app`，那你的
   API Key 虽然被认证了，却根本不会进入送往后端的 principal，后端也就无从据此
   收敛。这就是为什么有些操作要求你**同时**带用户凭证和应用凭证——哪怕其中任何
   一个单独也能通过认证。
3. **认证和授权是两跳。** 网关回答"你是谁"，后端回答"你能不能做这件事"。网关的
   `401` 和后端的 `401` 在你看来长得一样，含义完全不同——§9 讲怎么区分。
4. **地址决定 upstream。** `/openapi/v1` 之后的第一段是网关的 domain 选择器。
   `/openapi/v1/bots/**` 打到本后端；不属于任何已配置 domain 的路径会在边缘就被
   拒绝，根本到不了任何服务。

基础地址：`https://<你的网关域名>`。本文所有路径都相对于它。当
`module_config.web.enable_api_docs` 打开时，网关还会在 `/openapi.json` 提供聚合后的
OpenAPI 文档，在 `/docs` 提供 Swagger UI。

---

## 2. 四种身份，以及其中真正能用的两种

网关把调用者建模成一个身份**集合**，而不是单个身份。一次请求可以同时带着一个真人
和代表他调用的那个程序，后端两个都能看见。

| 身份 | 证明了什么 | 凭证 | 是否携带 tenant |
| --- | --- | --- | --- |
| `user` | 一个真人 | Google OAuth access token | **否** —— 用户凭证里没有任何东西能证明这个人在替哪个租户行事 |
| `app` | 一个已注册的第三方应用 | 应用 API Key | 是 —— 来自注册记录 |
| `bot` | 以自己身份调用的 Bot | Bot session token | 是 |
| `access_key` | 租户级机器密钥 | access-key token | 是 |

**在 `/openapi/v1/bots/**` 上只有 `user` 和 `app` 可用。** 这不是建议，而是两道
强制：

- 网关的 `route_security` 对这些路径只声明了 `user` 和 `app`，所以 `bot` 和
  `access_key` 凭证在这里根本不会被解析；
- 后端的验签器会拒绝"既没有 user 也没有 app"的身份集合，无论它还带了什么。

`bot` 凭证在别处是有意义的（协作会话文件那组路由声明了它）。`access_key` 目前在本
接口上没有任何一条路由声明，因此完全用不上。

### 租户，一段话说清

tenant 是数据隔离键：每次读取都被限制在其中，任何写入都不能指向另一个租户。它来自
请求上的**机器**身份——应用的注册记录说明它属于哪个租户。只带用户身份的请求不声明
任何租户，会落到内部默认租户（`teamclaw`），这对我们自家前端上的第一方调用者是正确
的作用域。如果一次请求带的两个身份指向**不同**租户，整个 token 会被拒绝：一次请求
不可能同时属于两个租户。

---

## 3. 凭证从哪里来

### 3.1 应用 API Key

应用向网关注册，网关**只此一次**返回明文 API Key：

```bash
curl -X POST https://<网关域名>/admin/apps \
  -H 'Content-Type: application/json' \
  -d '{
        "app_name": "acme-scheduler",
        "owners":   "acme-platform-team",
        "app_type": "SERVER",
        "tenant":   "acme",
        "creator":  "alice",
        "status":   "ACTIVE"
      }'
```

```json
{
  "id": 4711,
  "app_name": "acme-scheduler",
  "owners": "acme-platform-team",
  "app_type": "SERVER",
  "tenant": "acme",
  "status": "ACTIVE",
  "env": "",
  "api_key": "7Qk2mP9nR4vT6wY1zA3bC5dE8fG0hJ2k"
}
```

关于这个 Key，必须知道的几点：

- **32 位 base62 字符串。** 注册表只保存加盐 PBKDF2 哈希，外加 8 个字符的查找前缀，
  所以读数据库拿不到可用凭证——我们自己也拿不到。丢了就只能重新签发，没有"再看一次"。
- **`status` 必须是 `ACTIVE`。** 认证时是精确比较，注册成 `INACTIVE` 的记录会返回
  `201`，然后永远认证不过。
- **`tenant` 就是你后续读写的隔离作用域。** 一个全新租户在有自己的数据之前读到的是
  空集，这是隔离在生效，不是 bug。
- **历史的 JWT 应用 token 仍然可用**，走一条已废弃的查找路径，只在过渡窗口内有效。
  它们无法被转换（每一个都以相同的 `eyJhbGci` 开头，基于前缀的查找无法据此建键），
  所以持有者只能自行轮换成 API Key，而不是被迁移过去。
- **社区版的 `POST /admin/apps` 是不鉴权的** —— 它是 singlebox / 开发期的便利接口。
  生产部署必须在它前面加一层管理员凭证。不要把它暴露出去。

### 3.2 用户凭证

`user` 身份是一个 **Google OAuth access token**，网关在每次携带它的请求上都会调用
Google 的 userinfo 端点做校验。没有 cookie，也没有会话兜底：你的用户走完 Google 自己
的登录/授权流程，你的客户端持有拿到的 access token，每次调用时带上。

校验出来的 `sub` 就是本接口据以收敛的用户 id —— 也就是你要填进 `?user_id=` 的值。

> 企业版部署通过配置覆盖成另一条 `user` 链（公司 IdP）。下文的传输契约不变，只是
> token 的校验方式不同。

### 3.3 Access Key

通过 `POST /admin/access-keys` 签发（生产环境注意事项同上）。它标识的是租户而不是人，
而且目前 `/openapi/v1` 下没有任何路由声明 `access_key` 身份——所以它调不了这套接口。
这里写出来，是为了让你不必再去找用法。

---

## 4. 各场景该发什么

凭证都放在 header 里。每种策略都接受 `Authorization: Bearer <…>`，同时各自还有一个
专用 header —— 只要一次请求要带一个以上的凭证，你就必须用专用 header，因为
`Authorization` 只有一个。

| 身份 | 专用 header | 也可以用 |
| --- | --- | --- |
| `user` | `x-google-token: <access token>` | —— （这个只认专用 header） |
| `app` | `x-avernet-app-token: <api key>` | `Authorization: Bearer <api key>` |
| `bot` | `x-avernet-bot-token: <token>` | `Authorization: Bearer <token>`（仅限非 JWT 形态） |
| `access_key` | `x-avernet-access-key-token: <token>` | `Authorization: Bearer <token>` |

`Authorization` 这个兜底位置是共用的，所以**当你同时要带用户凭证和应用凭证时，请一律
使用专用 header**。第三方集成的常态就是这个形态。

### 场景 A —— 真人为自己调用

第一方场景：我们自己的前端、开发者本地跑的 CLI、用自己 token 的脚本。

```bash
curl 'https://<网关域名>/openapi/v1/bots?user_id=<google-sub>&page=1&page_size=20' \
  -H 'x-google-token: <google access token>'
```

作用域：内部默认租户。`user_id` 必须是调用者自己的 id —— 填别人是 `403`。

### 场景 B —— 应用带着用户一起调用

两个凭证同时在线。凡是"同意时刻"或租户级的操作都要求这样（§5、§7）。

```bash
curl -X POST 'https://<网关域名>/openapi/v1/bots/20260813_a7k2m9p1/authorized-apps?user_id=<google-sub>' \
  -H 'x-google-token: <google access token>' \
  -H 'x-avernet-app-token: <api key>'
```

作用域：**应用的**租户，因为机器身份声明了租户而用户身份没有。`user_id` 仍然必须是
这个真人自己的 id。

### 场景 C —— 应用单独调用，代表某个用户

链路上没有真人。应用只带自己的 Key，并在 `?user_id=` 里指明它代表谁。这才是集成场景，
而它只在存在**授权（grant）**（§7）、并且该操作允许机器调用者（§6）时才成立。

```bash
curl 'https://<网关域名>/openapi/v1/bots/20260813_a7k2m9p1/sessions?user_id=<授权给你的用户>' \
  -H 'x-avernet-app-token: <api key>'
```

作用域：应用的租户，并且在租户之内只限于该用户授权给这个应用的范围——不会更多，也
永远不会超过那个用户**此刻**自己能触达的范围。

---

## 5. 怎么定位一个操作

四个参数承担了全部工作。其中三个在所有方法上都是 query 参数——包括 `PUT`、`POST`、
`DELETE`。这套接口从不把身份放进 body 或路径段。

### `bot_id` —— 永远在路径里

```text
/openapi/v1/bots                        账号级集合
/openapi/v1/bots/{bot_id}               某一个 bot
/openapi/v1/bots/{bot_id}/<component>   该 bot 的某个组件
```

bot 是地址，不是参数。`sessions`、`skills`、`routines`、`resources`、`engine`、
`identity`、`approvals`、`models`、`connection`、`startup-script`、`authorized-apps`
全部挂在 `{bot_id}` 之下。

因为 `{bot_id}` 是一个通配段，所以凡是在这个位置上被真实服务的字面量，都是 bot 不能
取的名字。当前这份清单是：

```text
approvals  authorized  ceiling  check-name  connection  engine  identity
loadtest   logs        mcp      models      resources   routines
sessions   skills
```

其中九个——`approvals`、`connection`、`engine`、`identity`、`models`、`resources`、
`routines`、`sessions`、`skills`——只被**待退役的**组件优先地址占用；这些地址移除后，
清单就只剩 `authorized`、`ceiling`、`check-name`、`loadtest`、`logs`、`mcp`。另外
`messages` 是提前占位的：网关已经在 `/openapi/v1/bots/messages/ws/**` 上服务聊天
WebSocket，这个名字留给将来要放在那里的 HTTP 端点。

### `?user_id=` —— 几乎所有操作都必填

这次调用代表的终端用户。在所有操作上是同一个值，读和写含义相同。

```text
GET    /openapi/v1/bots/b-1?user_id=u-42
PUT    /openapi/v1/bots/b-1?user_id=u-42        {"bot_name": "Ada"}
DELETE /openapi/v1/bots/b-1?user_id=u-42
POST   /openapi/v1/bots/b-1/skills?user_id=u-42 <raw zip>
```

它怎么被授权，取决于谁在调用：

- 凭证里**指名了一个人**的调用者必须填自己 → 填别人一律 `403`；
- **单独调用的应用**填授权给它的那个用户 → 拿去比对 grant，没有授权的用户会被回
  `404`，和不存在的 bot 一模一样。所以猜 `user_id`毫无收益。

有四个操作不带 `user_id`，因为它们没有用户维度：`GET /bots/check-name`、
`GET /bots/mcp/servers`、`GET /bots/mcp/servers/{server_code}`、
`GET /bots/mcp/tenants`。它们仍然要求已认证的调用者。

> **一个陷阱。** `GET /openapi/v1/bots/logs/**` 也收 `user_id`，但在那里它的含义是
> *读谁的 trace*——这是租户级的可观测面，同时带 user 和 app 身份的调用者可以把它指向
> 别人。同一个拼写，相反的契约。不要在两者之间复用客户端代码。

### `?owner_id=` —— 只在 bot 不是你自己的时才需要

你所寻址的那个 bot 的 owner，默认为调用者自己。只有在访问**共享给你**的 bot 时才需要
它，而且只在提供了该参数的操作上：engine-runtime 那几组（`sessions`、`engine`、
`models`、`approvals`、`connection`）、skills 的两个集合操作，以及授权相关操作。

它之所以存在，是因为 `bot_id` 单独并不能唯一标识一个 bot —— 已废止的 `default` 约定
让很多 owner 拥有同名 bot，所以 `(bot_id, owner_id)` 这个二元组才是真正的地址。

谁可以操作一个共享 bot：它的 **owner**，或**member 级及以上的协作者**。公开可见性不
授予任何人操作权。其他任何人得到的回应与"这个 bot 不存在"逐字节相同——是被掩盖的
`404`，绝不是 `403`。

### `?stage=` —— 你指的是哪个 runtime

`draft`（默认）、`verify` 或 `online`。

- `draft` 是 bot 自己的工作区，也是个人 bot 唯一拥有的 runtime。
- `verify` 与 `online` 只在对应发布记录处于存活状态时存在。
- 请求一个没有存活 runtime 的 stage 会得到
  `409 "No live runtime at the requested stage"` —— 按设计不会静默回退到别的 stage。
- **读支持三个 stage，写只接受 `draft`。** 已发布的 runtime 是一次发布产出的结果，
  `PUT …?stage=online` 返回 `409 "The requested stage is read-only"` 且什么都不写——
  既不写已发布的 runtime，也不会拿 draft 顶替。

取用它的是 engine-runtime 那组，外加 `…/engine/config` 与
`…/identity[/{file_type}]`。启动脚本、MCP、resources、skills、routines 目前都只有
draft。

### 分页

所有列表端点都收 `page`（从 1 开始，默认 1）和 `page_size`（默认 20，最大 100），
返回 `Envelope[Page[T]]`，其中 `total` 是全部匹配数。

---

## 6. 应用能触达什么（准入表）

本接口上的每个操作都声明了它如何对待"链路上没有真人"的调用者。做集成方案时需要的
就是这张表，因为它决定了场景 C 到底能发哪些调用。

| 模式 | 含义 | 举例 |
| --- | --- | --- |
| **grant-checked，自有 bot** | 仅当存在覆盖 `(app, bot, 授权用户)` 的存活 grant 时准入；bot 恒为授权用户自己的。 | `GET/PUT/DELETE /bots/{bot_id}`、`…/restart`、`…/status`、`…/passport`、`…/engine/config`、`…/startup-script`、`…/identity/**`、`…/resources/**`、`…/routines/**`、四个 `…/skills/{skill_id}` 操作 |
| **grant-checked，寻址 bot** | 同样的检查，但针对 `(bot_id, owner_id)` 指定的 bot —— 因此**共享** bot 也能触达。 | `…/sessions/**`、`…/engine/{status,available,capabilities}`、`…/approvals/**`、`…/models/**`、`…/connection`、`GET/POST …/skills` |
| **grant-filtered** | 无条件准入；被收窄的是**结果**，只返回已授权的那些 bot。 | `GET /bots`、`GET /bots/authorized` |
| **user-gated** | 没有 bot 维度。仅当该应用持有来自这个用户的至少一条存活 grant 时准入。 | `GET /bots/ceiling` |
| **open** | 对租户内每个已认证调用者答案完全一致。 | `GET /bots/check-name`、`GET /bots/mcp/servers`、`…/mcp/servers/{server_code}`、`…/mcp/tenants` |
| **refused** | `401`。表中**没有列出**的操作也一律走这一档。 | `POST /bots`、三个 `…/authorized-apps` 操作、`…/bots/logs/**`、`…/mcp/servers/{server_code}/config`、`…/mcp/servers/{server_code}/permissions`、`…/loadtest/**` |

这些拒绝背后的理由，直接决定了你的集成必须在哪些环节把人拉进来：

- **创建 bot** —— 此时还没有 bot 可供 grant 覆盖，而且创建会消耗用户的配额。自动给
  新 bot 补一条授权，等于凭空发明了没人给过的同意。
- **授予、查看、撤销授权** —— 委托是人的行为。应用不得自行扩大自己的权限、撤销竞争
  对手的权限，或者窥探还有谁能触达这个 bot。
- **Bot 日志** —— 租户级可观测面，那里的 `user_id` 意思是"读谁的 trace"而不是"这是
  谁的调用"。grant 覆盖的是一个 bot，翻译不成那个含义。
- **MCP 配置** —— 账号级状态，没有 bot 维度。grant 是"允许触达某个 bot"的同意，不是
  "允许重配这个账号"。（MCP **目录**读取是另一回事，属于 open。）

**贯穿全部规则的不变式：**

> 应用能触达的范围，恰好等于授权用户能触达的范围，绝不更多。

而且不是同意那一刻拍下来的快照，是活的。每一次应用单独发起的请求都会重新过一遍那个
真人自己要面对的协作者判定，所以把授权者从某个 bot 上移除，应用的访问权在下一次请求
就结束了——不需要撤销，也没有残留要清理。

---

## 7. 应用如何拿到用户 Bot 的授权

这就是把场景 B 变成场景 C 的那条流程。

### 同意调用

```bash
curl -X POST \
 'https://<网关域名>/openapi/v1/bots/20260813_a7k2m9p1/authorized-apps?user_id=<用户>&owner_id=<bot的owner>' \
  -H 'x-google-token: <该用户的 google access token>' \
  -H 'x-avernet-app-token: <你的 api key>'
```

```json
{
  "code": 201000,
  "message": "Created",
  "data": {
    "app_id": 4711,
    "app_name": "acme-scheduler",
    "user_id": "u-42",
    "bot_id": "20260813_a7k2m9p1",
    "owner_id": "u-owner",
    "granted_at": "2026-08-19T09:14:02Z"
  },
  "request_id": "b0a6d2f4e8c94b1a9f3d5e7c60218a4d"
}
```

这次调用有五个性质，每一条最终都会有人踩到：

1. **两个身份都必须在线。** 用户的，因为他在表示同意；应用的，因为记录要写下它是谁。
   应用**永远不是参数**——它是从验签后的 principal 上读出来的——所以一次请求不可能把
   授权指向调用者以外的任何应用。"你没法把权授给别人的应用"是构造上成立的，不是靠
   某处的一次检查。
2. **授权者必须能操作这个 bot** —— 是它的 owner，或 member 级及以上的协作者。其他人
   得到被掩盖的 `404`。规则是*你只能委托你自己拥有的那份权限*。
3. **`owner_id` 默认为调用者自己。** 自己的 bot 就省略它；要在共享给你的 bot 上做
   委托，就填上它。
4. **幂等。** 重复授予一条已存活的授权会原样返回，所以合作方重试一个超时请求不会因为
   上一次其实成功了而受罚。但**两个不同用户**在同一个 bot 上授权同一个应用，是两条
   独立的 grant，不是重复——他们出借的是两份不同的权限。
5. **这条记录里有两个人。** `user_id` 是出借访问权的授权用户；`owner_id` 是 bot 的
   owner，他可能压根没听说过你的应用。应用之后能做的一切都按前者收敛；后者的存在是
   为了让 owner 始终看得见。

### 应用视角：我能触达哪些 bot

```bash
curl 'https://<网关域名>/openapi/v1/bots/authorized?user_id=<用户>' \
  -H 'x-avernet-app-token: <你的 api key>'
```

这里 user 在边缘是 optional 的——这是集成方用来发现自身权限范围的唯一一个操作，如果
还要求链路上有真人，那就只有被代理的那个人自己才能查到，失去意义。

它也是**唯一完整**的视图：一个被委托的、但该用户并不拥有的 bot，不会出现在该用户任何
bot 列表里，没有这个接口就无从发现。

### Owner 视角，以及撤销

```bash
# 哪些应用能触达我的 bot，分别是谁放进来的
curl 'https://<网关域名>/openapi/v1/bots/{bot_id}/authorized-apps?user_id=<用户>' \
  -H 'x-google-token: <token>'

# 撤销其中一个
curl -X DELETE 'https://<网关域名>/openapi/v1/bots/{bot_id}/authorized-apps/4711?user_id=<用户>' \
  -H 'x-google-token: <token>'
```

这两个只需要用户凭证，是刻意如此：一个需要应用配合才能完成的撤销根本不算撤销——而
恰恰是"凭证丢了、被轮换了、合作结束了"这些情形才需要撤销。

谁看到什么、能撤销什么：

- **owner** 能看到自己 bot 上的每一条授权，无论是谁委托的，并且撤销时会撤掉某个应用
  在这个 bot 上的**全部**授权。对 bot 的机器访问，永远不会对它的 owner 不可见。
- **协作者**只看得到、也只能撤销自己那条。他出借的是自己的那份权限；同事那份不归他
  收回。

### 访问权终止的三种方式

1. **显式撤销**，如上。
2. **授权者自己失去了对该 bot 的访问权** —— 被移出协作者，或降到 member 以下。grant
   行还在，但已经失效：每次请求都会重新问一遍活的问题，所以下一次调用就断了，不需要
   任何清理。
3. **bot 被删除。** 删除会先撤掉它上面的全部授权，再做任何破坏性动作，所以一旦失败，
   留下的是一个完好、可重试的 bot，而不是一个已经不可用却仍挂着存活授权的 bot。

### 这不是什么

这里没有 OAuth 授权码流程，没有 `/authorize`、没有 `/token`、没有 scope，也没有我们
托管的同意页。相关设计确实存在
（`src/gateway/docs/2026-07-25-third-party-delegated-oauth/`），但它被明确保留为
**参考笔记，不是规格**——真正上线的机制就是上面这套 grant 记录。不要照那份文档写
客户端。

---

## 8. 响应约定

无论成功还是已定义的失败，所有端点都返回同一个信封：

```json
{
  "code": 200000,
  "message": "OK",
  "data": { },
  "request_id": "b0a6d2f4e8c94b1a9f3d5e7c60218a4d"
}
```

- **`code`** 是六位：HTTP 状态码（3 位）加业务子码（3 位）。`200000` OK、`201000`
  Created、`202000` Accepted、`404000` not found。
- **`message`** 始终是英文；在与认证相关的状态上它刻意含糊——所有 `401` 都只说
  `"Unauthorized"`，所有 `403` 都只说 `"Forbidden"`。告诉调用者他被拒的凭证哪里不对，
  等于告诉他下一个该怎么伪造。
- **`data`** 在错误和空结果时存在但为 `null`。
- **`request_id`** 与响应头 `X-Trace-ID` 一致。**任何工单请附上它**——服务端会把拒绝
  的具体原因记在这个 id 上，而一个字都不会返回给你。
- **列表结果**是 `Envelope[Page[T]]`：`{"total": n, "items": [...]}`。
- **删除**返回 `{"deleted": true}`。删除失败返回错误信封，绝不会是 `deleted: false`。

### 状态码

| 状态码 | 什么时候 |
| --- | --- |
| `400` | schema 拦不住的非法输入——日志查询非法、资源路径非法、bot 名非法、引擎不支持 |
| `401` | 没有凭证、凭证无法验证，或这个操作不接纳这种调用者形态 |
| `403` | `user_id` 指向了一个已认证调用者无权代表的用户 |
| `404` | 不存在——**或者**存在但不属于你，**或者**你是应用且没有对应 grant。三者按设计逐字节相同 |
| `409` | 与当前状态冲突——重名、配额已满、该 stage 没有存活 runtime、stage 只读、设备未就绪 |
| `413` | 请求体超过已公布的上限（启动脚本 24 KiB、skill 包、文件预览） |
| `422` | 校验失败——缺失或格式错误的 query 参数，最常见的是 `user_id` |
| `500` | 内部错误 |
| `501` | 仅 engine-runtime：这个 bot 的引擎没有声明该能力 |
| `502` | 上游服务失败（engine、device、passport、MCP、skill 存储） |
| `504` | 引擎请求超时 |

**`404` 值得单独说一句。** 被掩盖的 `404` 是本接口对一切"你不该知道它是否存在"情形的
统一回答，所以在一个跑不通的集成里，它并不等于"id 写错了"。请按 §9 逐条排查。

---

## 9. 调用被拒绝时

响应体是刻意不提供信息的。请从外向内诊断。

### `401`

**先分清是网关的还是后端的。** 两者含义不同。

两者用的是同一个信封，靠 `code` 区分：

- 网关的 `code` 是 `401001`，`message` 会点名哪个身份没解析出来，例如
  `unauthenticated: no credential for user`；
- 后端的 `code` 是 `401000`，`message` 固定为 `"Unauthorized"`。

**网关的** 401 表示你的凭证没有通过认证：

1. 某个 required 身份缺凭证 —— 对照 §4 看这个操作需要哪些 header；最常见的失误是对
   一个规则里同时要求 user 的操作只发了 API Key；
2. 凭证在但无效 —— Google token 过期、Key 不是 `ACTIVE`、用了别的环境的 Key；
3. `Authorization: Bearer` 冲突 —— 两个凭证抢一个 header。请改用专用 header。

**后端的** 401 表示你通过了认证，但这种调用者形态不被接纳：

1. **应用单独调用了一个拒绝机器调用者的操作** —— 对照 §6 的表。这是集成场景下最常见
   的单一原因；
2. 签名的 principal 没验过。这是部署问题，不是客户端问题 —— 见 §10；
3. 请求压根不是经由网关到达的（后端会打印
   `no X-Avernet-Principal header on …`）。

### `403`

只有一种原因：`?user_id=` 不是凭证所指名的那个调用者的 id。对 Google 认证的调用者来
说，`user_id` 必须等于 token 的 `sub`。

注意单独调用的应用**不会**因此拿到 403 —— 它的 `user_id` 是拿去比对 grant 的，填错了
是 `404`。

### 明明存在却拿到 `404`

按可能性从高到低：

1. **没有 grant。** 单独调用的应用如果没有覆盖 `(app, bot, owner, user)` 的存活
   grant，得到的回应与 bot 不存在完全一致。用
   `GET /openapi/v1/bots/authorized` 确认。
2. **授权者失去了访问权。** grant 还在，但那个用户已经不是 owner、也不再是 member 级
   协作者。这是每次请求活判的，所以哪里都不会有撤销事件。
3. **共享 bot 上漏了 `owner_id`。** 它默认为调用者自己，所以在别人的 bot 上省略它，
   寻址到的是*你自己*同名的那个 bot —— 而那个多半不存在。
4. **租户不对。** 应用的 tenant 就是隔离作用域；别的租户里创建的 bot 你根本看不见。
5. **`user_id` 填错了人** —— 对应用调用者来说，应该填授权给你的那个用户，而不是 bot
   的 owner。bot 一旦是共享的，这两个人就不是同一个。

### `422`

几乎总是 `user_id` 缺失或为空。除了 §5 列出的四个目录类读取，所有操作都要求它，而且
永远放在 **query string** 里——无论什么方法、body 是什么。

### engine-runtime 组上的 `409`

- `"No live runtime at the requested stage"` —— 你对一个在该 stage 没有存活发布的 bot
  请求了 `verify` 或 `online`（个人 bot 永远没有）。不要改成别的 stage 重试，按设计
  没有回退。
- `"The requested stage is read-only"` —— 对已发布 stage 发起了写。什么都没写。再发布
  一次也不会让它落地；请写 `draft` 然后发布。
- `"Bot device is not ready"` / `"Bot has no active device"` —— bot 存在但容器没起来。
  轮询 `GET …/{bot_id}/status`。

---

## 10. 部署侧必须成立的前提

客户端能查的到此为止；下面是运维要确认的。

**一把共享 HMAC 密钥。** 网关用它签 principal token，后端用同一把密钥验签。

| 侧 | 社区版从哪里取 |
| --- | --- |
| 网关 | `AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE`，经由 `user_config.principal_signer.secret_name` |
| 后端 | `AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE`，经由 `secret_names.gateway_principal_signing_key` |

**两侧都没有任何兜底密钥**，这是刻意的：提交进仓库的共享密钥就是提交进仓库的凭证。
密钥在启动时解析一次，所以轮换需要两侧都重启。

**两侧都会在启动时打印指纹** —— 截断的 SHA-256，写进日志是安全的，对读日志的人没有
用处。诊断密钥不一致靠比对两行日志，而不是靠打印凭证：

```text
backend:  gateway principal verification is configured (secret='...', key fp=eb128a7a, key len=38, aud='backend', iss='gateway')
gateway:  principal signer configured (secret='principal_signing_key', key fp=eb128a7a, key len=38, kid='bare', iss='gateway', ttl=60s)
```

| 现象 | 含义 |
| --- | --- |
| 指纹不同 | 两端持有不同密钥 —— 最常见的原因 |
| 网关 `key fp=unset` | 网关没解析到密钥，根本签不了 |
| 指纹相同但 `key len` 不同 | 有一侧的值带了空白字符（只会出现在混版本灰度期间） |
| 指纹相同 | 不是密钥问题 —— 查 `iss`、时钟偏移，以及是否有进程早于上次轮换启动 |

**密钥缺失时的行为按环境不同，而这正是重点：**

| 环境 | 没有可用密钥时 |
| --- | --- |
| `pre` / `prod` | 进程**拒绝启动**，让发布明确失败，而不是起一个看着健康、却每个请求都 401 的服务 |
| local / dev | 正常启动，但**每个 `/openapi/v1` 请求都答 401** —— 这些环境本来就合理地没有密钥 |
| singlebox | 什么都解析不到；`/openapi/v1` 在那里一律拒绝，没有任何配置开关能改变这一点 |

**两处没有强制校验的耦合**，任何一处只在一侧改动都会让所有请求 401：

- `aud` 在后端代码里固定为 `backend`，网关那边是用 `upstreams.servers:` 里该 upstream
  的名字签出来的。改那个 server 名就会断。
- `iss` 在后端代码里固定为 `gateway`，网关那边可通过
  `user_config.principal_signer.issuer` 配置。改它必须在同一个版本里改后端常量。

**Token TTL 是 60 秒**，允许 5 秒时钟偏移。两个容器时间差超过几秒就会拒掉有效 token。

**准入表在边缘有一份对应物。** 网关的 `route_security` 决定某条路径上哪些身份是*可解析
的*；后端的 `admission.py` 决定请求到达后哪些操作接纳机器调用者。两者必须对"某个被拒绝
的操作仍然需要真人"这件事保持一致——只改了一侧而在两跳都放开的操作，正是这对机制存在
的意义所在。

**一条 L7 注意事项。** 网关前面的任何一跳都必须放行 WebSocket `Upgrade`，并且对
`/openapi/v1/bots/messages/ws/**` **不设**读超时。凭证只在握手时校验一次，而这条连接
本就被设计成可以活得比凭证有效期更久；设了空闲超时就会把健康的 socket 拆掉。

---

## 11. 接口全貌

| 分组 | 地址 | 做什么 |
| --- | --- | --- |
| bots | `/openapi/v1/bots`、`/openapi/v1/bots/{bot_id}` | 创建、列表、详情、更新、删除、重启、重名检查、配额上限、授权状态轮询、运行状态、passport、引擎配置、启动脚本 |
| sessions | `…/{bot_id}/sessions` | 会话及其消息 |
| engine | `…/{bot_id}/engine` | runtime 状态、能力、可用性 |
| approvals | `…/{bot_id}/approvals` | bot 的审批模式 |
| models | `…/{bot_id}/models` | 该 bot 引擎提供的模型 |
| connection | `…/{bot_id}/connection` | **可直接使用的聊天 socket URL** |
| skills | `…/{bot_id}/skills` | 本地 skill 的安装、列表、启用、停用、删除 |
| routines | `…/{bot_id}/routines` | 定时任务、执行与历史 |
| resources | `…/{bot_id}/resources` | bot 工作区文件——列表、stat、上传、下载、预览、mkdir、删除 |
| identity | `…/{bot_id}/identity` | bot 的身份文件 |
| authorized-apps | `…/{bot_id}/authorized-apps`、`…/bots/authorized` | 授权记录（§7） |
| mcp | `…/bots/mcp` | MCP 市场目录与账号级 server 配置 |
| logs | `…/bots/logs` | 跨 bot 的 trace 级可观测面（user **与** app 都必需） |
| loadtest | `…/bots/loadtest` | 一个 echo 端点和一个 socket，用于压测这条中继链路 |

**聊天不在这套 API 里。** `GET …/{bot_id}/connection` 返回可直接使用的 socket URL，
由你的客户端自己去连——这样引擎的帧格式就不会变成对外契约。该 socket 由网关在
`/openapi/v1/bots/messages/ws/**` 上服务并中继到 engine proxy；它的凭证走握手的 query
string，因为浏览器的 WebSocket API 只接受一个 URL 和一个 subprotocol，附不了别的东西。

### 创建 bot 有时是两步

`POST /openapi/v1/bots` 要么返回 `201 Envelope[Bot]`（已完成），要么返回
`202 Envelope[BotAuthPending]`（授权正在签发）。收到 `202` 时轮询
`POST …/{bot_id}/auth-status` —— 之所以是 POST，是因为在授权签发完成时它会**完成
创建**，并不是一次读。`GET` 那个拼写是待退役的。

### 待退役地址

有 42 个操作同时在新的 bot 优先地址和旧的组件优先地址（`…/bots/sessions/{bot_id}`
之类）上应答。**什么都没被删除**，而且旧地址不是别名：它们在**原来的位置**公布**原来
的**参数名并做转换，所以未迁移的客户端逐字节保持可用。

不过仍有两个迁移的理由：

- 一部分待退役地址不接受 `stage`，无条件按 `draft` 应答——其中包括两个写操作，因此
  它们返回 `200` 并写入 draft，而它们的替代地址返回 `409` 且什么都不写；
- 它们退役后保留名清单会缩短，释放出九个目前 bot 不能使用的名字。

---

## 12. 新集成检查清单

1. 注册应用，保存好 API Key（§3.1）。确认 `status` 是 `ACTIVE`，并记下你的租户。
2. 逐个调用确认自己处在哪个场景（§4）。一旦要发两个凭证，立刻改用专用 header，而不是
   `Authorization`。
3. 让用户把 bot 授权给你（§7），两个凭证同时在线。
4. 用 `GET /openapi/v1/bots/authorized` 确认自己的权限范围。
5. 对每一个你打算单独调用的操作先查 §6。凡是 **refused** 的，都需要链路上有真人——把
   这件事设计进你的产品流程，而不是设计进重试逻辑。
6. 除了四个目录类读取，每次调用都带 `user_id`；bot 不属于授权用户自己时带 `owner_id`；
   只有在确实要访问已发布 runtime 时才带 `stage`。
7. 记录每个响应里的 `request_id`。对于接口不会告诉你原因的拒绝，它是唯一的抓手。
