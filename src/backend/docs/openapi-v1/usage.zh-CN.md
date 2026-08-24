# `/openapi/v1` 使用文档

[English](usage.md) | **简体中文**

_这是一份使用文档，不是 API 参考手册。_ 参考手册是 `/openapi.json` 上的 OpenAPI
文档，它描述的是数据结构。这份文件描述怎么**调用**：什么场景带什么凭证、你的应用
如何取得代表用户操作其 Bot 的权限、每个操作在请求上要写什么，以及被拒绝时该怎么办。

> 你是在开发这套接口而不是调用它？[`README.md`](README.md) 是工程交接文档，
> [`engine-surface.md`](engine-surface.md) 是运行时那一组的逐端点清单。

---

## 1. 开始之前

只有**一个**地址。本文所有路径都在它之下：

```text
https://<你的-avernet-域名>/openapi/v1/...
```

域名向平台运维索取。同一个域名上，`/openapi.json` 提供机器可读的接口描述，
`/docs` 提供可浏览的版本。

有三条性质请先记住，它们决定了每一次调用长什么样：

- **Bot 是主语。** 几乎每个操作都作用在一个 bot 上，而 bot 是路径的一段：
  `/openapi/v1/bots/{bot_id}/<组件>`。
- **每次调用都要说明"这是替谁调用的"。** 用 query 参数 `?user_id=` 指明本次调用
  代表的终端用户。它几乎在所有操作上必填，读和写一样。
- **只有一种响应结构。** 成功和失败用同一个信封，客户端只需解析一套结构（§7）。

---

## 2. 谁在调用：两种调用者

这套接口认两种调用者，一次请求可以同时带上两个。

| 调用者 | 凭证 | 怎么传 |
| --- | --- | --- |
| **一个真人** | 他的 SSO 会话，由 BUService 解析 | `x-one-id: <token>` header，**或** `IAM_TOKEN` cookie |
| **一个应用** | 签发给你的应用 API Key | `Authorization: Bearer <api key>` |

**API Key 只放在 `Authorization` 里，别无他处。** 它没有备用 header；放在别的地方等于
没有携带任何应用身份。

两个凭证不会抢同一个位置——真人的身份走它自己的 header 或 cookie——所以需要同时带两个
的请求直接都带上即可。第三方集成的常态正是这种形态。

> 平台其他地方还存在别的凭证类型（Bot session token、租户 access key）。这套接口
> **不接受**它们，带了等于没带。

由此得到三种调用形态。本文后面所有操作都用这三种形态来描述。

### 形态 A —— 真人替自己调用

第一方客户端：我们自己的工作台、开发者本地跑的 CLI、用本人 token 的脚本。

```bash
curl 'https://<域名>/openapi/v1/bots?user_id=<用户id>&page=1&page_size=20' \
  -H 'x-one-id: <sso token>'
```

`user_id` 必须是调用者自己的 id。填别人是 `403`。

### 形态 B —— 应用带着用户一起调用

两个凭证同时在线。凡是要记录一次"同意"、或跨组织读取的操作都要求这样（§5、§6）。

```bash
curl -X POST 'https://<域名>/openapi/v1/bots/20260813_a7k2m9p1/authorized-apps?user_id=<用户id>' \
  -H 'x-one-id: <sso token>' \
  -H 'Authorization: Bearer <api key>'
```

`user_id` 仍然必须是你所带 token 那个人的 id。

### 形态 C —— 应用单独调用，代表某个用户

没有真人参与。你的应用只带自己的 Key，并在 `?user_id=` 里指明它代表谁。**这才是
集成场景**，它成立的前提是：那个用户已经就这个 bot 授权了你的应用（§5），并且该操作
接受没有真人的调用者（§6）。

```bash
curl 'https://<域名>/openapi/v1/bots/20260813_a7k2m9p1/sessions?user_id=<授权给你的用户>' \
  -H 'Authorization: Bearer <api key>'
```

这种形态下你能触达的，恰好等于那个用户**此刻**能触达的范围——不会更多，并且他自己的
权限一旦收缩，你的也立刻跟着收缩。

---

## 3. 凭证从哪里来

### 3.1 你的应用 API Key

应用在平台上注册，**只此一次**拿到明文 API Key。自建或 singlebox 安装下，注册就是：

