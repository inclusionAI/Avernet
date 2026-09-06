# Bot 配置清单 用户手册

> **状态：DRAFT（讨论稿），随特性实现同步修订。**本文是「Bot 配置清单」的
> **使用**说明——面向要给 bot 配内容的业务方，讲怎么把一份清单写出来、发上去、
> 确认它生效了、以及出问题时怎么查。规范性字段定义见
> `manifest-schema.zh-CN.md`，设计论证见 `design.zh-CN.md`，完整业务案例见
> `examples.zh-CN.md`。
>
> **本文按已拍板的口径写**，并与 `manifest-schema.zh-CN.md` 保持一致——两份
> 用户可见的契约说的是同一件事。`design.zh-CN.md` 早于其中几条决策、未随之
> 修订，差异清单见**附录 D**。尚未开放的写法见**附录 C**（与 schema §7
> 同一张表）——写了会在 `PUT` 时被拒绝，而不是被静默忽略。**要查某个端点该填什么、
> 回什么、枚举有哪些取值，直接看附录 B**——它逐端点列出请求字段与响应字段，也包括
> 「用清单创建 bot」这条路径的两个端点。

## 目录

1. [它替你做什么](#1-它替你做什么)
2. [上手：两条路径](#2-上手两条路径)
3. [五条心智模型](#3-五条心智模型)
4. [完整工作流](#4-完整工作流)
5. [逐类目怎么写](#5-逐类目怎么写)
6. [版本、钉扎与失败策略](#6-版本钉扎与失败策略)
7. [生效时机](#7-生效时机)
8. [你会失去什么](#8-你会失去什么刻意接受的代价)
9. [排错手册](#9-排错手册)
10. [限额](#10-限额)
11. [安全须知](#11-安全须知)
12. [FAQ](#12-faq)
13. [附录](#附录-a完整清单示例)（A 完整示例 · **B API 参考——逐端点的请求/响应字段与枚举取值** · C 尚未开放 · D 与设计文档的差异 · E 字段速查）

---

## 1. 它替你做什么

**今天**：新开一个 bot，或者更新一次话术，你要按顺序手工调好几个 TC Open API
——传 identity、传 skill、传资源、开 MCP。容器重建之后 bot 立刻可用，但内容可能
还是旧的；扩容出来的实例之间还可能不一致。

**用配置清单之后**：这些意图写进**一份文档**，`PUT` 一次。此后 bot 每次创建、
发布、重建，平台自动把它的实际状态收敛到你声明的样子。

| | 手工调 API | 配置清单 |
| --- | --- | --- |
| 新开一个 bot | 按顺序调 4~5 个接口，漏一个就少一样 | 创建时带上清单，第一个容器就是配好的 |
| 内容升一版 | 每个 bot 重跑一遍上传流程 | 改一行 `ref`，下一个 apply 点全套一起升 |
| 容器重建 | 重建后要人记得补 | 自动收敛，不需要人 |
| 扩容 | 实例可能不一致 | 实例共享同一份平台状态，天然一致 |
| 「线上跑的是哪一版」 | 靠人记 | apply 报告里记着解析出的 commit SHA |

一句话心智模型：**你声明状态，平台负责让它成真；每个 apply 点重新对齐。**

**它不是什么**：不是让你往容器里写文件的通道。清单里没有任何地方写引擎的物理
路径——「装一个 skill」是意图，装到哪个目录是引擎的事。需要在容器内跑命令的长尾，
走 `script`（且只有 ARCA 系支持，见 §5.5）。

---

## 2. 上手：两条路径

先分清你在哪条路上——它决定第一步做什么。**两条路的清单文档一模一样**，区别
只在它怎么被提交：

| 你的处境 | 走哪条 |
| --- | --- |
| **还没有 bot**（多数新用户） | **路径 A**：先写清单，再**用清单创建 bot**——它的第一个容器就是配好的（§2.2） |
| **已经有 bot** | **路径 B**：把清单 `PUT` 上去，**立即生效**（§2.3） |

鉴权与你今天调用 TC Open API 上传 skill 时完全一致。

### 2.1 先看这张表：你要建的 bot 支持什么

**不需要任何 API，也不需要先有 bot。**能力只由「**引擎类型 + bot 类型**」决定
——不查容器、没有第三种「未知」状态。所以在你还没有 bot 的时候，这张表就是答案；
它同时也是你**挑引擎**时该看的东西：

| 你要建的 bot | manifest（identity / skills / resources / mcp） | `script`（启动脚本） |
| --- | --- | --- |
| openclaw / aicoding / hermes / moltis | ✅ 全部 | ✅ |
| claude_code | ✅ 全部，但 **identity 只允许 `CLAUDE.md`** | ✅ |
| teclaw | ✅ 全部 | ❌ **写入即拒**（容器内没有执行通道） |
| desktop bot | 不在本特性范围内 | ❌ |

还有两道与引擎无关的门，**对所有人都关着**：类目 `engine_config`（没有物化器），
以及 **`resources` 条目用命名源 `from` 或 git 源**（resources 物化器目前只走 URL
那条路）——写了会在提交时被拒绝，清单见**附录 C**。

⚠️ **取源形态不是全局开关，要按类目看**：条目内联的 HTTPS `source` URL 与内联 `content`
到处都能用；**命名源 `from` 与 git 源只有 `skills` 与 `identity` 两个类目真正能用**。
`resources` 写了会在 `PUT` 就被拒；**`cli_tools` 更麻烦——它写了能通过 `PUT`，却会在
apply 时失败**（物化器把 `from` 的源名当成 URL 直接去取），所以 `cli_tools` 请一律写
内联 `source` URL。整张表见**附录 C**。

**已经有 bot 的人**可以直接问平台，答案与上表同源（同一个函数，所以不会出现
「这里说支持、`PUT` 却拒绝」）：

```text
GET /openapi/v1/bots/{bot_id}/config-manifest/capabilities
```

### 2.2 路径 A：还没有 bot

**第 1 步：写一份最小清单。**下面这份不需要 git 仓库、不需要凭证、不需要任何
外部服务——先跑通闭环，再往里加东西（每个类目怎么写见 §5）：

```yaml
schema_version: 1

manifest:
  identity:
    - type: SAFETY.md
      content: |
        # 安全边界
        不承诺退款金额；涉及资损问题一律转人工。
```

**第 2 步：把它连同普通创建参数一起提交**（引擎、名称、描述……）。这是一个
**异步**创建接口：

```text
POST /openapi/v1/bots/with-manifest        ← 202 + bot_id + 授权链接
（body = 普通创建参数 + config_manifest: "<清单 YAML 全文>"）
```

- **清单在申请授权之前就被校验**——不会让你点完授权才被告知清单写错了；
- **被校验的清单就是被应用的清单**，它在第一段落库，轮询时不重新提交。

**第 3 步：点开返回的 Passport 授权链接。**每个 bot 一次，这是 AgentPass 的
固有限制，不是本特性引入的（所以「一份清单批量创建 N 个 bot」= N 次点击）。

**第 4 步：轮询到终态**（状态机见 §4.5）：

```text
GET /openapi/v1/bots/{bot_id}/with-manifest/status
```

`READY` 与 `APPLY_FAILED` **都一定带 `bot`**——`APPLY_FAILED` 意味着 **bot 在跑**，
只是配置缺了一块；拿不到 bot 的失败叫 `CREATE_FAILED`，是另一件事。

`apply` 报告通常也在，会逐条告诉你哪些条目下发了、哪些没有；但**这两个状态下它都可能是
`null`，写客户端时务必判空**（容器后那一段没能启动，或创建之后又跑过一次显式 apply 把
记录顶掉了）。为 `null` 时改读 `GET …/config-manifest/last-apply`。两种情形与处理方式见
**附录 B.3.2**。

**第 5 步：以后要改**，不必重建 bot——改清单 `PUT` 上去即可，走路径 B 的第 3
步起，**立即生效、不需要重启**。

> 这两个端点逐字段的出入参见**附录 B.3**；本特性全部端点的参考见**附录 B**。

### 2.3 路径 B：已经有 bot

**第 1 步（可选）：探能力。**

```text
GET /openapi/v1/bots/{bot_id}/config-manifest/capabilities
```

**第 2 步：写清单**（同上那份最小示例即可）。

**第 3 步：发上去。**

```text
PUT /openapi/v1/bots/{bot_id}/config-manifest
（body 即上面的文档）
```

`PUT` 是 **all-or-nothing** 的：任何一条不合法或不受支持，整份文档被拒绝并列出
逐条原因，**什么都不写入**。接受之后**立即生效，不需要重启**（`script` 除外，
见 §7）。

**第 4 步：确认它真的生效了。**

```text
GET /openapi/v1/bots/{bot_id}/config-manifest/last-apply
```

这是「我的 manifest 生效了吗」的**权威答案**。看两个地方：顶层 `result`，以及
每一条的 `action`（`created` / `updated` / `unchanged` / `skipped` / `failed`）。

**第 5 步：改一改，再看一次。**把 `content` 改一句话，重新 `PUT`，再读一次报告
——这次那一条应该是 `updated`；一字不改地再 `PUT` 一次，则应该是 `unchanged`、
零动作。**这就是收敛**：同一份文档应用 N 次等于应用一次。

### 2.4 想手工触发一次 / 先看看会发生什么

```text
POST /openapi/v1/bots/{bot_id}/config-manifest/apply              ← 202 + apply_id，后台执行
POST /openapi/v1/bots/{bot_id}/config-manifest/apply?dry_run=true ← 同步返回计划，不动手
GET  /openapi/v1/bots/{bot_id}/config-manifest/applies/{apply_id} ← 轮询这一次
GET  /openapi/v1/bots/{bot_id}/config-manifest/last-apply         ← 最近一次
```

**apply 不阻塞。**它立刻返回 `202` 和一个 `apply_id`，真正的下发在后台进行——因为
apply 要写设备，将来（W5）还要走网络取源，让调用方举着一条 HTTP 连接等它是不行的。
拿 `apply_id` 去轮询；`result` 在做完之前是 `RUNNING`，之后才是
`SUCCEEDED` / `PARTIAL` / `FAILED`。丢了 id 也不要紧，`last-apply` 给你最近的一次。

**能立刻回答的都立刻回答，而且在发 id 之前**：这个 bot 已经有 apply 在跑 → `409`；
存下来的文档对这个 bot 已经不合法（比如引擎换了）→ `422`。**你永远不会拿到一个
「其实没跑起来」的 id。**

`dry_run` 是**同步**的，计划直接在响应体里——一个要靠轮询才知道结果的预览不叫预览。
它什么都不写，连报告行都不写，所以也不发 `apply_id`、不出现在历史里。**上线前先
dry_run** 是推荐动作，尤其是第一次声明 `skills` 或 `resources` 的时候（原因见 §8）。
这几个都需要 bot 已存在——**路径 A 的人在创建请求里就完成了同样的校验**（第 2 步）。

> 这四个端点逐字段的出入参（含 `409` / `422` 分别在什么时候出现）见**附录 B.2**。

### 2.5 本特性一共有哪些端点

一张全景图，够你判断「这件事该调谁」。**每个端点要填哪些字段、回哪些字段、枚举
有哪些取值，见附录 B**——右边的箭头就是它在附录 B 的小节号。

```text
清单本体（每个 bot 一份文档）                                      → B.1
  GET    /openapi/v1/bots/{bot_id}/config-manifest                 读回原文
  PUT    /openapi/v1/bots/{bot_id}/config-manifest                 整体替换 + 自动 apply
  DELETE /openapi/v1/bots/{bot_id}/config-manifest                 清除声明（不删实体）
  GET    /openapi/v1/bots/{bot_id}/config-manifest/capabilities    这个 bot 支持哪些构造

apply（把声明变成现实）                                            → B.2
  POST   /openapi/v1/bots/{bot_id}/config-manifest/apply           202 + apply_id
  POST   .../config-manifest/apply?dry_run=true                    200，同步返回计划
  GET    /openapi/v1/bots/{bot_id}/config-manifest/applies/{id}    轮询这一次
  GET    /openapi/v1/bots/{bot_id}/config-manifest/last-apply      最近一次（权威答案）

用清单创建 bot（还没有 bot 时；路径 A）                            → B.3
  POST   /openapi/v1/bots/with-manifest                            202 + bot_id + 授权链接
  GET    /openapi/v1/bots/{bot_id}/with-manifest/status            轮询创建状态

源凭证（租户级，跨 bot 复用）                                      → B.4
  GET    /openapi/v1/bots/source-credentials                       列表（掩码）
  GET    /openapi/v1/bots/source-credentials/{name}                单个（掩码）
  PUT    /openapi/v1/bots/source-credentials/{name}                注册 / 轮换
  DELETE /openapi/v1/bots/source-credentials/{name}                删除

CLI 工具（与清单类目 cli_tools 同一个组件）                        → B.5
  POST   /openapi/v1/bots/{bot_id}/cli-tools                       装一个（同名答 409）
  GET    /openapi/v1/bots/{bot_id}/cli-tools                       平台记录的全部
  DELETE /openapi/v1/bots/{bot_id}/cli-tools/{name}                移除一个

同一份状态的「另一扇门」（不感知清单）                             → B.6
  GET/PUT/DELETE  /openapi/v1/bots/{bot_id}/startup-script         启动脚本，与 script 同一行
  GET/PUT         /openapi/v1/bots/{bot_id}/engine/config          引擎配置（清单类目未开放）
  …以及 identity / skills / resources / mcp 各自的管理 API
```

**这不是几组无关的接口。**清单本体是「你想要什么」，apply 是「平台去做」，
创建 API 是「在还没有 bot 的时刻把前两件事塞进创建流程」，凭证是「取源时用谁的
身份」，另一扇门则是同一份状态的手工入口——被清单声明的类目上，那扇门的改动会在
下一个 apply 点被覆盖回去（§3.2）。

**每个响应都包在同一个信封里**（`{ code, message, data, request_id }`），下文与附录 B
说的字段都是 `data` 里的字段；信封本身见 B.0。

---

## 3. 五条心智模型

写清单之前，这五条先立住。绝大多数「怎么和我想的不一样」都是其中某一条没吃透。

### 3.1 你声明「要什么」，不声明「放哪」

`skills` 写 `name`、`identity` 写 `type`、`mcp` 写 `server_code`——都是标识符，
不含位置信息。**只有 `resources` 能指定位置**（`path`，而且是 workspace 相对的
逻辑路径，不是引擎物理路径）。不同引擎的 skills 目录、identity 文件位置本来就
不同，这个差异不该由你承担。

### 3.2 声明获胜

每个 apply 点重新取源、覆盖现状。**你在界面里手改了被清单管着的东西，下一个
apply 点会被改回去。**想改内容，改源头（仓库里的文件）或改清单——不是在界面里改。

这条不是平台霸道，是 #926 的立项动机：手工获胜的话，老实例带着手调过的
SOUL.md、新实例从清单长出来，同一个 bot 两个实例人设不同。

**那我怎么知道哪些东西归清单管？**看清单本身，以及上次 apply 做了什么——
**界面上不会给它们打标记**：清单装出来的实体与你手工创建的存储完全一样，下游
无从区分，也不需要区分。所以「谁管着这个 skill」的答案在
`GET …/config-manifest`（声明了什么）与 `GET …/last-apply`（上次实际写了什么）
里，不在实体的某个标志位上。

### 3.3 覆盖的单位是「类目区域」

让清单生效 = 把**每个被声明的类目**覆盖到恰好等于声明。没声明的类目**完全不碰**。

| 类目 | 被覆盖的区域 |
| --- | --- |
| `skills` | **active skill set**。它等于声明；没列出的 skill 被移除 |
| `identity` | **identity 文件集合**，减去保留名单（见 §8） |
| `resources` | **仅声明的 `path` 子树**。声明的 `path` 之外一律不碰——workspace 是 bot 的工作区，不是清单的 |
| `mcp` | **已启用的 server 集合** |

`resources` 是绝不能读成「区域 = 整个 workspace」的那一个。

**最容易搞错的是这三态，务必分清**：

| 写法 | 含义 | 结果 |
| --- | --- | --- |
| `skills:` 整段不写 | 「我对 skills 没意见」 | 一个都不碰 |
| `skills: []` | 「skill 集合是空的」——空集合**也是**声明 | **所有 skill 被移除** |
| `DELETE` 整份清单 | 没有任何类目被声明 | **什么都不删**，此前装上的东西留在 bot 上，只是不再有人管它 |

后两行看起来矛盾，其实是同一条规则：`[]` 是声明，缺席不是声明。

### 3.4 类目是 all-or-nothing 写入的

一个被声明的类目里，只要有**任何一条**取不回来，**该类目完全不覆盖**——关于它的
一切保持原样，而每一条的结果照样记进报告。

这是在保护你：在覆盖语义下，一个不完整的集合是**破坏性**的。声明 `{A, B}` 却只
写进 `{A}`，就等于把 B 删了——一次网络抖动删掉一个正在工作的 skill，这不能接受。

一个类目的失败**不牵连**别的类目：`skills` 全军覆没时，`identity` 该怎么写还怎么写。

### 3.5 secret 永远不进清单

清单会被原样 `GET` 读回、会进变更审计；`script` 的下发链路日志可见。所以：

- **不要**在 `source` URL 里写 `https://user:token@host/...`（userinfo 形式在 `PUT`
  和 fetch 两道门上都会被拒绝）；
- **不要**在 `script` 体内写任何密码、token；
- 私有源鉴权一律走**凭证引用**（§4.2）：清单里只出现凭证的**名字**。

---

## 4. 完整工作流

§2 是最短路径，这一节是把每一步展开。**§4.1–§4.3 两条路径通用**（规划仓库、
注册凭证、写清单）；之后分岔：**没有 bot 的人看 §4.5**（用清单创建 bot），
**已有 bot 的人看 §4.4 与 §4.6**（dry_run、`PUT`）；**§4.7–§4.8 又回到通用**
（读报告、升版本）。

### 4.1 先规划内容仓库

清单的六个类目按内容本性分两组，这条线决定各自该放哪：

| 内容 | 本性 | 放哪 |
| --- | --- | --- |
| identity / skills / resources | 文本表达（md、`SKILL.md`、csv/json…） | **公司 git 仓库**，以 tag 发版 |
| engine_config / mcp | 键值、注册表引用 | **就写在清单里**，没有「源」这回事 |
| cli_tools | 二进制制品 | **制品库（OSS）+ 强制 digest**——二进制进 git 是反模式 |

推荐的仓库布局（一个仓库、一个 tag，三类内容一起升版本）：

```text
team/content.git   ← tag: v1.2.0
├── bots/
│   └── <bot_id>/soul.md          ← 每个 bot 一份的人设
├── kb/
│   ├── service-rules.md          ← 全体共享的话术规范
│   ├── faq.csv
│   └── ...                       ← 整个目录作为知识库下发
└── skills/
    └── quality-check/
        ├── SKILL.md
        └── ...
```

这么放的收益在 §4.8：**升一版 = 改一行 `ref`**，identity、skills、知识库原子地
一起走，不会出现「人设升了、话术没跟上」。

### 4.2 一次性：注册源凭证

内容仓库是私有的，就得先告诉平台「用什么身份去取」。凭证是**租户级**命名对象，
一次注册、所有 bot 的所有清单都能引用。

```text
PUT /openapi/v1/bots/source-credentials/corp-git-content
{
  "type": "header",
  "header_name": "Authorization",
  "secret": "Basic <base64('git:<访问令牌>')>",
  "allowed_prefixes": ["https://code.example-corp.com/team/content"]
}
```

**四个字段的意思**：

| 字段 | 说明 |
| --- | --- |
| `{name}`（URL 里） | 凭证名，自由标识符。清单里用 `auth: corp-git-content` 引用它。它与域名之间**没有**任何推导关系 |
| `type` | 判别键是**认证机制**，不是存储类型。v1 只实现 `header`；`oss_aksk`、`basic` 是保留值，写了会被拒绝 |
| `header_name` + `secret` | fetch 时原样注入的请求头。`secret` 是**完整头值** |
| `allowed_prefixes` | **必填，至少一项**：这个凭证允许被出示给哪些 URL 前缀 |

**`allowed_prefixes` 为什么必填**：git 服务和对象存储都是**单 origin 承载大量
互不相关的内容**。只按域名放行的话，任何有清单编辑权的人把 `source` 改指同域名下
别人的仓库，就能套用你的 token。前缀把授权粒度收到「这个仓库」「这个桶前缀」，
并且由平台校验，不依赖托管服务具备任何能力。

匹配按**路径段边界**：目标要么等于前缀，要么以「前缀 + `/`」开头。
`…/team/content` **不会**匹配 `…/team/content-secret`。端口是 origin 的一部分
（`https://host:8443` 与 `https://host` 是两个服务）。想覆盖整个域名，必须显式
写 `https://host/`——这是一个明确选择，不是默认。

**目标不在名单内 → 该条目 `failed`，不会「不带凭证继续请求」。**跨前缀的重定向
同样直接失败，凭证不会被重定向带走。

**用什么 token**（纵深防御，与前缀叠加）：

| 选择 | 结论 |
| --- | --- |
| 仓库级/桶级只读 token（Deploy/Project Token 类） | **首选**，天生单仓库有效 |
| **专用机器账号 + 只读令牌** | 托管服务没有仓库级 token 时用它，靠账号的**成员身份**收权（只把它加进内容仓库） |
| 个人 PAT / 个人私有令牌 | **不要用**。权限面是这个人全部可见的仓库，生命周期还绑定个人（转岗离职即断） |
| 带完整读写 scope 的 API 令牌 | **不要用**。为一件只读的工作换来对一切的写权限 |

> **Ant Code 用户注意**：Ant Code 没有 Deploy Token，且它的两种 scope 里，只读的
> 那个（`read_repository`）**只有 Git-over-HTTP、没有 API**。所以口径是
> **专用机器账号 + `read_repository` 访问令牌**，按 HTTP Basic 注入——即上面例子里
> 的 `Authorization: Basic base64("git:<token>")`。**令牌过期由你自己管**，平台不
> 轮换；过期时 apply 报告会明确写成「凭证 `<name>` 被拒绝」，与普通网络错误区分开，
> 见 §9。

**生命周期**：

- **读回是掩码的**——`GET /openapi/v1/bots/source-credentials/{name}` 只返回
  `has_secret` / `type` / `header_name` / `allowed_prefixes` / `owner_app_id` /
  `updated_at`，任何路径下都不返回值；不带 `{name}` 的 `GET` 是租户内的列表，按名字
  排序，字段更少（`name` / `has_secret` / `updated_at`）。日志、错误信息、apply 报告
  里只出现凭证**名**。
- **凭证有「属主应用」**：第一次 `PUT` 一个没被占用的名字，调用方那个应用就成了它的
  owner；**此后只有 owner 应用能轮换或删除它**（同租户的其他应用得到 `403`）。因为
  一次轮换替换的是整行，而租户内所有引用这个名字的清单都依赖它。
- **轮换 = 对同一个名字重新 `PUT`**。不触发 apply，下一个 apply 点自然用新值。
- **删除一个仍被引用的凭证** → 引用它的条目在下次 apply 记 `failed`（「credential X
  不存在」），进而按 §3.4 让整个类目不写。

不需要凭证的情形照旧成立：公开源、靠网络 ACL 自保护的源、签名 URL（注意过期）。

### 4.3 写清单：骨架

```yaml
schema_version: 1          # 必填，v1 固定为 1；未知版本拒绝写入

sources:                   # 可选：命名源，一处声明、多处引用
  content:
    git: https://code.example-corp.com/team/content.git
    ref: v1.2.0
    auth: corp-git-content
    mode: non_strict       # 可选：移动 ref 的策略，见 §6.2

manifest:                  # 声明式部分
  identity: [ … ]
  skills:   [ … ]
  resources: [ … ]
  mcp:      [ … ]

script:                    # 命令式部分，能力门控（teclaw 拒绝）
  body: |
    #!/bin/bash
    …
```

三段都可以缺省。条目的**来源四选一、互斥**：

| 写法 | 什么时候用 |
| --- | --- |
| `from` + `subpath` | **推荐**。引用一个命名源，取其中某个子路径 |
| `source` | 内联来源：一个 HTTPS URL 字符串，或一个结构化 git 引用。单条目、跨仓库、一次性来源时用 |
| `content` | 内联 UTF-8 文本。**不推荐**——内容游离于版本控制之外，只用于一次性小片段 |
| 注册项引用 | 仅特定类目：MCP 的 `server_code` |

内联 `content` 条目没有 fetch 环节，所以 `auth` / `digest` / `on_fetch_failure`
对它一律非法（写了报错）。

**变量替换**：`source` URL 与源内路径里可以用一小组平台注入变量，`script` 里它们
是环境变量。**只接受这四个**，未知占位符在 `PUT` 时报错：

| 变量 | 含义 |
| --- | --- |
| `${BOT_ENGINE_TYPE}` | 当前引擎类型 |
| `${BOT_ENV}` | 环境（dev/prod/…） |
| `${BOT_TENANT}` | 租户标识 |
| `${BOT_ARCH}` | 目标 CPU 架构，当前恒为 `amd64` |

> 设计文档早期写的是 `OCB_*`。**用 `BOT_*`**——`OCB` 是内部代号，不是面向用户的
> 命名空间。写 `${OCB_BOT_ID}` 会被 `PUT` 拒绝。

> **没有 `${BOT_ID}`。**这几个变量都是**机群**属性（环境、租户、引擎、架构），
> 所以一份文档才能给多个 bot 复用。bot 标识不是：它在创建时生成（日期 + 8 位
> 随机字符），你指定不了，在 git 里准备内容时也无从得知。要按 bot 区分，就在
> 那个 bot 自己的 manifest 里把路径写成字面量。`${BOT_ID}` 会被 `PUT` 拒绝。

替换发生在**取源之前、也在前缀授权之前**，所以替换出来的 URL 逃不出凭证的
`allowed_prefixes`。

### 4.4 先 dry_run

```text
POST /openapi/v1/bots/{bot_id}/config-manifest/apply?dry_run=true
```

返回**计划**：每一条会发生什么、取哪个源、解析出哪个 SHA。不做任何写入。第一次
声明 `skills` 或 `resources` 时值得跑一次——因为覆盖语义会移除区域内没被声明的
东西（§8），而 dry_run 是在动手之前看清这件事的地方。

**它需要 bot 已存在**（要有 `{bot_id}` 才寻址得到）。**还没有 bot 的人不必找
替代品**：创建请求会在申请授权之前对清单跑同一套校验（§4.5），写错不会浪费一次
授权；只是「取源会取到什么」要等创建流程里的那次 apply 才知道，看它返回的报告。

**dry_run 是同步的**，和真正的 apply 不一样：计划就在响应体里，不需要轮询。它也
不写任何东西——不改配置，也不写 apply 记录，所以不发 `apply_id`、不出现在
`last-apply` 里。

### 4.5 路径 A：用清单创建 bot（还没有 bot 时）

只靠 `PUT` 补不上一个洞：bot 还不存在时，你没法往 `/bots/{bot_id}/config-manifest`
写东西，于是它的**首启**永远是空的。所以有一个**用清单创建 bot** 的公开 API——
把清单连同普通创建参数（引擎、名称、描述……）一起提交，清单在 bot 记录写入之前
就已在手，apply 在创建过程内部执行，**第一个容器就带着配置**。

它是**异步**的（创建很慢），调用方轮询状态：

```text
AWAITING_AUTHORIZATION   等用户点开 Passport 授权链接
        │                （响应携带 iframe_url / redirect_url）
        ├──► AUTHORIZATION_REJECTED   终态——用户拒绝了
        ├──► AUTHORIZATION_EXPIRED    终态——窗口内没人点（默认 10 分钟）
        ▼
CREATING                 已授权；bot 记录已写入，容器正在开通
        ├──► CREATE_FAILED   终态——**没拿到可用的 bot**，与清单无关
        ▼
APPLYING                 清单 apply 进行中（取源 → 物化 → 下发）
        ├──► READY          终态——bot 起来了，清单完整生效
        └──► APPLY_FAILED   终态——**bot 起来了**，部分配置没下发
```

**这套状态词表只出现在轮询响应里**：提交那一次的响应只给 `bot_id` 和授权链接，
不带任何状态——刚提交的创建按定义就是「等授权」，一个能装下终态的字段只会诱导
调用方去判断一个不可能出现的值。

用之前先知道这几件事：

- **三种失败是分开的，不用读文案就能分辨**：清单不合法 = 提交时 `422`（连
  `bot_id` 都没有）；`CREATE_FAILED` = **没有可用的 bot**；`APPLY_FAILED` =
  **bot 正在运行**，只是配置缺了一块。别把后两个当成一件事。
- **`APPLY_FAILED` 不需要重建 bot**：bot 记录从头到尾没被触碰，响应里**带着
  `bot`**，改完清单 `POST …/apply` 收敛即可。（响应里的 `apply` **可能为 `null`**——
  容器后那一段没能启动时就没有报告可给；详见附录 B.3.2。）
- **某个类目没被覆盖（`PARTIAL`）汇报为 `APPLY_FAILED`，不是 `READY`。**按 §3.3
  的类目覆盖语义，一个半途失败的类目可能已经**删掉**了旧条目却没写进新的——这是
  要处理的状态，不是带脚注的成功。
- **清单在申请 Passport 之前就被校验**——不会让你点完授权才被告知清单写错了。
- **提交时比 `PUT` 多一条拒绝**：本期没有物化器的类目（今天只剩 `engine_config`）
  在这里**被拒**，而不是先存下来。原因是这条路径上「先接受」的代价是一次授权、
  一个已创建的 bot，然后才失败。
- **被校验的清单就是被应用的清单**：它在第一段落库，轮询时不重新提交——轮询端点
  不接受清单，也不接受任何创建参数。
- **轮询是纯读**：它不查 AgentPass、不触发任何工作、不写任何东西。轮得快一点不会
  让创建变快，停止轮询也不会让创建停下。
- **超时会自己收尾**：窗口过了没人授权，创建转 `AUTHORIZATION_EXPIRED`，落库的
  清单和已经写下的启动脚本行一并删掉——不会留下一份挂在永远不会存在的 `bot_id`
  上的清单。
- **创建永远需要人点一次授权链接**，这是 AgentPass 的限制，不在平台控制范围内。
  所以「用一份清单批量创建 N 个 bot」是 N 次点击——不要按相反的假设做方案。把
  **一份清单应用到多个已有 bot** 则完全不涉及授权。

两个端点：

```text
POST /openapi/v1/bots/with-manifest
{
  "bot_name": "research-assistant",
  "bot_desc": "…",
  "engine": "openclaw",
  "bot_type": "personal",
  "cluster_name": "ACRA",
  "config_manifest": "schema_version: 1\nmanifest:\n  identity:\n    - …"
}
→ 202 { "bot_id": "20260813_a7k2m9p1",
        "iframe_url": "https://…/consent?flow=…", "redirect_url": "" }

GET /openapi/v1/bots/{bot_id}/with-manifest/status
→ 200 { "state": "READY", "bot_id": "…",
        "iframe_url": "", "redirect_url": "",
        "bot": { … }, "apply": { …apply 报告… }, "message": "" }
        ↑ bot 在终态一定有；apply 可能是 null，见附录 B.3.2
```

几个细节值得单独记：

- **`config_manifest` 是清单 YAML 的全文字符串**，与 `PUT` 收的是同一份文档；其余
  字段与普通创建 API（`POST /openapi/v1/bots`）逐字段一致。
- **提交响应里没有 `state`**——刚提交按定义就是「等授权」；`iframe_url` 与
  `redirect_url` 平台只给其中一个，**取非空的那个**。
- **轮询响应的 `iframe_url` / `redirect_url` 只在 `AWAITING_AUTHORIZATION` 时非空**；
  `bot` 与 `apply` 只在 `READY` / `APPLY_FAILED` 时出现；`message` 用来说明
  `AUTHORIZATION_REJECTED` / `AUTHORIZATION_EXPIRED` / `CREATE_FAILED` 的原因。
- **两个引擎系都能用**。teclaw 走的是它自己的顺序（先写 bot 记录、对着记录 apply、
  再开容器，§7.1），所以状态流是 `CREATING → APPLYING → CREATING → READY`；
  `script` 在 teclaw 上照旧写入即拒。另外 `engine` 与 `cluster_name` 是绑定的：
  teclaw ⟺ `ANDC`，其余引擎 ⟺ `ACRA`，配错直接 400。
- **404 表示「这个 `bot_id` 上没有用清单创建过」**——包括用普通接口创建、事后
  `PUT` 清单的 bot。它的配置状态去 `last-apply` 看。

对照表：

| 动作 | 需要点授权？ |
| --- | --- |
| 用清单**创建** bot | **需要，每个 bot 一次**。固有 |
| 给**已有** bot `PUT` 清单 | 不需要 |
| 重新发布 / 重建时自动 apply | 不需要 |
| 已有 bot 扩容 | 不需要 |

### 4.6 `PUT` 之后会发生什么（已有 bot）

1. **先落库并校验。**接受一份文档从不依赖 bot 的运行时状态——bot 是 ACTIVE 还是
   别的状态，都不影响一份合法清单被接受。
2. **落库之后立刻启动一次 apply**（W8）——和 `POST …/config-manifest/apply` 启动的
   是同一种 apply，触发器记为 `put`。响应里多一个 `apply` 字段：`RUNNING` 带着
   可轮询的 `apply_id`；或者 `NOT_STARTED` 并说明原因——`apply_in_progress`（这个
   bot 上另一个 apply 还没结束，等它完成后再 `POST …/apply`）或 `not_started`。
   **无论哪种情况文档都已经存下了，响应都是 `200`。**
3. **被声明的类目按 §3.3 覆盖。不需要重启**，两个引擎系都不需要。
4. **`script` 是唯一的例外**，而且与其说是「要重启」不如说是「延后生效」：它现在
   就写下去，**下次启动时执行**。响应的 `warnings` 里会说清楚这件事。
5. **bot 还不是 ACTIVE 时**（比如刚创建、容器还没起来）：apply 照样启动；在 ARCA 系
   上需要容器的类目会被记成失败，`warnings` 会告诉你等 bot ACTIVE 之后调用哪个
   apply 接口再来一次。teclaw 平台管理路径（见 §7.1）不需要容器，所以没有这条提示。

> **永远不要为了让清单生效去重启 teclaw bot。**teclaw 重启会销毁容器、重新分配
> 失败会把 bot 打成坏状态并丢失容器内文件；平台自己也不会这么做。按上面的机制，
> 本来就不需要重启。

### 4.7 读懂 apply 报告

```json
{
  "apply_id": "ap_01H…", "bot_id": "bot7",
  "trigger": "explicit|put|create:pre_container|create:on_container",
  "started_at": "…", "finished_at": "…",
  "result": "SUCCEEDED|PARTIAL|FAILED",
  "sources": [
    {"name": "content", "ref": "v1.2.0", "resolved_sha": "9c1f4ae…"}
  ],
  "entries": [
    {"category": "identity", "name": "SOUL.md", "action": "updated", "from": "content"},
    {"category": "skills", "name": "order-lookup", "action": "created",
     "from": "artifacts", "source_digest": "sha256:3e7a…"},
    {"category": "skills", "name": "quality-check", "action": "failed",
     "from": "content", "error": "credential `corp-git-content` 被拒绝 (401)"}
  ]
}
```

| 看哪里 | 回答什么 |
| --- | --- |
| `sources[].resolved_sha` | **「这批 bot 线上跑的到底是哪一版内容」**——声明的 `ref` 与解析出的 commit 都记着 |
| `entries[].action` | 逐条：`created` / `updated` / `unchanged` / `skipped` / `failed` |
| `entries[].error` | 这一条为什么没成 |
| `result` | `RUNNING` 表示还在做；三个终态是从逐条结果推导出来的摘要，**给人看的**——没有任何东西读它然后据此行动 |
| `categories[].removed` | **覆盖删掉了什么。**它不在 `entries` 里，因为被删的东西根本没有对应的声明条目 |
| `categories[].aborted` / `partially_written` | 这个类目有没有收敛；**只有 `partially_written: true` 意味着「写了一半」**，需要再 apply 一次收敛。`aborted` 单独为真表示什么都没写、没有东西要回滚 |
| `trigger` | 这次 apply 是谁发起的：`explicit`（`POST …/apply`）、`put`（`PUT` 之后自动跟的那一次）、`create:pre_container` / `create:on_container`（用清单创建 bot 的两个阶段）、`dry_run`（预览报告，不落库）。**没有 `restart` / `republish`**——第一期它们不是 apply 点（§7） |

> **完整字段表**（含 `sources[]` / `categories[]` / `entries[]` 每个字段的类型与含义、
> 以及 `result` / `action` / `trigger` 的全部取值）见**附录 B.2.5** 与 **B.7**。

**`result` 的四个值**：`RUNNING` 是「还没做完」——apply 是启动式的，所以轮询时先
看到它；然后才是 `SUCCEEDED` / `PARTIAL` / `FAILED`。

**还有第五种情况：空串。**从没 apply 过的 bot（以及问一个不存在的 `apply_id`）读出的是
一份**空报告**，它的 `result` 与 `trigger` 都是 `""`。按 `result` 写分支时先判空，
细节见附录 B.2.5。

**`skipped` 的含义**：「因为所在类目被中止（§3.4）而没写」。它不再来自任何你能
声明的策略。同一个类目里，**把类目搞挂的那一条记 `failed`，其余无辜的记
`skipped`**——这样你一眼能看出该去改哪一行。

**`note` 字段**：给成功条目用的、你本来得自己推断的事实。今天只有一处：`script`
用它说明什么时候真正执行（见 §5.5）。

**两条要记住的边界**：

- **apply 记录的是「下发」，不是「执行」。**`script` 那一条记的是「已写入」，脚本
  在容器启动时才跑，退出码不进这份报告（去容器里的
  `/home/admin/logs/startup_script.log` 看）。teclaw 那边记的是「artifact 已递交」
  或「逐文件写已落地」，引擎去 apply 是引擎的契约。
- **apply 失败不改 bot 的状态、不阻断 bot 就绪。**失败的条目就是没下发的条目，
  bot 继续用着原有的东西。所以**「bot 是健康的」不等于「它的清单全生效了」**——
  后者只有 `last-apply` 能回答。
- **第一期是纯拉取的**：没有通知、没有告警。你想知道的时候自己来读。

### 4.8 日常：升一版内容

用了命名源之后，这就是全部动作：

```diff
 sources:
   content:
     git: https://code.example-corp.com/team/content.git
-    ref: v1.2.0
+    ref: v1.3.0
```

`PUT` 上去。**所有引用该源的条目在同一次 apply 内解析到同一个 commit**，并且这个
SHA 只拉取一次、全程复用。不会出现「identity 升了、skills 没跟上」。

> **原子的是「解析」，不是「下发」。**跨类目仍可能一个成一个败（§3.4：类目之间
> 互不牵连），于是不同类目短暂停在不同版本。真正原子的那一层是**逐类目的
> all-or-nothing**。

---

## 5. 逐类目怎么写

### 5.1 `identity` — 人设与规则文件

```yaml
identity:
  - type: SOUL.md                       # 每个 bot 一份，源内路径写字面量
    from: content
    subpath: bots/support-agent/soul.md
  - type: RULES.md                      # 全体共享一份
    from: content
    subpath: kb/service-rules.md
  - type: SAFETY.md                     # 一次性小片段可以内联
    content: |
      # 安全边界
      不承诺退款金额；涉及资损问题一律转人工。
```

- **`type` 是白名单枚举，选而不是造**：`RULES` / `OKR` / `SAFETY` / `SOUL` /
  `OUTPUT` / `MEMORY` / `IDENTITY` / `AGENTS` / `USER` / `TOOLS` / `HEARTBEAT` /
  `BOOTSTRAP` / `KNOWLEDGE` / `CLAUDE` / `GREETING` / `README`（物理文件为
  `<type>.md`）。**claude_code 引擎的 bot 只允许 `CLAUDE.md`**，写别的在 `PUT`
  时就报错，而不是 apply 时静默跳过。
- **`MEMORY.md` 与 `IDENTITY.md` 声明不了**：它们是引擎生成的运行期状态，被列入
  保留名单——apply 永不写、永不删。清单里写了会在 `PUT` 时被拒绝（否则你会得到
  一份「被接受但永远收敛不了」的文档）。
- **优先取源、少用内联。**内联内容游离于版本控制之外，久了会变成「藏在配置里的
  第二份人设」，排查「这句话哪来的」要看两个地方。
- apply 走的是现有 IdentityService，所以现有派生行为原样生效（例如
  RULES/OKR/SAFETY/OUTPUT 向 AGENTS.md 的同步）——你不需要知道这个机制存在。

### 5.2 `skills` — Local Skills

```yaml
skills:
  - name: quality-check          # 形态 A：仓库里的 skill 目录，免打包
    from: content
    subpath: skills/quality-check/
  - name: order-lookup           # 形态 B：制品库上的 zip
    from: artifacts
    subpath: skills/order-lookup-1.4.0.zip
    digest: "sha256:3e7a…"       # 非 git 形态：强制
```

- **`name` 是标识符，不含位置信息**——装到引擎哪个目录由引擎决定。
- **非 git 形态 `digest` 强制，没写就拒绝写入。**skill 里有会被 agent 加载执行的
  脚本，属于「代码」而不是「数据」：git 形态有 commit SHA 天然兜底，URL/制品库
  形态没有钉子就等于每个 apply 点盲取最新。
- **归档自动识别**：平台按内容类型/扩展名判断要不要解包，`unpack` 只在扩展名不可靠
  时作为显式覆盖。
- apply 走的是**正规的上传 + 激活**路径，所以装出来的 skill 与你手工上传的**无法
  区分**，也能在 skills-pool 的 reconcile 中正常存活。
- ⚠️ **声明了 `skills` 就意味着这个 bot 的 skill 集合由清单独占**：通过界面装的
  skill 会在 apply 时被移除。见 §8。

### 5.3 `resources` — workspace 资源

`path` 以 `/` 结尾就是**目录条目**，否则是**文件条目**。

```yaml
resources:
  # 文件
  - path: data/faq.csv           # 落点：workspace 相对
    from: content
    subpath: kb/faq.csv          # 源内路径
    on_fetch_failure: keep_last

  # 目录（git 源：免打包）
  - path: data/kb/
    from: content
    subpath: kb/

  # 目录（归档形态：源不在 git 时）
  - path: data/manuals/
    source: https://cms.example.com/kb/manuals.zip
    unpack: zip                  # zip | tar.gz
    strip_components: 1          # 可选，默认 0
    auth: cms-token
```

**两个「路径」不要混淆**：`path` 是**落点**（写到 workspace 哪里），`subpath` 是
**源内路径**（从源的哪里取）。一个条目里可以同时出现。

- **HTTP 没有目录语义**，所以「文件夹」要么用能枚举目录的协议（git，免打包），
  要么用归档整体运输。
- **`strip_components` 不做魔法**：只按你写的层数剥，**不会**自动探测单一顶层目录
  ——同一份声明的行为不取决于归档内部长什么样。业务习惯 `zip -r kb.zip kb/` 打出
  的那层壳目录，就用 `strip_components: 1` 消掉。
- **目录级所有权**：`path` 下整棵树归清单管辖，源里没有的文件在 apply 时被清除
  （包括手工添加的）。**`path` 之外完全不碰。**
- **嵌套禁止**：任何条目的 `path` 不得位于另一个目录条目之下（所有权无法定义），
  `PUT` 时拒绝。
- **权限被拍平**：归档里的可执行位不保留。要装可执行物，那是 `cli_tools` 的事
  （§5.6）。
- ⚠️ **替换有一个非原子窗口**：v1 的下发是「删掉整棵树、再逐文件写」。中途失败会
  让这棵树停在缺失或半写状态，该条目记 `failed`、报告里说明这棵树状态未知。**别把
  bot 运行期要写的目录声明成资源目录**。

### 5.4 `mcp` — MCP servers

```yaml
mcp:
  - server_code: mcp.ant.homistudio.meetmcp   # 就这一个字段
```

- **只接受平台 MCP 注册表引用**，不接受任意 URL。
- **一个条目只有 `server_code`。**早先的草案有一个可选的 `config`，已经删掉并在
  写入时按名拒绝：它被定义成「per-bot 配置，形状同现有 MCP config API」，而那个
  API 是**账号级**的（键 `(user_id, server_code)`，写入还会扇出到你名下所有
  bot），装的又正好是 `api_key` / headers 这类凭证。详见
  `manifest-schema.zh-CN.md` §3.1。
- **凭证永不进清单**：需要 `api_key` 的 server，配置照旧走现有统一配置存储——
  `GET`/`PUT /openapi/v1/bots/mcp/servers/{server_code}/config`，它本来就是账号级的。
  必需配置缺失时该条目记 `failed` 并给出明确错误。
- apply = 校验注册表存在 + 租户有权限（复用现有权限检查）→ **把这个 bot 的已启用
  server 集合收敛到声明**：声明了没启用的启用，启用了不再声明的**停用**（包括你
  在界面上手工开的），已经一致的记 `unchanged`。

### 5.5 `script` — 命令式长尾（仅 ARCA 系）

```yaml
script:
  body: |
    #!/bin/bash
    set -euo pipefail
    # 只有沙箱网络够得到的内部服务，平台侧 fetch 拿不到，只能在容器内取
    curl -fsSL http://inner-ops.example.com/whitelist/today.json \
      -o "$HOME/workspace/data/whitelist.json"
```

**什么时候才该用它**：声明式吸收不了的残留——沙箱内才可达的源、条件逻辑、动态
转换。绝大多数「取内容 → 装上」都应该走 manifest。

**约束（全部是 #935 的现状，本特性不改）**：

- 以 `admin` 身份执行、300s 超时、体积 ≤ 24 KiB；
- 输出只在容器内 `/home/admin/logs/startup_script.log`；
- **退出码不影响平台就绪判定**；
- **体内不得有密**——下发链路日志可见。

**两条你必须知道的能力/顺序规则**：

1. **teclaw 与 desktop 不支持，`PUT` 时直接拒绝**（fail closed），不是启动后静默
   不执行。建 bot 时就知道能不能用它。
2. ⚠️ **第一期：`script` 不得依赖同一份清单声明的任何内容。**脚本是被烤进启动
   命令的，而 identity / skills / resources 在容器**起来之后**才下发——所以**首启
   时脚本跑在它们之前**。别在脚本里假定 `data/kb/` 或某个 skill 已经存在。
   这条限制**只在第一期成立**，等所有类目能在启动前下发（#1508）时会被删除
   ——届时顺序反转，设计文档 §3.4 承诺的「脚本可以假定实体已就位」才成立。用
   清单创建 bot 时同样适用：那条路径上脚本也是烤进启动命令的。

**什么时候真的会执行**（这是最容易误解的一点）：apply **只负责把脚本写进
`ac_bot_startup_script` 那一行，绝不触发它执行**——不重启、不重新发布、不重建
payload。那一行会在这个 bot **下一次开设备**时被执行：创建、重启、重新发布。
`_build_create_bot_payload` 每次拼装 payload 都会重新读这一行，所以**后来改的
脚本不会丢**，只是要等下一次开设备。它**不会**在一个已经跑着的容器里被重新执行。
报告里那一条的 `note` 就是这么说的。

**老端点还能用，且不感知清单**：`GET/PUT/DELETE /openapi/v1/bots/{bot_id}/startup-script`
继续读写 `ac_bot_startup_script` 那一行，行为与今天逐字节一致——清单是上层，
启动脚本这一层不知道它的存在（W8 评审决定）。清单声明的 `script` 在 apply 时
物化进同一行，所以在**清单声明了 `script`** 的 bot 上，通过老端点改的脚本会被
下一次 apply 覆盖回清单声明的内容：清单是它所声明内容的真相源，要改就改清单。

### 5.6 `cli_tools` — 给模型调用的命令行工具

一个条目 = 一个命令 = 一个文件。平台替你取源、按 `digest` 验、（压缩包的话）
解包取出那一个文件、确认它是 x86-64 可执行文件、留一份自己的副本，然后让 bot
的引擎把它装上。

```yaml
cli_tools:
  - name: shopctl                 # 命令名；同一 bot 内唯一，不含路径分隔符
    source: https://artifacts.example-corp.com/tools/shopctl/2.3.0/shopctl-linux-amd64
    digest: "sha256:9f2c…"        # 本类目强制
    version: "2.3.0"              # 元数据；**不参与收敛**
    auth: oss-artifacts           # 私有制品库时写凭证名（§4.2）
```

要点：

- **交付单位是「一个自包含的可执行文件」。**压缩包只是传输形态——用 `unpack` +
  `subpath` 指出包内哪个文件是这个命令，平台取出它，**包内其余文件不下发**。一个
  包里两个命令就写两个条目。所以**需要同包辅助程序、或运行时要读同包 `lib/` 的
  工具用不了**，请打成静态二进制。（没有 `entrypoints` 字段。）
- **两种源形态**：直接指向一个二进制，或指向一个压缩包 + `subpath`。两种都必须
  带 `digest` —— 平台代你分发可执行物，供应链必须钉死。（`md5` 是平台物化之后
  自己算出来给引擎做变更判断的，不是你写的字段。）
- ⚠️ **来源只能写内联 `source` URL，不要用 `from` 引用命名源、也不要用 git 源。**
  这一条与 `resources` 那条不同、也更危险：`resources` 写了会在 `PUT` 当场被拒，而
  `cli_tools` 写了**能通过 `PUT`**，然后在 apply 时失败——物化器不解析命名源，会把
  `from` 的那个**源名当成 URL** 直接拿去取。见附录 C。
- **`digest` + `subpath` 才是收敛依据，`version` 不是。**只改 `version` 不会
  触发重新下发——否则改一个字符串就会重推一个可能 200 MiB 的二进制。
- **两个入口，一套实现**：清单里声明，或直接调管理 API——
  `POST` / `GET` / `DELETE /openapi/v1/bots/{bot_id}/cli-tools`（读 MEMBER、
  写 ADMIN，与清单本身同级）。**两条路走的是同一个组件**，所以同一份声明得到
  同样的拒绝理由。区别只有一处：清单 apply 是**全量覆盖**（不再声明的工具会被
  移除，包括你用 API 装的），单次 `POST` 不是（同名工具答 409，要换先删）。
- **两个家族上 `PUT` 都是立即生效的**，没有 `script` 那条「下次启动才执行」的例外
  （§7）：ARCA 走引擎的 CLI 端点装进运行中的容器，teclaw 由重新编排的 artifact
  承载。
- **CLI 工具不是 workspace 文件。**它由平台托管、有自己的元数据，**不会**出现在
  文件/资源列表里，也不能通过资源接口读写——只能经清单或上面那组 API 管理。
  （这不是加了个过滤：资源接口在结构上只寻址 `workspace/`，而 CLI 工具**根本不
  按路径寻址**。）

**v1 里 agent 怎么找到工具（请务必读完）**：工具落在哪由**引擎**决定，平台不知道
也不记录。v1 **不做 PATH 注入**——默认技能集里有一个 skill 告诉 agent 落点，agent
**以绝对路径调用**。代价是：

- 直接敲 `shopctl --help` **不工作**；
- 每次调用都依赖那个 skill 被读到；
- 一个脚本内部再去 shell 调同目录的另一个工具，**也找不到**。

把目录加进 PATH 是引擎侧的后续改动，届时你的清单、API 调用、已装的工具都不用动。

**用法认知仍然不归本类目**：安装只保证「这个 bot 有这个命令」。模型怎么知道有它、
参数怎么传，是内容问题——配一个教用法的 skill，或写 `TOOLS.md`
（见 §12 FAQ 的最后一条）。

### 5.7 暂不开放：`engine_config`

schema 已定稿（见 `manifest-schema.zh-CN.md` §3.4），但**第一期没有物化器**，
所以写了会在 `PUT` 时被拒绝。详情与解禁条件见**附录 C**。

---

## 6. 版本、钉扎与失败策略

### 6.1 三种 `ref` 写法

| 写法 | 语义 | 什么时候用 |
| --- | --- | --- |
| `ref: v1.2.0`（tag） | 每个 apply 点重新解析。**tag 被重打 = 声明的含义变了**，下次 apply 收敛到新内容 | **推荐**：发版式管理 |
| `ref: main`（branch） | 追最新 | 内部快速迭代的仓库 |
| `ref: 9c1f4ae…`（SHA） | 绝对不可变 | 要求严格可复现 |

**收敛单位是解析出的 commit SHA**，它就是 git 源的天然 digest——所以 git 源条目
上写 `digest` 是错误，`PUT` 会拒绝。

### 6.2 移动引用：`mode`

分支（以及会被重打的 tag）可能在一次没人把它跟配置变更联系起来的重启里解析出
不同内容。用源上的 `mode` 控制：

| `mode` | 行为 |
| --- | --- |
| `non_strict`（**默认**） | 应用新内容，并在 apply 报告里对该条目**告警**，写明前后两个 SHA |
| `strict` | 解析出的 SHA 与上次 apply 记录的不同时，该条目**失败**，bot 继续跑它现在跑的 |

- **写在源上**，不是按 bot、也不是按清单——要描述的性质是「这个 ref 允不允许在我
  脚下移动」，它属于持有 ref 的那个东西。一份清单里同时有一个钉死的外部依赖和一个
  快速变动的内部仓库是常态。
- **SHA 形式的 ref 忽略这个模式**（它动不了）——是「接受但无效」，不是报错。
- 拼错的取值会被拒绝，不会静默落到默认值。

### 6.3 `digest`：哪里强制、哪里非法

| 场景 | `digest` |
| --- | --- |
| git 源的任何条目 | **非法**（commit SHA 就是 digest），写了报错 |
| `skills` 的非 git 源 | **强制**——skill 是代码 |
| `cli_tools` 的非 git 源 | **强制**——平台代你分发可执行物 |
| `resources`、`identity` 的 URL 源 | 可选，是钉版手段 |

digest 不匹配按 fetch 失败处理，不是「损坏的成功」。

### 6.4 `on_fetch_failure`

条目级，**只有两个取值**：

| 值 | 行为 |
| --- | --- |
| `keep_last`（默认） | 用平台上一次为**这一条**成功物化的副本补全集合。源站抖动不影响 bot |
| `fail` | 该类目不写 |

> `skip` **已被删除**。在覆盖语义下它会意味着「把这一条删掉」——与字面相反。
> `keep_last` 与 `fail` 覆盖了原本会用到它的场景。

注意 `keep_last` 的边界：**首次** apply 就取不到的条目没有存量副本可回退，集合
补不全，于是该类目不写。首启遇到不稳定的源，那个类目交付的是「什么都没有」，而
不是「一部分」——这是这笔交易安全的那一端。

---

## 7. 生效时机

| 你做了什么 | 什么时候生效 | 要重启吗 |
| --- | --- | --- |
| `PUT` 清单（`identity` / `resources` / `skills` / `mcp`） | **立即**——`PUT` 自己就启动了一次 apply（§4.6） | 否 |
| `PUT` 清单里的 `script` | **立即下发，下次启动执行** | 否（但要等下次启动才跑） |
| `POST …/apply` | 立即 | 否 |
| 轮换凭证（重 `PUT` 同名） | 下一个 apply 点 | 否，且**不触发** apply |
| bot 创建 | 创建流程内部 apply，第一个容器就带着（两个引擎系都是） | —— |
| publish / republish | **不自动 apply**（第一期推迟，见下） | —— |
| 重建式 restart | **不自动 apply**（第一期推迟，见下） | —— |
| 扩容（scale-out） | **不重新 apply**——实例共享同一份平台状态，天然一致 | —— |

同一个 bot 的 apply 是**串行**的，所以显式 apply 撞上 `PUT` 启动的那一次也不会互相
踩——后来的那个会得到 `apply_in_progress`，等前一个结束再来。

**重新发布与重建式重启第一期不是 apply 点。**它们要解决的是「清单 `ref` 指向的
git 仓库有人推了新提交」这类事，第一期的答案是：显式 `POST …/apply` 一次，或者
再 `PUT` 一次清单。平台状态在两个引擎系上都是真相来源，容器重开只是把它再投一遍。

⚠️ **ARCA 系首启存在一个「bot 已 ACTIVE、但配置还在下发」的窗口**：第一期只有
`script` 在容器起来**之前**下发，其余类目在之后。用创建 API 的调用方会看到
`APPLYING` 状态，等到 `READY` 就跨过了这个窗口。teclaw 平台管理路径（§7.1）没有
这个窗口：所有类目在容器起来之前就已经在第一份 artifact 里了。

### 7.1 teclaw：第一份 artifact 就带着清单

teclaw 引擎不是「容器起来再逐文件写」，而是**靠一份 artifact 启动**——整包配置的
清单，引擎自己按引用去对象存储拉文件。所以在 teclaw 上，清单的 apply 是把每个
类目**物化进平台状态**：`mcp` 与 `skills` 进数据库；`identity`、`resources` 与本地
skill 的包文件进平台自己的托管副本（bot-data 对象存储 + 一张索引表）。artifact 由
平台状态组装，天然就带着清单的结果，并附一个 `ownership` 映射告诉引擎这份
artifact 由谁断言——**跟着操作走**：apply 结束时的整包重投、以及带清单的 bot 的
第一份 artifact，所有类目都是 `platform`（列表就是完整期望状态，空列表 = 区域
清空）；上传 skill、上传资源、改 MCP 等其他操作触发的组装，所有类目都是 `engine`
（引擎自己的状态是真相；`mcp` 除外，它任何时候都是 `platform`）。

- **创建**：先只写 bot 记录，对着它跑那唯一一个 apply 阶段，再开容器——容器拿到的
  **第一份** artifact 已经是清单的结果。轮询状态会从 `CREATING` → `APPLYING` →
  `CREATING` → `READY`，报告只有这一个阶段。
- **运行中 `PUT`**：apply 写平台状态，最后把整包 artifact 重投一次给运行中的容器
  （不是逐文件写）；重投失败不会让 apply 失败，会记在报告的 `notes` 里，
  再 `POST …/apply` 一次即可。
- **本地 skill**：清单装的 skill 在 artifact 的 `skills` 里是一条 `scope: "user"` 且带
  `store`/`path` 的引用，指向包目录，引擎按前缀拉整个包；当清单同时声明了
  `resources`（于是 `resources` 也由平台断言）时，包里的每个文件还会作为 `resources`
  引用出现。被清单删掉的 skill 不再出现在 artifact 里（文件留在平台副本，与 ARCA 上
  停用一个 skill 时文件留在主机一致）。

**这条路径由部署开关 `user_config.bot_config_manifest.teclaw_platform_managed`
控制，默认关闭。**关闭时 teclaw 与 W8 之前一样：容器起来之后逐文件写，artifact 里
只多一个全为 `engine` 的 `ownership` 映射。开关要等 teclaw 引擎实现 `ownership`
语义（引擎收敛契约 §9）之后再翻开；**翻开之前，先对每个已有清单的 teclaw bot 显式
apply 一次**，让平台副本落上文件——否则第一份带 `platform` 断言的 artifact 会把声明
了却为空的类目当成「清空」。

---

## 8. 你会失去什么（刻意接受的代价）

这些是清单换来一致性所付的账，写在这里而不是让你在生产上撞见：

1. **通过界面安装的 skill 会被移除**——当一份声明了 `skills` 的清单被应用时。
   对一个被声明的类目，清单是唯一的所有者：在那个类目上，**清单与界面互斥**。
2. **被声明类目内、由 bot 自己创建的文件会被移除**，除非它在保留名单里。
   保留名单**只有两个名字**，永不被 apply 写入、也永不被删除，无论清单声明与否：

   ```text
   MEMORY.md
   IDENTITY.md
   ```

   它们是引擎生成的运行期状态。名单是有限、可枚举的——正因为如此，两个引擎系才
   能对它达成同一份契约。
3. **首启有一段 ACTIVE-but-unconfigured 窗口**（§7），期间启动的扩容实例会看到
   只下发了一部分的内容。（之后实例不会发散：它们共享同一份挂载。）
4. **首启时 `script` 跑在其他类目之前**（§5.5）。
5. **目录条目的替换不是原子的**（§5.3）。

前两条的代价来自「不做三方 diff」这个决定；换来的是**两个引擎系一套语义**——
teclaw 收下整包 artifact 并替换，ARCA 现在对被声明的类目做同一件事，同一份清单
在两边行为一致。

---

## 9. 排错手册

### 9.1 `PUT` 被拒绝

`PUT` 是 all-or-nothing 的，响应会**指名违规条目**。**用清单创建 bot 时同一套
校验发生在 preflight**——在申请 Passport 之前，所以你不会点完授权才被告知清单
写错了；下面这张表对两条路径都适用。常见原因：

| 报错指向 | 原因 | 怎么修 |
| --- | --- | --- |
| 同一条目上有多个来源 | `from` / `source` / `content` 互斥 | 只留一个 |
| `from` 指向未声明的源 | 源名拼错，或 `sources` 里没写 | 对齐名字 |
| `source` URL 带 userinfo | `https://user:token@host/…` 形式的内联 token | 删掉，改用凭证引用（§4.2） |
| git 源上写了 `digest` | commit SHA 就是 digest | 删掉 `digest` |
| 用了 `from` 的条目上写了 `auth` | 凭证声明在**源**上 | 把 `auth` 移到 `sources.<name>` |
| `content` 条目上写了 `auth`/`digest`/`on_fetch_failure` | 内联条目没有 fetch 环节 | 删掉这些字段 |
| 未知的 `${…}` 占位符 | 多半是写了 `${OCB_*}`，或写了并不存在的 `${BOT_ID}` | 只有 §4.3 那四个可用；按 bot 区分请写字面量 |
| `on_fetch_failure: skip` | 该取值已删除 | 改 `keep_last` 或 `fail` |
| 未知的 `mode` 取值 | 拼错 | 只有 `strict` / `non_strict` |
| `identity.type` 非法 | 不在白名单，或 bot 是 claude_code 引擎（只允许 `CLAUDE.md`） | 见 §5.1 |
| `identity.type` 是 `MEMORY.md` / `IDENTITY.md` | 保留名单 | 删掉这条 |
| `resources.path` 绝对路径或含 `../` | 路径穿越 | 改成 workspace 相对路径 |
| `resources` 条目嵌套在另一个目录条目之下 | 所有权无法定义 | 拆开或收缩目录条目 |
| `skills` 非 git 源缺 `digest` | 该形态强制 | 补 `digest` |
| `apply_once` | v1 保留字 | 删掉 |
| 某个类目 `unsupported` | 引擎不支持（teclaw / desktop 的 `script`），或还没开放（`engine_config`，附录 C） | 先 `GET …/config-manifest/capabilities` |
| `resources` 条目上写了 `from` 或 git 源 | 这个组合还没接通（附录 C），拒绝理由里会点名类目 | 改成内联 `source` URL 或 `content` |
| 超限 | 文档大小 / 条目数 / 内联大小（§10） | 拆分或改走取源 |

### 9.2 条目在报告里是 `failed`

| `error` 长什么样 | 原因 | 怎么修 |
| --- | --- | --- |
| **「凭证 `<name>` 被拒绝」**（401/403） | **令牌过期或权限不够**——这条错误被刻意与网络错误区分开，就是为了让你知道该去轮换 | 重 `PUT` 同名凭证（§4.2）。别去查网络 |
| 目标不在 `allowed_prefixes` 内 | 源被改指到授权范围之外，或前缀写窄了 | 核对前缀；注意路径段边界与端口 |
| 跨前缀重定向 | 源站把请求重定向出了授权范围 | 直接失败，不会剥离凭证继续；改源或加前缀 |
| digest 不匹配 | 制品被覆盖重发，或 URL 指到了别的东西（比如一个 404 的 HTML 页） | 重算 digest，或钉到不可变路径 |
| 目标地址被拒绝 | SSRF 防护：环回 / 链路本地 / 元数据段 / 内网地址 | 源必须是**平台侧可达的公网 https**。只有沙箱内可达的源属于 `script` 的领域 |
| 超时 / 超大 | 单条 60s、单次 apply 300s，以及 §10 的体积上限 | 减小体量或拆条目 |
| `credential X 不存在` | 凭证被删了，或名字拼错 | 重新注册 |
| MCP：`server X 需要先配置 api_key` | 必需配置缺失 | 去统一配置里补，凭证不进清单 |

### 9.3 「一条都没失败，但整个类目没变」

看是不是同类目里另有一条 `failed`——**类目是 all-or-nothing 的**（§3.4）。修好
那一条，整个类目才会被写入。

### 9.4 「报告说成功了，但 bot 行为没变」

按这个顺序查：

1. **是 `script` 吗？**它是立即下发、**下次启动执行**（§7）。
2. **是 teclaw 吗？**apply 记录到「artifact 递交/逐文件写落地」为止，引擎侧应用是
   下一层。
3. **是不是被手工改回去了？**清单管辖的实体每个 apply 点重新收敛（§3.2），反过来
   也一样：你在界面上的改动会被覆盖。
4. **`unchanged` 是正常的**——收敛就是「已经对了就不动」。

### 9.5 `script` 没执行

| 现象 | 原因 |
| --- | --- |
| `PUT` 时就被拒 | teclaw / desktop 不支持（§5.5） |
| 脚本跑了但找不到 skill / 资源文件 | **首启顺序**：脚本先于其他类目（§5.5 第 2 条） |
| 不知道跑没跑 | 看容器内 `/home/admin/logs/startup_script.log`；apply 报告只记「已写入」 |
| 脚本失败但 bot 照样起来了 | 设计如此：退出码不影响就绪判定 |

### 9.6 创建卡在 `AWAITING_AUTHORIZATION` / apply 一直不动

**先查这一条，它不是清单的问题。**apply 现在跑在平台的任务队列上，用清单创建
bot 也是队列上的一个任务。所以部署里必须满足两个前提：

- `task_queue_worker.enabled=true`；
- `ac_task_queue` 表已经开好。

**任一条不满足，创建不是变慢，而是永远不会完成**：提交会照常返回 `202`，轮询会
一直停在 `AWAITING_AUTHORIZATION`（或授权后停在 `CREATING`），直到窗口超时被判为
`AUTHORIZATION_EXPIRED`。`PUT` 之后的 apply 同理：`POST …/apply` 照常给你
`apply_id`，报告则永远停在 `RUNNING`，直到 apply 锁的 TTL 到期。

这在过去只是一个「优化开关」，从本期开始它决定功能是否可用——排查「创建卡住」时
第一个要确认的就是它。

### 9.7 内容更新了，但 bot 还是旧的

- 用的是 **tag 且没动**？那就是没变——改 `ref`（§4.8）。
- 用的是 **branch 且 `mode: strict`**？SHA 变了会让该条目**失败**，这是你要的钉扎
  语义。看报告里的前后 SHA。
- 取源失败并落到了 **`keep_last`**？报告里那一条会写明。

---

## 10. 限额

| 项 | 上限 |
| --- | --- |
| 清单文档总大小 | 64 KiB（`script` 另按 24 KiB） |
| 每类目条目数 | 50 |
| 内联 `content` 单条 | 64 KiB |
| 单条目取源 | skills zip 100 MiB；resources 文件 100 MiB；identity 1 MiB |
| resources 目录条目 | 单归档 200 MiB；解包后 500 MiB；单归档文件数 5000 |
| 单次 apply 取源总量 | 500 MiB（目录条目按解包后算） |
| 超时 | 单条 60s；单次 apply 总预算 300s |

`PUT` 时能查的（文档大小、条目数、内联大小）当场拒绝；只能在取源时发现的（远端
内容大小）按 `on_fetch_failure` 处理并记进报告。

---

## 11. 安全须知

1. **secret 不进清单、不进 script、不进 URL。**清单会被原样读回并进审计；script
   的下发链路日志可见。私有源一律走凭证引用。
2. **凭证是租户级的**：一次注册、整批 bot 复用。一个凭证只装一个 secret——一个
   凭证装多个 secret 会把轮换周期、权限边界、可出示范围全糊在一起。
3. **`allowed_prefixes` 收到仓库/桶前缀**，别图省事写整个域名。它防的是「有清单
   编辑权的人把 `source` 指向同域名下别人的仓库来套取你的 token」。
4. **不要用个人 PAT**，也不要用带写权限的 API 令牌（§4.2）。
5. **secret 加密落库、写后不可读回**；日志、报告、错误信息里只出现凭证名。生产
   环境解析不到主密钥时，凭证写入会被**拒绝**，绝不明文落库。
6. **平台代你 fetch 的范围是受限的**：仅 https、拒绝内网与元数据地址、逐跳校验
   重定向、限大小限时间；取回的字节只被写入或哈希，**绝不执行**。
7. **归档解包会拒绝**路径穿越、绝对路径成员、逃逸的符号/硬链接、设备特殊文件，
   并**抹平可执行位**。
8. **凭证零引擎面**：fetch 全在平台侧完成，凭证不下发容器、不进 artifact。

---

## 12. FAQ

**Q：我还没有 bot，怎么知道支持什么？**
看 §2.1 的静态表——能力只由「引擎类型 + bot 类型」决定，不需要先有 bot。
`GET …/capabilities` 是给已有 bot 的便利接口，答案与那张表同源。另外，用清单
创建 bot 时，清单在**申请授权之前**就被校验，所以写错不会浪费一次授权。

**Q：清单和界面能混用吗？**
能，但边界是类目：**声明了的类目由清单独占**（界面改动会被覆盖、界面装的会被
移除），**没声明的类目完全归你手工管**。别在同一个类目上两边都动。

**Q：`DELETE` 清单会把装上的东西删掉吗？**
不会。删除声明 ≠ 删除资产——那些实体留在 bot 上，只是不再有人管它。要清空某个
类目，用 `skills: []` 这种空集合声明（§3.3）。

**Q：怎么知道一批 bot 线上跑的是哪一版内容？**
`GET …/last-apply` 的顶层 `sources[]`：声明的 `ref` 与解析出的 `resolved_sha`
都在。

**Q：源站挂了，bot 还能起来吗？**
能。默认 `keep_last` 用上次成功物化的副本；失败的条目就是没下发的条目，bot 继续
用着原有的东西。apply 失败不阻断 bot 就绪。

**Q：teclaw 上能用吗？**
manifest 全部类目都能用，且**第一份 artifact 就带着清单的结果**。只有 `script`
不支持，写入时就会被拒。

**Q：一份清单能同时管一批 bot 吗？**
v1 是 bot 级的：同一份文档 `PUT` 到多个 bot 上。模板级清单（一份声明服务多个
bot）是后续方向；注意它对**已有** bot 成立，而**批量创建** N 个 bot 仍然是 N 次
授权点击（§4.5）。

**Q：我的内容只有沙箱网络能访问怎么办？**
那属于 `script` 的领域（且 teclaw 上不可用）。清单的源必须**平台侧可达**——换来
的是统一的取源防护与统一的新鲜度语义。

**Q：模型怎么知道我装的 skill/工具存在？**
skill 会进入 bot 的技能集，模型自然可见。私有 CLI 工具则**不在模型的先验里**
——它不会凭空敲一个没见过的命令名，所以工具的「存在性」必须靠上下文注入：配一个
教用法的 skill，或写 `TOOLS.md`（合法 identity 类型）。

**Q：配置失败了会通知我吗？**
不会。第一期是纯拉取的——没有通知、没有告警。所以**把 `last-apply` 接进你自己的
巡检**是推荐做法。

---

## 附录 A：完整清单示例

一个「商家客服 bot」的完整声明，内容在公司 git、制品在 OSS：

```yaml
schema_version: 1

sources:
  content:                                   # 内容仓库（git）
    git: https://code.example-corp.com/team/content.git
    ref: v1.2.0                              # ← 整套配置升版本只改这一行
    auth: corp-git-content
    mode: non_strict
  artifacts:                                 # 制品桶（URL 前缀）
    url: https://artifacts.example-corp.com/tools/
    auth: oss-artifacts

manifest:
  identity:
    - type: SOUL.md
      from: content
      subpath: bots/support-agent/soul.md
    - type: RULES.md
      from: content
      subpath: kb/service-rules.md

  resources:
    - path: data/faq.csv
      from: content
      subpath: kb/faq.csv
      on_fetch_failure: keep_last
    - path: data/kb/
      from: content
      subpath: kb/
      on_fetch_failure: keep_last

  skills:
    - name: quality-check
      from: content
      subpath: skills/quality-check/
    - name: order-lookup
      from: artifacts
      subpath: skills/order-lookup-1.4.0.zip
      digest: "sha256:3e7a…"

  mcp:
    - server_code: mcp.ant.homistudio.meetmcp

script:                                      # 仅 ARCA 系；不得依赖上面任何一项
  body: |
    #!/bin/bash
    set -euo pipefail
    curl -fsSL http://inner-ops.example.com/whitelist/today.json \
      -o "$HOME/workspace/data/whitelist.json"
```

配套的两次凭证注册（一次性）：

```text
PUT /openapi/v1/bots/source-credentials/corp-git-content
{
  "type": "header",
  "header_name": "Authorization",
  "secret": "Basic <base64('git:<访问令牌>')>",
  "allowed_prefixes": ["https://code.example-corp.com/team/content"]
}

PUT /openapi/v1/bots/source-credentials/oss-artifacts
{
  "type": "header",
  "header_name": "Authorization",
  "secret": "Bearer …",
  "allowed_prefixes": ["https://artifacts.example-corp.com/tools/"]
}
```

---

## 附录 B：API 参考

本特性开放的全部端点，逐个列出**请求要填什么**、**响应回什么**、以及每个枚举
字段的取值。共用的对象（信封、`Bot`、apply 报告）只在 B.0 与 B.2.5 定义一次，
后面引用。

- [B.0 公共约定](#b0-公共约定)
- [B.1 清单本体](#b1-清单本体)
- [B.2 apply](#b2-apply)
- [B.3 用清单创建 bot](#b3-用清单创建-bot)
- [B.4 源凭证](#b4-源凭证)
- [B.5 CLI 工具](#b5-cli-工具)
- [B.6 同一份状态的「另一扇门」](#b6-同一份状态的另一扇门)
- [B.7 枚举速查](#b7-枚举速查)

### B.0 公共约定

**每个响应都包在同一个信封里**，成功失败都是这个形状：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `code` | int | 6 位：前 3 位是 HTTP 状态码，后 3 位是业务子码。`200000` = OK |
| `message` | string | 人读的状态说明，**恒为英文**（如 `"OK"`） |
| `data` | object \| array \| null | 载荷。空结果时可能是 `null`。**出错时通常是 `null`，但有两个例外**，见下 |
| `request_id` | string | 链路 id，与响应头 `X-Trace-Id` 一致。报障时带上它 |

下文每个端点的「响应」一节说的都是 `data` 里的字段。

**出错时 `data` 不总是 `null`。**绝大多数失败只有固定的 `message`、`data` 为 `null`；
但有两类失败带着**结构化的、你需要它才能改对**的载荷，别用「出错就丢掉 data」的通用
处理把它们扔了：

| 失败 | `data` 里是什么 |
| --- | --- |
| 清单校验失败（`422`） | `{ "violations": [ {location, code, message}, … ] }`——逐条告诉你哪里不合法（B.1.2） |
| 空间 bot 配额已满（`409`） | `{ space_id, space_name, space_type, ceiling, used }`（B.3.1） |

### B.0.1 每个请求都要带的参数

⚠️ **这一条最容易踩：`user_id` 是必填查询参数。**漏了它不是「按默认身份处理」，而是
**`422`**（FastAPI 的参数校验失败）。下文各端点的「请求」一节只列它们**额外**的参数，
`user_id` / `owner_id` 不再重复。

**它落在哪些端点上**：B.1 清单本体、B.2 apply、B.3 用清单创建 bot、B.5 CLI 工具——也就是
所有针对某个 bot（或要创建一个 bot）的操作。**唯一的例外是 B.4 源凭证**：那一组是
**租户级**的，路径虽然也在 `/openapi/v1/bots/` 下面，但它的路由只认调用方身份本身，
**不接受也不需要 `user_id` / `owner_id`**——写不写都不影响，写的权属由**调用方应用**决定
（B.4）。

| 参数 | 位置 | 必填 | 含义 |
| --- | --- | --- | --- |
| `bot_id` | 路径 | ✅（`/bots/{bot_id}/…` 的端点） | 就是 `Bot.bot_id`（形如 `20260813_a7k2m9p1`），原样传，不做加工 |
| `user_id` | 查询 | ✅ | 这次请求**代表哪个终端用户**。非空。它指向别人时答 `403`；**完全不传时答 `422`**。应用调用方传它所代表的那个用户 |
| `owner_id` | 查询 | ❌ | 要操作的 bot**属于谁**。默认是调用方自己，**只有在操作别人分享给你的 bot 时才需要写**。写了它而你既不是属主也不是协作者 → 按 B.0 的规则答 `404`（与「没有这个 bot」不可区分）。清单本体、apply、CLI 工具这几组协作者可用的端点都接受它 |

`user_id` 与 `owner_id` 的区别就是「**谁在调**」与「**调谁的 bot**」：在你自己的 bot 上
两者相同、`owner_id` 可以不写；在别人分享给你的 bot 上，`user_id` 是你、`owner_id` 是
那个 bot 的属主。

**权限位**：`MEMBER` / `ADMIN` / `OWNER` 是调用方在这个 bot 上的协作者等级，**OWNER
恒通过**。「编辑锁」表示该操作**还**要求持有这个 bot 的编辑锁——**只在有协作者的 bot 上
生效**，没拿到锁答 `423`。

⚠️ **等级不够答的是 `404 Not found`，不是 `403`**，而且与「这个 bot 不存在」**逐字节
相同**（同样的状态码、同样的 message、同样的信封）。这是故意的：如果调用方能把「我权限
不够」和「没有这个 bot」区分开，那就等于拿到了一个枚举租户内全部 bot id 的探针。所以
**不要把 `404` 当成「bot 没了」**——在这些端点上它同时意味着「你不够格」。

这条路径下的 `403` 是**另一回事**，只有一个含义：`user_id` 指向了这个调用方无权代表的
用户。三种拒绝各归各的：

| 状态 | 含义 |
| --- | --- |
| `403` | `user_id` 指向了调用方无权代表的用户 |
| `404` | 这个 bot 不存在**，或者**调用方的协作者等级不够——两者不可区分 |
| `423` | 等级够了，但没持有编辑锁（只发生在有协作者的 bot 上） |

**`DELETE` 类端点的 `data`**：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `deleted` | bool | **恒为 `true`**。删除失败回的是错误信封，永远不会是 `deleted: false` |

**`Bot` 对象**（`GET …/with-manifest/status` 在终态返回它）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `bot_id` | string | bot 唯一标识 |
| `bot_name` | string | 展示名，租户内唯一 |
| `bot_desc` | string | 描述，可能为空串 |
| `engine` | string | 引擎，创建时固定不可改 |
| `cluster_name` | enum | `ACRA` / `ANDC`，见 B.7 |
| `bot_type` | string | `personal` / `service` / `desktop`（读侧包含 `desktop`，它不由本 API 创建） |
| `status` | string | 生命周期状态，**开放集合**，见 B.7 |
| `owner_entity_id` | string | 属主用户，回显请求里的 `user_id` |
| `template_type` | string \| null | 模板类型（如 `applicationCoding`）；无模板时 `null` |
| `template_config` | object \| null | 模板快照，**逐字段原样存的创建入参**，可能含创建者填进去的敏感值（如 `token`）——按敏感数据对待 |
| `space` | object \| null | 所属业务空间：`{ space_id, name, kind }`；只有列表类端点会填 |

**错误**：每个端点的错误在各自小节列出。跨端点共有的两条就是上面那张表里的 `403`
与 `404`；下文各端点只列它们**额外**的失败。

---

### B.1 清单本体

#### B.1.1 `GET /openapi/v1/bots/{bot_id}/config-manifest`

读整份清单。**权限：MEMBER。**

**请求**：除 B.0.1 的公共参数（`bot_id` + 必填 `user_id` + 可选 `owner_id`）外，没有 body、没有别的查询参数。

**响应 `data`**：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `bot_id` | string | 这份清单属于哪个 bot |
| `document` | string | 清单原文，**逐字节原样**（`script` 的引号与空白都保留）。**没存过清单时是空串，不是 404** |
| `size_bytes` | int | 存下来的文档字节数；没存过是 `0` |
| `schema_version` | int \| null | 文档声明的版本；**只有在没存过清单时才是 `null`** |
| `updated_by` | string | 最后一次写入者。应用代用户写入时是 `app:<应用 id>:on-behalf-of:<用户 id>`；没存过是空串 |
| `updated_at` | datetime \| null | 最后一次写入时间；**只有在没存过清单时才是 `null`** |
| `warnings` | string[] | **读的时候恒为空数组**，只有 `PUT` 会填（见 B.1.2） |
| `apply` | object \| null | **读的时候恒为 `null`**，只有 `PUT` 会填（见 B.1.2） |

#### B.1.2 `PUT /openapi/v1/bots/{bot_id}/config-manifest`

整体替换清单，接受之后**顺手启动一次 apply**。**权限：ADMIN。**

**请求 body**（不认识的键会被拒绝，不是被忽略）：

| 字段 | 必填 | 类型 | 含义 |
| --- | --- | --- | --- |
| `document` | ✅ | string | 清单文档全文（YAML）。**原样存、原样读回**。校验规则见 §5–§6 与附录 E |

**响应 `data`**：与 B.1.1 同样的字段，但 `warnings` 与 `apply` 这次会填：

`apply` 对象——`PUT` 启动的那次 apply 的**启动结果**（不是报告）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `apply_id` | string | 拿去轮询的 id（`GET …/config-manifest/applies/{apply_id}`）。**没启动起来时是空串** |
| `result` | enum | `RUNNING`（已启动）或 `NOT_STARTED`（没启动） |
| `reason` | string \| null | 只在 `NOT_STARTED` 时有值：`apply_in_progress`（这个 bot 上还有 apply 没结束，等它完了再 `POST …/apply`）或 `not_started`（其他原因没能启动） |

`warnings`（`string[]`，人读的提示，不致命）今天有三类来源：

1. 校验器自己的提醒——例如 `sources` 里声明了却没有任何条目引用的源；
2. 文档声明了 `script`：**现在就写下去，下次启动才执行**；
3. bot 当前不是 `ACTIVE`：需要容器的类目会被记成失败，等 ACTIVE 之后再 apply 一次。
   （teclaw 的平台管理路径不需要容器，所以没有第 3 条。）

> **无论 `apply` 是 `RUNNING` 还是 `NOT_STARTED`，文档都已经存下了，响应都是 `200`。**

**错误**：

| 状态 | 什么时候 | `data` |
| --- | --- | --- |
| `404` | bot 不存在，**或**协作者等级不到 ADMIN（不可区分，见 B.0） | `null` |
| `413` | 文档超出大小上限（§10） | `null` |
| `422` | 文档不合法或有不受支持的构造 | `{ "violations": [ … ] }`，**一次列全**（见下） |

`violations[]` 每一条的形状——三个字段都是公开契约：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `location` | string | 指进**你提交的那份文档**的路径，如 `manifest.identity[1].type`、`sources.content.mode`。照着它就能把光标放到那一行 |
| `code` | string | 稳定的机器可读原因，`snake_case`（如 `multiple_sources`、`missing_digest`、`unsupported_source`）。**要分支就读它**——`message` 是会被改写的散文 |
| `message` | string | 人读的解释，会点出规则、必要时点出值 |

> 一次校验会把**所有能跑的规则都跑完**再一起回答，所以修文档是一遍过，而不是
> 「修一条、再提交、再学下一条」。但如果文档顶层就解析不了，后面的规则没有东西
> 可检查，那次的答案就只有一条解析错误——**列表是「我们能判定的全部」，不保证
> 第二次提交能通过的文档在第一次就被完整描述过**。

#### B.1.3 `DELETE /openapi/v1/bots/{bot_id}/config-manifest`

清除声明。幂等。**权限：ADMIN。**

**请求**：只有 B.0.1 的公共参数。**响应 `data`**：`{ "deleted": true }`。

> **不删除任何已装上的实体**——清掉的是声明，不是它装出来的东西。要清空某个类目，
> 用 `skills: []` 这种空集合声明（§3.3）。

#### B.1.4 `GET /openapi/v1/bots/{bot_id}/config-manifest/capabilities`

这个 bot 接受哪些构造。**权限：MEMBER。**

**请求**：只有 B.0.1 的公共参数。

**响应 `data`**：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `bot_id` | string | 这张表描述的 bot |
| `engine_type` | string | 结论是按哪个引擎算的 |
| `bot_type` | string | 结论是按哪个 bot 类型算的 |
| `schema_versions` | int[] | 本部署接受的 `schema_version` 取值，今天是 `[1]` |
| `constructs` | object[] | 每个构造一行，**支持与不支持的都在**，见下 |

`constructs[]` 每一行：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `kind` | enum | `category`（`manifest` 下的六个类目）/ `section`（顶层非类目段，今天只有 `script`）/ `source`（条目怎么指定来源） |
| `name` | string | 该 `kind` 内的名字。取值见 B.7 |
| `supported` | bool | 能不能写 |
| `reason` | string | 不支持时说明原因；**支持时是空串** |

> 这张表与 `PUT` 的拒绝**是同一个函数算的**，所以不会出现「这里说支持、`PUT` 却
> 拒绝」。唯一它答不了的是「类目 × 源形态」的组合（`resources` 不能用 `from`/git，
> 附录 C）——那由条目校验逐条拒绝，`reason` 里会点名类目。
>
> 不认识的引擎**什么都不支持**，每一行的 `reason` 都会这么说。

---

### B.2 apply

#### B.2.1 `POST /openapi/v1/bots/{bot_id}/config-manifest/apply`

把存下来的清单应用一次。**权限：OWNER；有协作者的 bot 上还要持有编辑锁（否则 `423`）。
状态码 `202`。**

**请求**：路径参数 `bot_id`；查询参数 `dry_run`（见 B.2.2，默认 `false`）；**没有 body**
——要应用的文档就是已经存下来的那份，这个端点不收清单。

**响应 `data`**（`202`，这是一个**句柄，不是报告**）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `apply_id` | string | 拿去轮询：`GET …/config-manifest/applies/{apply_id}` |
| `result` | enum | **恒为 `RUNNING`**——活还没干，这里不可能有结果 |

**错误**：

| 状态 | 什么时候 |
| --- | --- |
| `404` | bot 不存在，**或**调用方不是 OWNER（不可区分，见 B.0） |
| `409` | 这个 bot 上已经有一次 apply 在跑 |
| `422` | 存下来的文档对**现在的**这个 bot 已经不合法（比如引擎换了） |
| `423` | **有协作者的 bot 上，调用方没有持有它的编辑锁** |

> 这两个都是**在发 id 之前**回答的。**你永远不会拿到一个「其实没跑起来」的 id。**
>
> 没存过清单的 bot：apply 什么都不做、报告里什么都没有——**这不是错误**。

#### B.2.2 `POST …/config-manifest/apply?dry_run=true`

只算计划、不动手。**权限同 B.2.1（OWNER，有协作者时还要编辑锁）。状态码 `200`
——同步，不需要轮询。**

**请求**：

| 查询参数 | 必填 | 类型 | 含义 |
| --- | --- | --- | --- |
| `dry_run` | ❌ | bool，默认 `false` | `true` = 返回计划而不执行。**不写 apply 记录**，所以不发 `apply_id`、不进 `last-apply`、不出现在历史里。返回的那份报告里 **`trigger` 是 `dry_run`**、`apply_id` 是空串 |

**响应 `data`**：一份 **apply 报告对象**（B.2.5），只是它描述的是「会发生什么」。

> **不改 bot 的任何配置**，但有两件事照做，都是有意的：声明的源**可能真的被取一次**
> （一个说不出「你的源可不可达、digest 对不对得上」的预览是弱预览），取回的字节按
> 平台自己的副本留存——那条审计记的是「取得」，不是「下发」；读 bot 的 MCP 集合会
> 顺带把平台安装行与 SkillSet 成员对一次账，任何一次读这份状态都会这么做。

#### B.2.3 `GET /openapi/v1/bots/{bot_id}/config-manifest/applies/{apply_id}`

轮询某一次 apply。**权限：MEMBER。**

**请求**：

| 路径参数 | 类型 | 含义 |
| --- | --- | --- |
| `bot_id` | string | 这次 apply 属于哪个 bot |
| `apply_id` | string | `POST …/apply` 返回的那个 id |

**响应 `data`**：apply 报告对象（B.2.5），跑着的和跑完的都读得到。

> `apply_id` 属于**别的 bot**、或者根本不存在时，这里返回的是**空报告**（`result` 与
> `trigger` 为空串，见 B.2.5 开头那条），**不是 `404`**——它是轮询句柄，不是访问凭据，
> 所以问一个不属于你的 id 得到的是「没有这条记录」而不是「有，但不给你」。

#### B.2.4 `GET /openapi/v1/bots/{bot_id}/config-manifest/last-apply`

这个 bot 最近的一次 apply。**权限：MEMBER。**这是「**我的清单生效了吗**」的权威答案。

**请求**：只有 B.0.1 的公共参数。**响应 `data`**：apply 报告对象（B.2.5）。

> 从没 apply 过的 bot 读出的是**空报告**（`apply_id` / `result` / `trigger` 都是空串、
> `started_at` 与 `finished_at` 都是 `null`、四个数组为空），**不是错误**——和「没有清单
> 的 bot 读出空文档」是同一条规则。判空看 `result == ""`，别去判 `apply_id`，更不要拿
> `finished_at == null` 判「还在跑」。

#### B.2.5 apply 报告对象

B.2.2 / B.2.3 / B.2.4 与 `GET …/with-manifest/status` 的 `apply` 字段都是这个形状。

> ⚠️ **先看这条，它影响你怎么写分支。**「空报告」是一个正常响应，不是错误——`last-apply`
> 读一个从没 apply 过的 bot，或 `applies/{apply_id}` 读一个不存在、或属于别的 bot 的
> `apply_id`，都会得到它。**空报告里 `result` 与 `trigger` 是空串 `""`，不是下表列的任何
> 一个枚举值**，`apply_id` 也是空串、`started_at` 与 `finished_at` 都是 `null`、四个数组
> （`sources` / `categories` / `entries` / `notes`）都是空的。所以按
> `result` 穷举分支时**必须先处理空串**（判空 = 「这个 bot 还没有 apply 记录」），再去分
> `RUNNING` / `SUCCEEDED` / `PARTIAL` / `FAILED`。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `apply_id` | string | 这次 apply 的 id。**空报告时是空串** |
| `bot_id` | string | 目标 bot |
| `trigger` | enum \| `""` | 谁发起的：`explicit` / `put` / `create:pre_container` / `create:on_container` / **`dry_run`**，见 B.7。**空报告时是空串** |
| `result` | enum \| `""` | `RUNNING` / `SUCCEEDED` / `PARTIAL` / `FAILED`，见 B.7。终态是从逐条结果**推导出来的摘要，给人看的**。**空报告时是空串** |
| `started_at` | datetime \| null | 开始时间；bot 从没 apply 过时 `null` |
| `finished_at` | datetime \| null | 结束时间。**`null` 有两个原因，别拿它判「在跑」**：`result` 是 `RUNNING`（真的在跑），或者这是一份**空报告**（`result` 为空串）。要判在飞的活，读 `result == "RUNNING"`，不要读 `finished_at == null` |
| `sources` | object[] | 命名源的溯源，每个源一行，见下。**「这批 bot 线上跑的到底是哪一版内容」看这里** |
| `categories` | object[] | 每个**被声明的**类目一行，见下。文档没提的类目不出现，因为它根本没被碰 |
| `entries` | object[] | 每个**被声明的条目**一行，跨所有类目，见下 |
| `notes` | string[] | 不属于任何条目的 apply 级说明。今天只有一处：teclaw 上「所有类目都写完了、最后整包 artifact 重投失败」记在这里，而不是让整次 apply 失败。ARCA 上恒为空 |

`sources[]`：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `name` | string | 源名（`sources.<name>` 里的那个名字） |
| `ref` | string \| null | 声明的 ref：tag / branch / commit SHA |
| `resolved_sha` | string \| null | 这一次**实际解析到**的 commit。`ref: main` 这种会动的引用，下周就是另一个值 |
| `auth` | string \| null | 用到的凭证**名**。**永远只有名字，没有值** |

`categories[]`：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `category` | string | 类目名，或 `script` |
| `aborted` | bool | 这个类目没有收敛（至少一条声明的条目没能物化）。**单独看它并不保证「这块区域没被动过」**，要和下一个字段一起读 |
| `partially_written` | bool | 失败发生在**写了一半的时候**，这块区域可能已经变了。`aborted` 为真而它为假 = 什么都没写、没有东西要回滚；它为真 = 再 apply 一次收敛 |
| `removed` | string[] | 覆盖**删掉了什么**。它不在 `entries` 里，因为被删的东西根本没有对应的声明条目 |

`entries[]`：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `category` | string | 这条属于哪个类目 / 段 |
| `name` | string | 条目怎么称呼自己：skill 的 `name`、identity 的 `type`、resource 的 `path`、mcp 的 `server_code`、cli_tool 的 `name` |
| `action` | enum | `created` / `updated` / `unchanged` / `skipped` / `failed`，见 B.7 |
| `error` | string \| null | `failed` 或 `skipped` 时说明原因 |
| `note` | string \| null | 成功条目上「你本来得自己推断」的事实。今天只有一处：`script` 用它说明什么时候真正执行 |

> **apply 记录的是「下发」，不是「执行」。**`script` 那一条记的是「已写入」，脚本在
> 容器启动时才跑，退出码不进这份报告。**apply 失败也不改 bot 状态、不阻断 bot 就绪**
> ——所以「bot 是健康的」不等于「它的清单全生效了」。

---

### B.3 用清单创建 bot

#### B.3.1 `POST /openapi/v1/bots/with-manifest`

创建 bot + 提交清单，一次请求。**总是 `202`。**

**权限**：与 `POST /openapi/v1/bots` 相同（这不是针对某个已有 bot 的操作，没有协作者
等级可查）。**纯应用身份的调用方被拒绝（`401`）**——bot 还不存在时没有授权关系可查，
所以这条路必须由一个指名了终端用户的调用来走。

**请求查询参数**：`user_id`（**必填**，见 B.0.1——这条路径同样要它，漏了答 `422`）。
没有 `bot_id`（还没创建出来），也没有 `owner_id`（新 bot 的属主就是 `user_id` 指的那个人）。

**请求 body**（= 普通创建 API 的全部字段 + 一个 `config_manifest`；不认识的键会被拒绝）：

| 字段 | 必填 | 类型 | 含义与取值 |
| --- | --- | --- | --- |
| `bot_name` | ✅ | string | 展示名。**先去首尾空白**，然后：非空、**长度 ≤ 32 字符**、且只能由**中英文、数字、下划线、中划线、空格、`+`、`(`、`)`** 组成——这是**白名单**，`@`、`#`、`.`、`/` 等一律不收。违反 → `400`。另外租户内不能重名 → `409`（与 `400` 是两回事） |
| `bot_desc` | ✅ | string | 这个 bot 是干什么的 |
| `engine` | ✅ | string | 引擎，**创建后不可改**。合法集合由部署配置决定——去引擎组的 available-engines 端点读；不在表里的 `400`。**内部实现引擎不接受**（如 `aicoding`，它是 `claude_code` 背后的内部运行时） |
| `cluster_name` | ✅ | enum | `ACRA` / `ANDC`。**与 `engine` 严格一一对应**：`teclaw` ⟺ `ANDC`，其余引擎 ⟺ `ACRA`。配错 `400` |
| `bot_type` | ✅ | enum | `personal`（本人自用，单一 draft 运行时）或 `service`（为发布而建，随发布流程获得 verify / online 运行时）。**`desktop` 不能从这里创建** |
| `space_id` | ❌ | string \| null | 要关联的业务空间；省略则用当前空间 |
| `engine_properties` | ❌ | object \| null | 引擎专属属性。普通 bot 省略；模板 bot 传 `template_config` |
| `engine_properties.template_type` | ❌ | string \| null | 模板类型。模板工厂快照必填（值从 available-tc-list 回显）；手写的 application-coding 配置省略或写 `applicationCoding` |
| `engine_properties.template_config` | ✅（给了 `engine_properties` 时） | object | 模板配置：手写的 application-coding 属性，或从 bot-templates/available-tc-list **逐字段回显**的模板工厂快照。平台管理的身份与生命周期字段不接受 |
| `config_manifest` | ✅ | string | **清单文档全文（YAML 字符串）**，与 `PUT …/config-manifest` 收的是同一份东西 |

`config_manifest` 的校验规则与 `PUT` **相同**，包括对 `engine_config` 的拒绝：能力表
（`resolve_capabilities`）已经把它标成不支持，而 `PUT` 在落库**之前**就拿能力表校验，
所以**两条路都答 `422`**（附录 C 也是这么说的）。**不要指望「先创建、再 `PUT`」能把
`engine_config` 存进去**——存不进去。

这条路径确实比 `PUT` 多一道**结构性**的关卡：提交时会额外检查「本 build 有没有这个构造
的物化器」，没有就拒。只是今天这道关卡和能力表拒绝的是同一个东西（`engine_config`），
所以你观察不到差别。它存在的意义是兜底——万一将来某个构造在能力表里被标成支持、却还
没有物化器，这条路会拒绝它，而不是让你花掉一次授权、建出一个 bot、然后才配置失败。

⚠️ **第一期：`script` 不得依赖同一份清单声明的任何东西。**首启时脚本被烤进启动命令，
在其他类目下发**之前**就跑了。

**响应 `data`**（`202`）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `bot_id` | string | 已分配的 bot id。拿它去轮询 |
| `iframe_url` | string | 可内嵌的授权链接，或空串 |
| `redirect_url` | string | 整页跳转的授权链接，或空串 |

> **平台只给其中一个，给哪个不可预测——取非空的那个。**
>
> **响应里没有 `state`**：刚提交按定义就是「等授权」，一个能装下终态的字段只会诱导
> 调用方去判断一个不可能出现的值。

**错误**：

| 状态 | 什么时候 | `data` |
| --- | --- | --- |
| `400` | 引擎不在部署配置里 / 是内部引擎；或 `engine` 与 `cluster_name` 不匹配 | `null` |
| `401` | 调用方是**纯应用身份**（没有指名任何终端用户）。bot 还不存在时没有授权关系可查，所以这条路必须由能解析出用户的调用来走 | `null` |
| `403` | 调用方无权代表 `user_id` 指名的那个用户 | `null` |
| `409` | `bot_name` 在租户内重名；或目标空间的 bot 配额满了 | 配额满时是 `{ space_id, space_name, space_type: "PERSONAL"\|"TEAM", ceiling, used }` |
| `422` | 清单不合法 | `{ "violations": [ … ] }`，形状同 B.1.2 |
| `422` | **漏了 `user_id` 查询参数**（B.0.1）。与上一行同码不同因：这一条是参数校验失败，`data` 为 `null`、没有 `violations` | `null` |

> **`422` 时连 `bot_id` 都不会分配**，不会有 Passport 申请、不会有任何东西落库——
> **不会让你点完授权才被告知清单写错了**。
>
> **校验顺序**（决定你先看到哪个错）：① 创建策略归一（老引擎别名会在这一步被改写成
> 真正要跑的引擎）→ ② 名称检查，然后是平台 preflight（配额、保留引擎）→ ③ **对着
> 归一之后的引擎**校验清单 → ④ 清单落库 → ⑤ 申请 Passport。所以拿到 `422` 就意味着
> 名字和配额那关**已经过了**；反过来，`409` 的时候清单还没被看过一眼。

#### B.3.2 `GET /openapi/v1/bots/{bot_id}/with-manifest/status`

这次创建走到哪了。**权限同 B.3.1；只看得到调用方自己的创建。**

**请求**：`bot_id` 加上 `user_id`（必填，见 B.0.1），**没有别的**——不收清单，也不收
任何创建参数。这是刻意的：**被校验的那份清单必然就是被应用的那份**，没有第二份副本能被
人换掉。（这条路径上没有 `owner_id`：它答的是「调用方自己的那次创建」。）

> **纯读**：它读的是落库的行（创建任务行、bot 记录、apply 记录），不查 AgentPass、
> 不启动任何工作、不写任何东西。**轮得快一点不会让创建变快，停止轮询也不会让创建停下。**

**响应 `data`**：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `state` | enum | 创建走到哪了。八个取值见下 |
| `bot_id` | string | 这次创建对应的 bot |
| `iframe_url` | string | **只在 `AWAITING_AUTHORIZATION` 时非空**，之后是空串 |
| `redirect_url` | string | 同上 |
| `bot` | object \| null | 创建出来的 `Bot`（B.0）。**只在 `READY` 与 `APPLY_FAILED` 时出现** |
| `apply` | object \| null | apply 报告（B.2.5）。**只可能在 `READY` 与 `APPLY_FAILED` 出现，但即使在这两个状态也可能是 `null`——务必判空**（两种情形见下）。它只会是**这次创建自己的那份**：创建之后又跑过的显式 apply 不会顶替它（那个去 `last-apply` 看） |
| `message` | string | 状态名说不清楚时的原因；其余状态是空串 |

`state` 的八个取值：

| 值 | 终态 | 有 bot 吗 | 含义 |
| --- | --- | --- | --- |
| `AWAITING_AUTHORIZATION` | ❌ | 还没有 | 等用户点开授权链接。**什么都还没创建** |
| `AUTHORIZATION_REJECTED` | ✅ | **没有** | 用户拒绝了。落库的清单随创建一起删掉，不留残骸。`message` 说明原因 |
| `AUTHORIZATION_EXPIRED` | ✅ | **没有** | 窗口内没人响应（默认 10 分钟）。与「拒绝」分开，因为**没有人做过任何决定** |
| `CREATING` | ❌ | 记录已有 | 已授权，bot 记录已写入，容器正在开通 |
| `CREATE_FAILED` | ✅ | **没有可用的 bot** | 建不出来，或容器始终没起来。**与清单无关** |
| `APPLYING` | ❌ | 有，且在跑 | bot 起来了，清单的容器后阶段正在下发 |
| `READY` | ✅ | 有，且在跑 | bot 起来了，**整份清单都落地了** |
| `APPLY_FAILED` | ✅ | **有，且在跑** | bot 起来了，**部分配置没落地**。响应**一定**带着 `bot`（这就是它跟 `CREATE_FAILED` 一眼可分的依据），`apply` 则**可能为 `null`**（见下）。改完清单 `POST …/apply` 即可，**不需要重建** |

> **三种失败不用读文案就能分辨**：清单不合法 = 提交时 `422`（连 `bot_id` 都没有）；
> `CREATE_FAILED` = 没有可用的 bot；`APPLY_FAILED` = **bot 正在运行**，只是配置缺了一块。
>
> **`PARTIAL` 的 apply 汇报为 `APPLY_FAILED`，不是 `READY`**：按类目覆盖语义，一个
> 半途失败的类目可能已经**删掉**了旧条目却没写进新的——这是要处理的状态，不是带
> 脚注的成功。
>
> 终态的报告**覆盖两个阶段**：容器前阶段的 `script` 会被带进来，不会看起来像是消失了。
>
> ⚠️ **两个终态下 `apply` 都可能是 `null`，别无条件解引用它。**`bot` 在这两个状态下一定
> 有值，`apply` 不是——它只在「这次创建自己的那份报告还在」时才给：
>
> - **`APPLY_FAILED` 且 `apply` 为 `null`**：容器后那一段**根本没能启动**（不是跑了然后失败），
>   创建任务放弃了，而 bot 已经在跑。没有报告可给，因为没有 apply 发生过。**这仍然是需要你
>   处理的状态**——去 `POST …/config-manifest/apply` 手动跑一次，然后看它的报告。
> - **`READY` 且 `apply` 为 `null`**：创建之后你（或别人）又跑过一次显式 apply，把创建那次
>   的记录顶掉了。这个端点只答「这次创建是怎么结束的」，不会拿后来那次冒充它——**bot 现在
>   的配置状态去 `GET …/config-manifest/last-apply` 看**。
>
> 一句话：**`apply` 非空时它是创建那次的权威报告；为 `null` 时改问 `last-apply`。**
>
> **teclaw 的顺序不同**（§7.1：先写记录、对着记录 apply、再开容器），所以它的状态流是
> `CREATING → APPLYING → CREATING → READY`。

**错误**：

| 状态 | 什么时候 |
| --- | --- |
| `401` | 调用方是纯应用身份（同 B.3.1） |
| `403` | 无权代表这个用户 |
| `404` | **这个 `bot_id` 上没有用清单创建过**——包括用普通接口创建、事后 `PUT` 清单的 bot。它的配置状态去 `last-apply` 看 |

---

### B.4 源凭证

租户级命名对象，一次注册、所有 bot 的所有清单都能按名字引用。语义、
`allowed_prefixes` 的匹配规则与选 token 的口径见 §4.2。

**权限**：读——租户内任何应用都可以；写与删——**只有属主应用**（第一次 `PUT` 占下
这个名字的那个应用），其他应用 `403`。

#### B.4.1 `GET /openapi/v1/bots/source-credentials`

租户内全部凭证，按名字排序。**请求**：无参数——源凭证是**租户级**的，不针对某个 bot，所以这一组端点不带 `bot_id`／`owner_id`。

**响应 `data`**：数组，每项：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `name` | string | 凭证名。清单里 `auth: <name>` 就是引用它 |
| `has_secret` | bool | 这个名字下有没有存着 secret |
| `updated_at` | datetime | 最后一次写入时间（服务端时钟） |

#### B.4.2 `GET /openapi/v1/bots/source-credentials/{name}`

单个凭证的掩码详情。

**请求**：

| 路径参数 | 类型 | 含义 |
| --- | --- | --- |
| `name` | string | 凭证名。**调用方自选的标识符，与域名之间没有任何推导关系**。规则见 B.4.3 |

**响应 `data`**：B.4.1 的三个字段，再加：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `type` | enum | 认证机制：`header` / `oss_aksk` / `basic`，见 B.7 |
| `header_name` | string \| null | secret 会被放进哪个请求头；机制不用请求头时 `null` |
| `allowed_prefixes` | string[] | 这个凭证**允许被出示给**哪些绝对 HTTPS 前缀 |
| `owner_app_id` | int | 属主应用的注册 id。轮换与删除只有它能做；租户内所有应用都能读这份元数据 |

**任何路径下都不返回 secret 的值。**日志、错误信息、apply 报告里只出现凭证**名**。

**错误**：`404` —— 本租户下没有这个名字的凭证。

#### B.4.3 `PUT /openapi/v1/bots/source-credentials/{name}`

注册或轮换。**轮换 = 对同一个名字重新 `PUT`。**

**请求**：路径参数 `name`，规则如下（违反 → `422`）：

| 约束 | 说明 |
| --- | --- |
| 非空 | 空名字直接拒 |
| **长度 ≤ 128** | 与存储列宽一致，在边界上拒绝而不是让 DB 报错 |
| **不含任何空白字符** | 空格、制表符、换行一律不行——它是标识符，不是描述 |

除此之外字符不限（它只是个名字，与域名、仓库名没有任何推导关系）。body：

| 字段 | 必填 | 类型 | 含义与取值 |
| --- | --- | --- | --- |
| `type` | ❌ | enum，默认 `header` | 出示 secret 的**认证机制**（不是存储类型）。`header` 是唯一已实现的；`oss_aksk` 与 `basic` 是保留值，**写了会被 `422` 拒绝** |
| `header_name` | 见右 | string \| null | secret 放进哪个请求头。**`type` 是 `header` 时必填**（如 git 宿主的 `PRIVATE-TOKEN`，或 bearer 风格的 `Authorization`）。必须是合法的 HTTP header token（RFC 7230：字母数字与 `!#$%&'*+.^_\`\|~-`，**不含空格、冒号**），**长度 ≤ 256**；不合法 → `422` |
| `secret` | ✅ | string | secret 值本身，**是完整的头值**。加密落库，**永远读不回来**，不进日志、不进 apply 报告 |
| `allowed_prefixes` | ✅ | string[] | 授权出示范围：绝对 HTTPS 前缀，**至少一项，空数组即拒绝**。按**路径段边界**匹配——`https://host/team/content` 授权的是那棵树，**不包括** `…/team/content-secret` |

**响应 `data`**：与 B.4.2 相同的掩码详情（回显你刚写的元数据，不含 secret）。

**错误**：

| 状态 | 什么时候 |
| --- | --- |
| `403` | 这个名字已被别的应用占着——轮换是属主应用一个人的事 |
| `422` | **`name` 不合规**（空、超 128 字符、含空白）；`type` 是保留机制；`type` 是 `header` 却没给 `header_name`，或 `header_name` 不是合法 header token / 超 256 字符；`secret` 为空；前缀不是绝对 HTTPS 或数组为空 |
| `503` | 生产环境解析不到平台主密钥。**写入被拒绝，绝不明文落库** |

> **注册/轮换不触发任何 apply**，下一个 apply 点自然用新值。

#### B.4.4 `DELETE /openapi/v1/bots/source-credentials/{name}`

删除。重复删除也成功。

**请求**：路径参数 `name`。**响应 `data`**：`{ "deleted": true }`。**错误**：`403`（非属主应用）。

> 删掉一个**仍被引用**的凭证：引用它的条目在下次 apply 记 `failed` 并写明凭证名，
> 进而按 §3.4 让整个类目不写。**绝不会退化成不带凭证的匿名请求。**

---

### B.5 CLI 工具

`…/cli-tools` 与清单的 `cli_tools` 类目**走同一个组件**，所以同一份声明得到同样的
拒绝理由。区别只有一处：**清单 apply 是全量覆盖**（不再声明的工具会被移除，包括你
用这组 API 装的），单次 `POST` 不是。语义见 §5.6。

#### B.5.1 `POST /openapi/v1/bots/{bot_id}/cli-tools`

装一个命令行工具。**权限：ADMIN。**

**请求 body**：

| 字段 | 必填 | 类型 | 含义与取值 |
| --- | --- | --- | --- |
| `name` | ✅ | string，≤128 | agent 要敲的**命令名**。**裸标识符，不含位置信息**——落在哪由引擎决定 |
| `source` | ✅ | string | 从哪里取。平台侧可达的 https |
| `digest` | ✅ | string | `sha256:<64 位十六进制>`，**强制**。平台在代你分发可执行物，供应链必须钉死。它校验的是**取回来的那个对象**——二进制本身，或整个压缩包 |
| `unpack` | ❌ | enum \| null | 源是压缩包时写 `zip` 或 `tar.gz`；省略则取回来的对象本身就是可执行文件 |
| `subpath` | 见右 | string \| null | 包内哪个文件是这个命令。**给了 `unpack` 就必填，没给 `unpack` 就非法**——一个条目 = 一个命令 = 一个文件 |
| `version` | ❌ | string \| null | **纯元数据，不参与收敛**：同样的字节换个 version 字符串，还是同一个工具，不会重新下发 |
| `auth` | ❌ | string \| null | 取源时用的**凭证名**（B.4）。**永远不是 secret 值** |

平台的动作顺序：取源 → 按 `digest` 验 → （有 `unpack` 时）解包取出 `subpath` 那一个
文件 → 确认它是 **x86-64 ELF 可执行文件** → 留一份自己的副本 → 让引擎装上。
**任何一步失败都不留记录**，所以 `200` 就意味着 bot 真的有这个命令了。

**响应 `data`**（一个 `CliTool`）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `name` | string | 命令名 |
| `version` | string \| null | 你声明的那个。元数据 |
| `digest` | string | 取回对象被钉住的 digest |
| `subpath` | string \| null | 哪个包内成员成了这个命令 |
| `md5` | string | **交付出去的那个文件**的 md5，由平台在解包与选取**之后**算——所以是可执行文件的，不是压缩包的。引擎拿它做变更判断 |
| `size_bytes` | int | 交付出去的那个文件的大小 |
| `installed_by` | string | `manifest`（清单 apply 装的）或装它的**用户 id**。**清单 apply 是全量覆盖，会移除它没声明的、从这里装的工具**——这个字段就是报告能这么说的依据 |
| `gmt_modified` | datetime | 这条记录最后一次变化的时间 |

**错误**：

| 状态 | 什么时候 |
| --- | --- |
| `404` | bot 不存在，**或**协作者等级不到 ADMIN（不可区分，见 B.0） |
| `409` | **两种情况，都是 `409`，但含义不同**：① 这个 bot 已经有同名工具——单次安装不会替换你没提到的东西，要换先删，或者用清单声明整套（清单 apply 是全量覆盖）；② **这个 bot 的引擎根本装不了 CLI 工具**（如 desktop bot），这一条在**取源之前**就判掉，message 是 `This bot's engine cannot take CLI tools` |
| `422` | **声明或字节本身**被拒：没钉 digest、源对不上 digest、包里没有那个成员、二进制是别的架构、或者引擎拒绝了**这一次**安装。注意它与上面 ②的区别——②是「这个引擎压根不支持这个能力」，`422` 是「支持，但这次不行」 |

#### B.5.2 `GET /openapi/v1/bots/{bot_id}/cli-tools`

**权限：MEMBER。请求**：只有 B.0.1 的公共参数。

**响应 `data`**：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `tools` | object[] | 每项是一个 `CliTool`（B.5.1），**按名字排序**。没有工具时是空数组 |

> 这是**平台自己的记录**，不是读容器。它也正是清单 apply 计算「要删掉哪些」的依据，
> 所以同时是「全量覆盖会替换掉什么」的答案。

#### B.5.3 `DELETE /openapi/v1/bots/{bot_id}/cli-tools/{name}`

**权限：ADMIN。**

**请求**：

| 路径参数 | 类型 | 含义 |
| --- | --- | --- |
| `bot_id` | string | 目标 bot |
| `name` | string | 要移除的命令，**按它被装上时的名字写** |

**响应 `data`**：`{ "deleted": true }`。从 bot 上移除、删掉平台记录、删掉那份字节副本。

**错误**：`404` —— 这个 bot 没有叫这个名字的工具。**这一个不是幂等的**（和清除清单
不同）：「工具已经没了」和「你写错了名字」值得分开告诉你。

> 注意这里的 `404` 有三个来源，且**互相不可区分**：没有这个工具、没有这个 bot、你的协作者
> 等级不到 ADMIN（B.0）。所以删除返回 `404` 时，先确认自己在这个 bot 上有 ADMIN，再去怀疑
> 工具名。

---

### B.6 同一份状态的「另一扇门」

清单管的每个类目，平台上都另有一组手工管理的端点。它们不是本特性的一部分，但
**被清单声明的类目上，这些端点的改动会在下一个 apply 点被覆盖回去**（§3.2）——
列在这里，是为了让你知道「哪扇门通向同一间屋子」。

| 类目 / 段 | 另一扇门 | 关系 |
| --- | --- | --- |
| `cli_tools` | **B.5**（`…/cli-tools`） | 同一个组件，出入参见上 |
| `script` | `GET` / `PUT` / `DELETE` `/openapi/v1/bots/{bot_id}/startup-script`（权限 OWNER） | #935 老端点，**不感知清单**；清单声明的 `script` 在 apply 时物化进同一行，所以在声明了 `script` 的 bot 上，从这里改会被下一次 apply 改回去（§5.5） |
| `identity` | `GET` `…/identity`、`GET` / `PUT` `…/identity/{file_type}` | 同一批 identity 文件 |
| `skills` | `/openapi/v1/bots/{bot_id}/skills` 一组（上传、启用/停用、删除、参数） | 同一个 active skill set |
| `resources` | `/openapi/v1/bots/{bot_id}/resources` 一组 | 同一棵 workspace 文件树。**CLI 工具不在其中**——它不按路径寻址（§5.6） |
| `mcp` | `GET /openapi/v1/bots/{bot_id}/mcps`、`POST …/mcps/{server_code}/activate`、`POST …/mcps/{server_code}/deactivate` | **同一个服务**（`DirectActivationService`），和 `cli_tools` 一样：清单的 `mcp` 物化器就是调它来收敛的，收敛的对象是这个 bot 的**已启用 server 集合**。所以**界面上手工开的 server 会被声明了 `mcp` 的清单移除**（§8）。⚠️ 别和 `…/skill-sets/{set_id}/mcps/{server_code}` 搞混：那是**另一个控制面**，改的是某个 SkillSet 内部的 MCP 成员关系，所有权与冲突语义都不同 |
| `engine_config` | `GET` / `PUT` `/openapi/v1/bots/{bot_id}/engine/config` | 这扇门**开着**（自由 JSON，直接写设备），但**清单类目 `engine_config` 没开**——今天要配引擎配置只能走这里，且它不受清单管辖、不会被 apply 覆盖 |

> 这张表列的是「同一份状态的另一个入口」，不是完整的 bot 开放平台 API 目录。这些端点
> 自己的出入参以各自的 OpenAPI 文档为准。

---

### B.7 枚举速查

一页看完本特性所有枚举字段的取值。

**`cluster_name`**（请求与响应）：

| 值 | 含义 |
| --- | --- |
| `ACRA` | `teclaw` 以外的全部引擎 |
| `ANDC` | 引擎 `teclaw` |

**`bot_type`**：请求侧只接受 `personal`（本人自用，单一 draft 运行时）与
`service`（为发布而建，随发布流程获得 verify / online 运行时）；响应侧还可能读到
`desktop`（跑在用户自己机器上，生命周期不归本 API 管，也不在本特性范围内）。

**`Bot.status`**——**开放集合**，新的生命周期还会加值，所以**除 `ACTIVE` 之外一律
当作「还不能干活」**：

| 值 | 含义 |
| --- | --- |
| `PENDING` | 已创建，设备还在开通 |
| `ACTIVE` | 在跑，可达 |
| `FAILED` | 设备开通失败（重启，或删掉重建） |
| `OFFLINE` / `RELEASING` / `RELEASED` | desktop bot 的生命周期状态 |
| `RECYCLED` / `REACTIVATING` | 休眠 bot 的回收状态 |

**创建状态 `state`**（B.3.2）：`AWAITING_AUTHORIZATION` · `AUTHORIZATION_REJECTED` ·
`AUTHORIZATION_EXPIRED` · `CREATING` · `CREATE_FAILED` · `APPLYING` · `READY` ·
`APPLY_FAILED`——逐个含义见 B.3.2 的表。

**apply 的 `result`**：

| 值 | 含义 |
| --- | --- |
| `""`（空串） | **空报告**：这个 bot 从没 apply 过，或你问的 `apply_id` 不存在／不属于它。`trigger` 同时也是空串。**穷举分支时先判这一个**（B.2.5） |
| `RUNNING` | 还没做完。apply 是启动式的，所以轮询时先看到它；此时 `finished_at` 为 `null` |
| `SUCCEEDED` | 全部落地 |
| `PARTIAL` | 一部分落地。**按类目覆盖语义，这是要处理的状态，不是带脚注的成功** |
| `FAILED` | 都没落地 |

**`PUT` 响应里 `apply.result`**：只有 `RUNNING` 与 `NOT_STARTED` 两个值；
`NOT_STARTED` 时 `reason` 是 `apply_in_progress` 或 `not_started`。

**条目的 `action`**：

| 值 | 含义 |
| --- | --- |
| `created` | 这个类目区域里原来没有，装上了 |
| `updated` | 原来有，内容变了 |
| `unchanged` | 已经是声明的样子，**零动作。这是正常的**——收敛就是「已经对了就不动」 |
| `skipped` | **因为所在类目被中止（§3.4）而没写**，不是「可选所以跳过」。同一类目里，把类目搞挂的那条记 `failed`，其余无辜的记 `skipped` |
| `failed` | 这一条自己没成，`error` 说明原因 |

**apply 的 `trigger`**：

| 值 | 什么发起的 | 进历史吗 |
| --- | --- | --- |
| `""`（空串） | 空报告，没有哪次 apply 可说（见上） | —— |
| `explicit` | `POST …/config-manifest/apply` | ✅ |
| **`dry_run`** | **`POST …/apply?dry_run=true` 的预览报告**。它不落库、不进 `last-apply`，但**返回给你的那份报告里 `trigger` 就是这个值**——按 `trigger` 穷举分支时别漏了它 | ❌ |
| `put` | `PUT …/config-manifest` 之后自动跟的那一次 | ✅ |
| `create:pre_container` | 用清单创建 bot 的**容器前**阶段 | ✅ |
| `create:on_container` | 用清单创建 bot 的**容器后**阶段 | ✅ |

**没有 `restart` / `republish`**——第一期它们不是 apply 点（§7）。

**能力表的 `kind` 与 `name`**：

| `kind` | 可能的 `name` |
| --- | --- |
| `category` | `mcp` · `resources` · `skills` · `engine_config` · `identity` · `cli_tools` |
| `section` | `script` |
| `source` | `url`（内联 HTTPS URL）· `git`（git 引用）· `named`（`from` 引用命名源）· `content`（内联文本） |

**凭证的 `type`**：

| 值 | 含义 |
| --- | --- |
| `header` | 取源时把 secret 放进 `header_name` 指定的请求头。**唯一已实现的机制** |
| `oss_aksk` | 保留给将来的 OSS AK/SK 机制，**今天写了就被拒**——这样存下来的 type 从第一天起就是真的 |
| `basic` | 保留给将来的 HTTP Basic 机制，同上 |

**CLI 工具的 `unpack`**：`zip` / `tar.gz`（或省略，表示源本身就是可执行文件）。
---

## 附录 C：尚未开放的写法

**规则：这个面绝不接受它 apply 不了的东西。**下面这些在 schema 里表达得出来，
但没有对应的物化器，所以 `PUT` 会明确报错并拒绝——不会被静默忽略，也不会
「先存着以后生效」。

> 规范性版本见 `manifest-schema.zh-CN.md` §7，两处内容一致。

| 构造 | 为什么 | 什么时候开放 |
| --- | --- | --- |
| 类目 `engine_config` | 按跨引擎确认的结论移出第一期，至今没有物化器 | 它的物化器回来时。**在此之前**要写引擎配置，走 `GET`/`PUT /openapi/v1/bots/{bot_id}/engine/config`（附录 B.6），那条路不受清单管辖 |
| `resources` 条目用 **`from`（命名源）或 git 源** | resources 物化器目前只走 URL 那条路（W6）；接受了会在 apply 时以一条看不懂的错误失败，所以在 `PUT` 就精确拒掉 | resources 接上 git 那条路之后。**在此之前**：resources 条目写内联 `source` URL 或 `content` |
| `cli_tools` 条目用 **`from`（命名源）或 git 源** | cli_tools 物化器同样不解析命名源：`CliToolDecl.from_entry` 把 `from` 的**源名原样当成 URL**，`resolve` 也不经过取源会话。⚠️ **与上一行不同，这个组合 `PUT` 不会拒——它会在 apply 时失败**，所以它今天是一个「写得出、过得了、跑不通」的陷阱 | cli_tools 接上取源会话之后。**在此之前**：cli_tools 条目一律写内联 `source` URL |

**已经开放的**（早期版本的本文档曾把它们列在这张表里，现在不是了）：

| 构造 | 状态 |
| --- | --- |
| 类目 `cli_tools` | **已开放**（W9），与管理 API `…/cli-tools` 同一个组件（§5.6） |
| `from` 指向命名源 | **对 `skills` 与 `identity` 已开放**（W7）。`resources` 与 `cli_tools` **不可用**，见上表 |
| git 源（`sources.<name>.git` 或条目内联的 git 引用） | 同上：**只对 `skills` 与 `identity`** |

还有一条与引擎有关、不属于「没做完」的拒绝：**`script` 在 teclaw 与 desktop bot 上
写入即拒**（§2.1）——它不是待开放，是那两类 bot 结构上没有执行通道。

> 一句话给写客户端的人：**能力表是唯一的事实来源**，写之前先
> `GET …/config-manifest/capabilities`；它与 `PUT` 的判定是同一个函数，不会互相矛盾。
> 唯一它答不了的是上面 `resources` 那一条——那是「类目 × 源形态」的组合，能力表按
> 单个构造出结论，所以它由条目校验逐条拒绝，理由里会点名类目。

---

## 附录 D：与设计文档的已知差异

**`manifest-schema.zh-CN.md` 已与本文对齐**——早先那几处分歧（`OCB_*` 变量、
`on_fetch_failure` 的 `skip`、缺少 `mode`、`skills: []` 的含义、identity 保留
名单）都已改进 schema 文档；两份用户可见的契约现在说的是同一件事。

仍未修订的是 **`design.zh-CN.md`**：它早于下面这些决策，作为设计论证保留原样。
凡与本文/schema 不一致处，**以本文与 schema 为准**：

| design 文档写的 | 实际口径 | 在哪儿讲 |
| --- | --- | --- |
| §3.1：`PUT` 惰性生效（等下次重启） | `PUT` **立即生效，不需要重启**；`script` 例外——立即下发、下次启动执行 | §7 |
| §3.4：固定顺序 `engine_config → identity → resources → skills → mcp`，`script` 最后、可依赖已就位的实体 | 第一期**反过来**：首启时 `script` 先于其余类目，因此不得依赖它们 | §5.5 |
| §3.2：实体上有 `managed by manifest` 标记 | v1 **没有**这个标记：清单装的实体与手工创建的完全一样；状态由 `GET …/config-manifest` 与 `last-apply` 回答 | §3.2 · schema §1 |
| §4.3：`on_fetch_failure` 有 `skip` | 只有 `keep_last` / `fail` | §6.4 |
| §6：`DELETE` 把实体「摘除 managed 标记」 | `DELETE` **什么都不删**（没有类目被声明 = 没有东西被覆盖）；要清空用 `[]` | §3.3 |
| §10.5：git 源走托管服务的归档 API | 改为 **git over HTTPS 的浅层单 ref fetch**——本部署的 git 宿主没有只读的 API scope | schema §2.2 |

---

## 附录 E：字段速查（一页纸）

写清单时对着这张表看，不用翻正文。规范定义见 `manifest-schema.zh-CN.md`。

### 顶层三段

| 字段 | 必填 | 取值 | 备注 |
| --- | --- | --- | --- |
| `schema_version` | ✅ | `1` | 未知版本拒绝写入 |
| `sources` | ❌ | 命名源字典（§4.1、§6） | **只有 `skills` / `identity` 条目能用 `from` 引用它**；`resources` 写了当场被拒，`cli_tools` 写了 apply 时才失败（附录 C） |
| `manifest` | ❌ | 六个类目，见下 | 缺省的类目完全不碰 |
| `script` | ❌ | `{ body: <shell> }` | teclaw / desktop **写入即拒**；≤ 24 KiB |

### 源（`sources.<name>`，或条目里内联的 `source`）

| 字段 | 说明 |
| --- | --- |
| `git` + `ref` | git 源；`ref` = tag / branch / commit SHA。**只对 `skills` / `identity` 有效**——`resources`、`cli_tools` 不可用（附录 C） |
| `url` | URL 源；作为前缀，条目的 `subpath` 拼在其后 |
| `auth` | 凭证名（§4.2）。用 `from` 时凭证声明在**源**上，条目里不写 |
| `mode` | `strict` / `non_strict`（默认）；只对会动的 `ref` 有意义（§6.2） |

### 条目通用字段（resources / skills / identity / cli_tools）

**来源四选一、互斥**：`from` + `subpath` ／ `source` ／ `content` ／ 注册项引用。

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `subpath` | 源的根 | **源内路径**（不是落点）；禁绝对路径与 `..` |
| `digest` | 无 | `sha256:…`。git 源上写它**报错**（commit SHA 就是 digest）；`skills` 与 `cli_tools` 的非 git 源**强制** |
| `auth` | 无 | 仅对内联 `source` 有效；`content` 条目上非法 |
| `on_fetch_failure` | `keep_last` | 只有 `keep_last` / `fail`（§6.4） |

### 逐类目的键字段与覆盖区域

| 类目 | 键字段 | 约束 | 被覆盖的区域 |
| --- | --- | --- | --- |
| `identity` | `type` | 白名单枚举；claude_code 仅 `CLAUDE.md`；**`MEMORY.md` / `IDENTITY.md` 写入即拒** | identity 文件集合（减保留名单） |
| `skills` | `name` | 标识符，不含位置信息 | active skill set |
| `resources` | `path` | workspace 相对；`/` 结尾 = 目录条目；禁绝对路径/`../`；禁嵌套；**来源只能是内联 `source` URL 或 `content`** | **仅被声明的 `path` 子树** |
| `mcp` | `server_code` | 平台注册表引用；**条目只有这一个字段** | 已启用的 server 集合 |
| `engine_config` | `config` 对象 | **未开放**（附录 C） | 被声明的顶层键 |
| `cli_tools` | `name` | 命令名，同 bot 内唯一，不含路径分隔符；`digest` 强制 | 清单下发的工具集合（含用 `…/cli-tools` API 装的） |
| `script` | `body` | 仅 ARCA 系 | —— |

目录条目专用（`resources`，归档形态）：`unpack`（`zip` / `tar.gz`）、
`strip_components`（默认 `0`，不自动探测顶层目录）。

### 可用变量

`${BOT_ENGINE_TYPE}` · `${BOT_ENV}` · `${BOT_TENANT}` ·
`${BOT_ARCH}`（当前恒为 `amd64`）。**不是 `OCB_*`，也没有 `${BOT_ID}`**——
按 bot 区分的路径请直接写字面量（§4.3）。

### 一眼判断「会不会被拒」

写了下面任何一条，提交时就会被拒绝（不是静默忽略）：

- `${OCB_*}`、`on_fetch_failure: skip`、`apply_once`；
- `identity.type` 是 `MEMORY.md` / `IDENTITY.md`；
- 一个条目上同时有两个来源，或 `content` 条目上写了 `auth`/`digest`/`on_fetch_failure`；
- git 源条目上写了 `digest`；用了 `from` 的条目上写了 `auth`；
- `source` URL 里带 `user:token@`；
- `resources.path` 绝对路径或含 `../`，或嵌套在另一个目录条目之下；
- `skills` / `cli_tools` 非 git 源缺 `digest`；
- teclaw / desktop bot 写了 `script`；
- 类目 `engine_config`；
- `resources` 条目用了 `from`（命名源）或 git 源。

**`cli_tools` 条目用 `from` 或 git 源是唯一的例外**：它**不会**在提交时被拒，而是在
apply 时失败。别按「`PUT` 过了就没问题」推断这一条。