```bash
curl -X POST https://<域名>/admin/apps \
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

托管部署下由平台运维替你完成注册并把 Key 交给你。无论哪种方式：

- **签发那一刻就存好。** 系统只保留单向哈希，Key 无法再次展示——丢了只能重新签发，
  找不回来。
- **`status` 必须是 `ACTIVE`。** 用别的状态注册会创建成功，然后永远认证不过。
- **`tenant` 是你的数据边界。** 你读写的一切都在它之内；新租户在有自己的数据之前读到
  的是空集。
- **`app_id`**（上面的 `id`）是你的应用在授权记录里、以及
  `DELETE …/authorized-apps/{app_id}` 里用到的编号。
- 注册接口是开发期便利接口，**社区版不鉴权**。生产安装必须在它前面加一层管理员凭证。

如果你手上是更早的 JWT 形态应用 token，它在过渡窗口内仍可认证，但无法被转换——请自行
计划轮换成 API Key。

### 3.2 用户凭证

真人由平台 **SSO** 认证，经 BUService 解析。你的客户端自己不做任何校验——它只是把 SSO
会话已经给它的东西带上，有两种被接受的形式：

- **`x-one-id: <token>`** —— 显式转发的 subject token。这是服务端到服务端的形式，也是
  第三方集成用的那种。
- **`IAM_TOKEN` cookie** —— 已经有会话的浏览器会自动带上。适合第一方或浏览器端客户端；
  服务端集成不应该持有它。

两种形式解析出的是同一个人，而这个人的 id 就是你要填进 `?user_id=` 的值。

> 自建部署可能接的是另一个身份提供方，那时用户 token 由该提供方签发。下文其余内容不变，
> API Key 的携带方式也完全一样。

---

## 4. 一个请求怎么写

四个参数承担全部定位工作。其中三个在**所有方法上都是 query 参数**——包括 `PUT`、
`POST`、`DELETE`。这套接口从不把身份放进 body 或路径段。

### `bot_id` —— 在路径里

```text
/openapi/v1/bots                        账号级集合
/openapi/v1/bots/{bot_id}               某一个 bot
/openapi/v1/bots/{bot_id}/<组件>        该 bot 的某个组件
```

`sessions`、`skills`、`routines`、`resources`、`engine`、`identity`、`approvals`、
`models`、`connection`、`startup-script`、`harness`、`authorized-apps` 全部挂在
`{bot_id}` 之下。

有少量字面量本身就占用了 `{bot_id}` 这个位置，因此 bot 不能取这些名字：

```text
approvals  authorized  ceiling  check-name  connection  engine  identity
loadtest   logs        mcp      messages    models      resources
routines   sessions    skills
```

### `?user_id=` —— 几乎所有操作都要带

本次调用代表的终端用户。在所有操作上是同一个值，读和写含义相同。

```text
GET    /openapi/v1/bots/b-1?user_id=u-42
PUT    /openapi/v1/bots/b-1?user_id=u-42        {"bot_name": "Ada"}
DELETE /openapi/v1/bots/b-1?user_id=u-42
POST   /openapi/v1/bots/b-1/skills?user_id=u-42 <raw zip>
```

- **以真人身份调用**（形态 A、B）：必须填你自己的 id，填别人是 `403`。
- **以应用身份单独调用**（形态 C）：填授权给你的那个用户。一个没有授权过你的用户会
  被回 `404` —— 和 bot 不存在是同一个回答，所以猜 `user_id` 毫无收益。

有四个操作不带 `user_id`，因为它们没有用户维度：`GET /bots/check-name`、
`GET /bots/mcp/servers`、`GET /bots/mcp/servers/{server_code}`、
`GET /bots/mcp/tenants`。它们仍然需要凭证。

> **一个陷阱。** `GET /openapi/v1/bots/logs/**` 也收 `user_id`，但在那里它的含义是
> *读谁的 trace*，而不是*这是谁的调用*，同时带两个凭证的调用者可以把它指向别人。
> 同一个拼写，相反的含义——不要在两者之间复用客户端代码。

### `?owner_id=` —— 只在 bot 不属于你时才需要

你所寻址的那个 bot 的 owner。默认为调用者自己，所以只有访问**共享给你**的 bot 时才需要
它，而且只在提供了该参数的操作上：运行时那一组（`sessions`、`engine`、`models`、
`approvals`、`connection`）、skills 的两个集合操作，以及授权相关操作。

它之所以存在，是因为 `bot_id` 单独并不能唯一标识一个 bot —— 同一个 id 可以存在于多个
owner 名下，所以 `(bot_id, owner_id)` 才是真正的地址。

谁可以操作共享 bot：它的 **owner**，或 **member 级及以上的协作者**。bot 设为公开可见
并不授予任何人操作权。其他任何人得到的回答与"这个 bot 不存在"完全一致——是 `404`，
不是 `403`。`harness` 那一组是唯一的例外，门槛更高：owner，或 **admin 级**协作者。

### `?stage=` —— 你指的是哪个运行时

`draft`（默认）、`verify` 或 `online`。

- `draft` 是 bot 自己的工作区，也是个人 bot 唯一拥有的运行时。
- `verify` 与 `online` 只在对应发布存活时存在。
- 请求一个没有存活运行时的 stage，得到
  `409 "No live runtime at the requested stage"` —— 不会回退到别的 stage。
- **读支持三个 stage，写只接受 `draft`。** 已发布的运行时只能靠再次发布来替换，不能
  编辑，所以 `PUT …?stage=online` 返回
  `409 "The requested stage is read-only"` 且什么都不写——既不写发布产物，也不会拿
  draft 顶替。

取用它的是运行时那一组，外加 `…/engine/config` 与 `…/identity[/{file_type}]`。
启动脚本、MCP、resources、skills、routines 目前只有 draft。

### 分页

所有列表操作都收 `page`（从 1 开始，默认 1）和 `page_size`（默认 20，最大 100），
返回的分页里 `total` 是全部匹配数，不是本页条数。

---

## 5. 取得用户 Bot 的授权

这就是把形态 B 变成形态 C 的那一步：用户把自己对某个 bot 的访问权借给你的应用。

### 授权调用

```bash
curl -X POST \
 'https://<域名>/openapi/v1/bots/20260813_a7k2m9p1/authorized-apps?user_id=<用户>&owner_id=<bot的owner>' \
  -H 'x-one-id: <该用户的 sso token>' \
  -H 'Authorization: Bearer <你的 api key>'
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

1. **两个凭证都必须带。** 用户的，因为他在表示同意；你的，因为记录要写下是哪个应用。
   你的应用**永远不是参数**——它是从你的 Key 上读出来的——所以一次请求不可能给调用者
   以外的任何应用授权。你没法把权授给别人的应用，别人也没法把权授给你的。
2. **授权的人必须能操作这个 bot** —— 是它的 owner，或 member 级及以上的协作者。其他
   人得到 `404`。规则是*你只能出借你自己拥有的那份权限*。
3. **`owner_id` 默认为调用者自己。** 用户授权自己的 bot 时省略它；在共享给他的 bot 上
   授权时才填。
4. **幂等。** 重复授权会原样返回已有记录，所以重试超时请求是安全的。但**两个不同用户**
   在同一个 bot 上授权你，是两条独立记录而不是重复——他们出借的是两份不同的权限。
5. **记录里有两个人。** `user_id` 是授权用户，你借的是他的访问权；`owner_id` 是 bot 的
   owner，他可能压根没听说过你的应用。你之后能做的一切都按前者收敛。

### 查询自己的权限范围

```bash
curl 'https://<域名>/openapi/v1/bots/authorized?user_id=<用户>' \
  -H 'Authorization: Bearer <你的 api key>'
```

不需要真人在线——这是集成方用来查清自己能触达什么的唯一一个调用。它也是**唯一完整**的
视图：一个该用户并不拥有、但就它授权了你的 bot，不会出现在该用户任何 bot 列表里，没有
这个接口你就发现不了它。

### 用户视角，以及撤销

```bash
# 哪些应用能触达这个 bot，分别是谁放进来的
curl 'https://<域名>/openapi/v1/bots/{bot_id}/authorized-apps?user_id=<用户>' \
  -H 'x-one-id: <sso token>'

# 撤销其中一个
curl -X DELETE 'https://<域名>/openapi/v1/bots/{bot_id}/authorized-apps/4711?user_id=<用户>' \
  -H 'x-one-id: <sso token>'
```

这两个只需要用户凭证，是刻意如此：一个需要你配合才能完成的撤销根本不算撤销——而恰恰
是"Key 丢了、被轮换了、合作结束了"这些情形才需要撤销。

- bot 的 **owner** 能看到自己 bot 上的每一条授权，无论是谁授的；撤销会移除某个应用对
  这个 bot 的全部访问权。
- **协作者**只看得到、也只能撤销自己授出去的那条。

### 你的访问权终止的三种方式

1. **被撤销**，如上。
2. **授权用户自己失去了对该 bot 的访问权** —— 被移出协作者，或被降级。你的访问权在下
   一次调用时终止。没有任何撤销动作、也没有事件可观察，因为这个问题每次都是现问的。
3. **bot 被删除**，删除会撤掉它上面的全部授权。

### 这不是什么

这里没有 OAuth 授权码流程：没有 `/authorize`、没有 `/token`、没有 scope，也没有我们
托管的同意页。授权就是上面那一个调用，没有别的。（仓库里确实有一份 OAuth 流程的设计
笔记，但它没有实现，不要照它写客户端。）

---

## 6. 你的应用单独能做什么

每个操作都声明了它如何对待**没有真人**的调用者。做集成方案时要对着这张表规划，因为它
决定了形态 C 到底能发哪些调用。

| 行为 | 哪些操作 | 含义 |
| --- | --- | --- |
| **仅限已授权的 bot** | `GET/PUT/DELETE /bots/{bot_id}`、`…/restart`、`…/status`、`…/passport`、`…/engine/config`、`…/startup-script`、`…/identity/**`、`…/resources/**`、`…/routines/**`、`…/skills/{skill_id}/**` | 只能作用在该用户就此授权过你的 bot 上，其他一律 `404`。 |
| **已授权的 bot，含共享的** | `…/sessions/**`、`…/engine/{status,available,capabilities}`、`…/approvals/**`、`…/models/**`、`…/connection`、`GET/POST …/skills` | 同上，但这些接受 `owner_id`，所以只是共享给授权用户的 bot 也能触达。 |
| **结果被收窄** | `GET /bots`、`GET /bots/authorized` | 一定允许；返回的列表被收窄为你已获授权的那些 bot。 |
| **需要该用户的任意一条授权** | `GET /bots/ceiling` | 没有 bot 维度，所以门槛是关系本身：只要你持有该用户的至少一条授权就允许。 |
| **总是允许** | `GET /bots/check-name`、`GET /bots/mcp/servers`、`…/mcp/servers/{server_code}`、`…/mcp/tenants` | 目录与可用性查询，对所有人答案相同。 |
| **必须有真人** —— `401` | `POST /bots`、三个 `…/authorized-apps` 操作、`…/{bot_id}/harness/**`、`…/bots/logs/**`、`…/mcp/servers/{server_code}/config`、`…/mcp/servers/{server_code}/permissions`、`…/loadtest/**` | 请一并带上用户凭证（形态 B）。 |

最后一档为什么必须有人——这正是集成必须把真人设计进产品流程的地方：

- **创建 bot** 会消耗用户的配额，而且还不存在的 bot 上不可能有任何授权。
- **授予、查看、撤销授权** —— 授权是人的行为。应用不得自行扩大自己的权限、撤销竞争
  对手的权限，或者窥探还有谁能触达这个 bot。
- **Bot 日志**是组织级的可观测面，那里的 `user_id` 意思是"读谁的 trace"。授权覆盖的是
  一个 bot，翻译不成那个含义。
- **MCP 配置**是账号级状态，没有 bot 维度。在某个 bot 上被授权，不等于被允许重配一个
  账号。（MCP **目录**查询是另一回事，总是允许。）
- **Harness** 会诊断并改写 bot 的线上配置文件。它是维护面而不是可委托面，而且要求的是
  admin 级访问，比这套接口其余部分的 member 门槛更高。

**贯穿全部规则的一句话：** 你的触达范围恰好等于授权用户的触达范围，且每次请求都重新
核对。不是他授权那一刻的快照，是活的。

---

## 7. 响应

无论成功还是失败，所有操作都返回同一个信封：

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
- **`message`** 始终是英文。在认证与权限类失败上它刻意含糊——所有 `401` 都只说
  `"Unauthorized"`，所有 `403` 都只说 `"Forbidden"`——不会告诉你被拒的凭证哪里不对。
- **`data`** 在错误和空结果时存在但为 `null`。
- **`request_id`** 与响应头 `X-Trace-ID` 一致。**任何工单请附上它：** 拒绝的具体原因
  记在这个 id 上，而不会返回给你。
- **列表**在 `data` 里是 `{"total": n, "items": [...]}`。
- **删除**返回 `{"deleted": true}`。删除失败返回错误信封，绝不会是 `deleted: false`。

| 状态码 | 什么时候 |
| --- | --- |
| `400` | schema 放过了、但服务端用不了的输入——日志查询非法、资源路径非法、bot 名非法、引擎不支持 |
| `401` | 没有可用凭证，或这个操作必须有真人（§6） |
| `403` | `user_id` 填的不是你认证的那个人 |
| `404` | 不存在——**或者**不属于你——**或者**你没有就它被授权。三者按设计完全相同 |
| `409` | 与当前状态冲突——重名、配额已满、该 stage 没有存活运行时、stage 只读、设备未就绪 |
| `413` | 请求体超过已公布的上限（启动脚本 24 KiB、skill 包、文件预览） |
| `422` | 校验失败——缺失或格式错误的 query 参数，最常见的是 `user_id` |
| `500` | 内部错误 |
| `501` | 仅运行时那一组：这个 bot 的引擎不提供该能力，见 `…/engine/capabilities` |
| `502` | 平台的某个依赖失败 |
| `504` | bot 的运行时没有在时限内应答 |

---

## 8. 调用被拒绝时

响应体是刻意不提供信息的，所以请从你发出去的东西开始诊断。

### `401`

看 `code`：

- **`401001` —— 你的凭证没被接受。** 要么是某个必需身份缺凭证（对照 §2 和 §6：最常见
  的失误是对一个还需要真人的操作只发了 API Key），要么是凭证在但无效——SSO 会话过期、
  Key 不是 `ACTIVE`、用了别的环境的 Key。另外确认 API Key 确实放在
  `Authorization: Bearer` 里：放在别的 header 里等于没有携带任何应用身份。
- **`401000` —— 凭证没问题，但这个操作不接受没有真人的调用者。** 对照 §6，改成形态 B
  重发。对集成方来说这是最常见的 `401`。

> 如果某个环境下**所有**调用都答 `401`，包括本该成功的，那是这个环境部署有问题。请找
> 平台运维，而不是继续调你的客户端。

### `403`

只有一种原因：`?user_id=` 不是你所认证的那个人的 id。它必须等于你所带的 SSO 凭证解析
出来的那个人。

单独调用的应用**不会**因此拿到 `403` —— 它的 `user_id` 是拿去比对授权的，填错了是
`404`。

### 明明存在却拿到 `404`

按可能性从高到低：

1. **没有授权。** 单独调用，但这个用户就这个 bot 没有存活的授权。用
   `GET /openapi/v1/bots/authorized` 确认。
2. **授权用户失去了对该 bot 的访问权** —— 不再是 owner，也不再是 member 级协作者。
   这是每次调用现问的，所以哪里都不会有撤销记录。
3. **共享 bot 上漏了 `owner_id`。** 它默认为调用者，所以省略它寻址到的是*那个用户
   自己*同名的 bot ——而那个多半不存在。
4. **租户不对。** 你的 Key 所属租户就是数据边界；别的租户里创建的 bot 你看不见。
5. **`user_id` 填错了人** —— 对应用来说，应该填授权给你的那个用户，而不是 bot 的
   owner。bot 一旦是共享的，这两个人就不是同一个。

### `422`

几乎总是 `user_id` 缺失或为空。除了 §4 里那四个目录类读取，所有操作都要求它，而且
永远放在 **query string** 里——无论什么方法、body 是什么。

### 运行时那一组的 `409`

- `"No live runtime at the requested stage"` —— 你对一个在该 stage 没有存活发布的 bot
  请求了 `verify` 或 `online`（个人 bot 永远没有）。不会回退，别改成别的 stage 重试。
- `"The requested stage is read-only"` —— 对已发布 stage 发起了写。什么都没写，再发布
  一次也不会让它落地。请写 `draft`，然后发布。
- `"Bot device is not ready"` / `"Bot has no active device"` —— bot 存在但运行时没起来。
  轮询 `GET …/{bot_id}/status`。

---

## 9. 这套接口能做什么

| 分组 | 地址 | 做什么 |
| --- | --- | --- |
| bots | `/openapi/v1/bots`、`…/{bot_id}` | 创建、列表、详情、更新、删除、重启、重名检查、配额上限、授权状态轮询、运行状态、passport、引擎配置、启动脚本 |
| sessions | `…/{bot_id}/sessions` | 会话及其消息 |
| engine | `…/{bot_id}/engine` | 运行时状态、能力、可用性 |
| approvals | `…/{bot_id}/approvals` | bot 的审批模式 |
| models | `…/{bot_id}/models` | 该 bot 引擎提供的模型 |
| connection | `…/{bot_id}/connection` | **可直接使用的聊天 socket URL** |
| skills | `…/{bot_id}/skills` | 本地 skill 的安装、列表、启用、停用、删除 |
| routines | `…/{bot_id}/routines` | 定时任务、执行与历史 |
| resources | `…/{bot_id}/resources` | bot 工作区文件——列表、stat、上传、下载、预览、mkdir、删除 |
| identity | `…/{bot_id}/identity` | bot 的身份文件 |
| harness | `…/{bot_id}/harness` | 诊断 bot 的线上配置，预览 / 应用 / 回滚补丁，读取诊断报告及其历史 |
| authorized-apps | `…/{bot_id}/authorized-apps`、`…/bots/authorized` | 授权记录（§5） |
| mcp | `…/bots/mcp` | MCP 市场目录，以及账号级 server 配置 |
| logs | `…/bots/logs` | 跨 bot 的 trace 级可观测面（需要两个凭证） |
| loadtest | `…/bots/loadtest` | 一个 echo 端点和一个 socket，用于压测平台 |

### 和 bot 聊天

聊天不走这套 HTTP 接口。调用
`GET /openapi/v1/bots/{bot_id}/connection`，它返回可直接使用的 WebSocket URL 和一个
过期时间，由你的客户端自己去连。凭证已经在 URL 里，所以握手不需要任何 header——这也是
浏览器能直接打开它的原因。

这条连接被设计成可以活得比凭证有效期更久：凭证只在握手时校验一次。如果你在客户端前面
放了代理，不要给这个路径设空闲读超时。

### 创建 bot 有时是两步

`POST /openapi/v1/bots` 要么返回 `201` 带上 bot，要么返回 `202` 带上一个"授权待完成"的
载荷。收到 `202` 时轮询 `POST /openapi/v1/bots/{bot_id}/auth-status` 直到出结果——它是
`POST`，因为它会**完成创建**，不是一次读。（还有一个 `GET` 拼写，正在退役。）

### 启动脚本

`PUT …/{bot_id}/startup-script` 会把一段 `bash` 追加到 bot 的启动序列。用它之前要知道：

- 它在平台组装的**每一次**启动时都会跑——创建、重启、发布——而且不去重，所以**必须写成
  幂等的**；
- 修改在**下一次**启动时才生效，永远不会作用于运行中的容器，因此第一次写完总要重启一次；
- **不要把密钥放进脚本体。** 它按原样存储，并会以可还原的形式出现在平台日志里；
- 体积上限 24 KiB（超了 `413`），运行本身没有时间上限——启动要等你的脚本退出才会被报告
  完成，所以长耗时的活请自行放到后台；
- 输出写在容器内的 `/home/admin/logs/startup_script.log`，目前没有读取它的接口；
- 有两类 bot 完全不能跑启动脚本，`GET …/startup-script` 会对它们报 `supported: false`；
  这时的写入会被 `409` 拒绝，而不是存下来却永远不执行。

### 旧地址

这套接口在改为 bot 优先寻址之前服务过的每一个地址仍然可用，参数名和位置都保持原样，
所以现有客户端不受影响。新客户端请使用 §4 里 `{bot_id}` 优先的地址：部分旧地址会忽略
`stage` 而始终作用于 draft ——其中包括两个写操作，因此它们会报告成功，而它们的替代地址
返回 `409` 且什么都不写。

---

## 10. 集成检查清单

1. 拿到 API Key，记下 `app_id` 和租户（§3.1）。确认状态是 `ACTIVE`。
2. 逐个调用确认自己处在哪种形态（§2）。真人的身份放 `x-one-id` 或 `IAM_TOKEN` cookie，
   API Key 只放 `Authorization: Bearer`。
3. 让每个用户就他的 bot 授权你（§5），两个凭证同时在线。
4. 用 `GET /openapi/v1/bots/authorized` 确认自己的权限范围。
5. 对每一个你打算单独调用的操作先查 §6。凡是标着**必须有真人**的，都需要把人设计进你
   的产品流程——不是设计进重试逻辑。
6. 除了四个目录类读取，每次调用都带 `user_id`；bot 不属于授权用户自己时带 `owner_id`；
   只有确实要访问已发布运行时时才带 `stage`。
7. 记录每个响应里的 `request_id`。对于接口不会告诉你原因的拒绝，它是支持人员唯一的抓手。
