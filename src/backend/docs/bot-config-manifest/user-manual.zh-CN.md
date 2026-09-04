# Bot 配置清单 用户手册

> **状态：DRAFT（讨论稿），随特性实现同步修订。**本文是「Bot 配置清单」的
> **使用**说明——面向要给 bot 配内容的业务方，讲怎么把一份清单写出来、发上去、
> 确认它生效了、以及出问题时怎么查。规范性字段定义见
> `manifest-schema.zh-CN.md`，设计论证见 `design.zh-CN.md`，完整业务案例见
> `examples.zh-CN.md`。
>
> **本文按已拍板的口径写**，并与 `manifest-schema.zh-CN.md` 保持一致——两份
> 用户可见的契约说的是同一件事。`design.zh-CN.md` 早于其中几条决策、未随之
> 修订，差异清单见**附录 D**。第一期尚未开放的写法见**附录 C**（与 schema §7
> 同一张表）——写了会在 `PUT` 时被拒绝，而不是被静默忽略。

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
13. [附录](#附录-a完整清单示例)（A 完整示例 · B API 速查 · C 尚未开放 · D 与设计文档的差异 · E 字段速查）

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

还有两道与引擎无关的门，**第一期对所有人都关着**：`engine_config` 与
`cli_tools` 这两个类目、以及命名源 `from` 与 git 源——写了会在提交时被拒绝，
清单见**附录 C**。所以第一期的取源形态是**条目内联的 HTTPS `source` URL**。

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

- **清单在申请授权之前就被校验**——不会让你点完授权才被告知清单写错了；
- **被校验的清单就是被应用的清单**，它在第一段落库，轮询时不重新提交。

**第 3 步：点开返回的 Passport 授权链接。**每个 bot 一次，这是 AgentPass 的
固有限制，不是本特性引入的（所以「一份清单批量创建 N 个 bot」= N 次点击）。

**第 4 步：轮询到终态**（状态机见 §4.5）。`READY` 与 `APPLY_FAILED` **都**带
apply 报告和 `bot`，逐条告诉你哪些条目下发了、哪些没有——`APPLY_FAILED` 意味着
**bot 在跑**，只是配置缺了一块。拿不到 bot 的失败叫 `CREATE_FAILED`，是另一件事。

**第 5 步：以后要改**，不必重建 bot——改清单 `PUT` 上去即可，走路径 B 的第 3
步起，**立即生效、不需要重启**。

> **端点路径以实现为准**（附录 B）。

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

- **读回是掩码的**——`GET` 只返回 `has_secret` / `header_name` / `allowed_prefixes`
  / `updated_at`，任何路径下都不返回值。日志、错误信息、apply 报告里只出现凭证**名**。
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
  `bot`**，改完清单 `POST …/apply` 收敛即可。
- **某个类目没被覆盖（`PARTIAL`）汇报为 `APPLY_FAILED`，不是 `READY`。**按 §3.3
  的类目覆盖语义，一个半途失败的类目可能已经**删掉**了旧条目却没写进新的——这是
  要处理的状态，不是带脚注的成功。
- **清单在申请 Passport 之前就被校验**——不会让你点完授权才被告知清单写错了。
- **提交时比 `PUT` 多一条拒绝**：本期没有物化器的类目（如 `resources`）在这里**被
  拒**，而不是像 `PUT` 那样先存着。原因是这条路径上「先接受」的代价是一次授权、
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

> **端点路径以实现为准**（附录 B）。

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
  "trigger": "create|republish|restart|explicit",
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

**`result` 的四个值**：`RUNNING` 是「还没做完」——apply 是启动式的，所以轮询时先
看到它；然后才是 `SUCCEEDED` / `PARTIAL` / `FAILED`。

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
  （尚未开放，见附录 C）。
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
    from: artifacts
    subpath: shopctl/2.3.0/shopctl-linux-amd64
    digest: "sha256:9f2c…"        # 本类目强制
    version: "2.3.0"              # 元数据；**不参与收敛**
```

要点：

- **交付单位是「一个自包含的可执行文件」。**压缩包只是传输形态——用 `unpack` +
  `subpath` 指出包内哪个文件是这个命令，平台取出它，**包内其余文件不下发**。一个
  包里两个命令就写两个条目。所以**需要同包辅助程序、或运行时要读同包 `lib/` 的
  工具用不了**，请打成静态二进制。（没有 `entrypoints` 字段。）
- **两种源形态**：直接指向一个二进制，或指向一个压缩包 + `subpath`。两种都必须
  带 `digest` —— 平台代你分发可执行物，供应链必须钉死。（`md5` 是平台物化之后
  自己算出来给引擎做变更判断的，不是你写的字段。）
- **`digest` + `subpath` 才是收敛依据，`version` 不是。**只改 `version` 不会
  触发重新下发——否则改一个字符串就会重推一个可能 200 MiB 的二进制。
- **两个入口，一套实现**：清单里声明，或直接调管理 API——
  `POST` / `GET` / `DELETE /openapi/v1/bots/{bot_id}/cli-tools`（读 MEMBER、
  写 ADMIN，与清单本身同级）。**两条路走的是同一个组件**，所以同一份声明得到
  同样的拒绝理由。区别只有一处：清单 apply 是**全量覆盖**（不再声明的工具会被
  移除，包括你用 API 装的），单次 `POST` 不是（同名工具答 409，要换先删）。
- **两个家族上 `PUT` 都是立即生效的**，没有 §2.6 那条例外：ARCA 走引擎的 CLI
  端点装进运行中的容器，teclaw 由重新编排的 artifact 承载。
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
| `cli_tools`（未开放） | **强制**——平台代你分发可执行物 |
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
| 某个类目 `unsupported` | 引擎不支持（teclaw 的 `script`），或第一期还没开放（附录 C） | 先 `GET …/capabilities` |
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

## 附录 B：API 速查

| 方法与路径 | 用途 |
| --- | --- |
| `GET /openapi/v1/bots/{bot_id}/config-manifest` | 读整份清单（没有 manifest 的 bot 读出的是**空文档，不是错误**） |
| `PUT /openapi/v1/bots/{bot_id}/config-manifest` | 整体替换；all-or-nothing 校验；接受即生效 |
| `DELETE /openapi/v1/bots/{bot_id}/config-manifest` | 清除声明；**不删除任何已装上的实体** |
| `GET /openapi/v1/bots/{bot_id}/config-manifest/capabilities` | 该 bot 的逐类目支持表；与 `PUT` 判定同源 |
| `POST /openapi/v1/bots/{bot_id}/config-manifest/apply` | 显式 apply；`?dry_run=true` 只返回计划 |
| `GET /openapi/v1/bots/{bot_id}/config-manifest/last-apply` | 最近一次 apply 报告——**「生效了吗」的权威答案** |
| `PUT /openapi/v1/bots/source-credentials/{name}` | 注册/轮换租户级凭证 |
| `GET /openapi/v1/bots/source-credentials[/{name}]` | 列表/单个，**仅掩码元数据** |
| `DELETE /openapi/v1/bots/source-credentials/{name}` | 删除；仍被引用时下次 apply 该条目 `failed` |
| `GET/PUT/DELETE /openapi/v1/bots/{bot_id}/startup-script` | #935 老端点，不感知清单；清单声明的 `script` 在 apply 时写进同一行 |
| 用清单创建 bot | 异步创建 API，见 §4.5（端点路径以实现为准） |

---

## 附录 C：第一期尚未开放的写法

**规则：这个面绝不接受它 apply 不了的东西。**下面这些在 schema 里表达得出来，
但第一期没有对应的物化器，所以 `PUT` 会明确报 `unsupported` 并拒绝——不会被静默
忽略，也不会「先存着以后生效」。

> 规范性版本见 `manifest-schema.zh-CN.md` §7，两处内容一致。

| 构造 | 为什么 | 什么时候开放 |
| --- | --- | --- |
| 类目 `cli_tools` | 交付按业务优先级后置：没有物化器、没有 PATH 下发 | 该工作项落地后 |
| 类目 `engine_config` | 按跨引擎确认的结论移出第一期 | 它的物化器回来时 |
| `from` 指向**命名源** | 命名源解析属于 git/命名源工作项 | 该工作项落地后 |
| **git 源**（`sources.<name>.git` 或条目内联的 git 引用） | 同上 | 该工作项落地后 |

> 命名源与 git 源是本文推荐的主力写法（§4.1、§4.8），它们在关键路径上、按计划进
> v1。在它们落地之前，可用的取源形态是**条目内联的 HTTPS `source` URL**——
> 先用 URL 源跑通，之后迁到命名源只是把 `source` 换成 `from` + `subpath`。
>
> 一句话给写客户端的人：**能力表是唯一的事实来源**，写之前先 `GET …/capabilities`。

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
| `sources` | ❌ | 命名源字典（§4.1、§6） | **第一期未开放**（附录 C） |
| `manifest` | ❌ | 六个类目，见下 | 缺省的类目完全不碰 |
| `script` | ❌ | `{ body: <shell> }` | teclaw / desktop **写入即拒**；≤ 24 KiB |

### 源（`sources.<name>`，或条目里内联的 `source`）

| 字段 | 说明 |
| --- | --- |
| `git` + `ref` | git 源；`ref` = tag / branch / commit SHA（**第一期未开放**） |
| `url` | URL 源；作为前缀，条目的 `subpath` 拼在其后 |
| `auth` | 凭证名（§4.2）。用 `from` 时凭证声明在**源**上，条目里不写 |
| `mode` | `strict` / `non_strict`（默认）；只对会动的 `ref` 有意义（§6.2） |

### 条目通用字段（resources / skills / identity / cli_tools）

**来源四选一、互斥**：`from` + `subpath` ／ `source` ／ `content` ／ 注册项引用。

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `subpath` | 源的根 | **源内路径**（不是落点）；禁绝对路径与 `..` |
| `digest` | 无 | `sha256:…`。git 源上写它**报错**；`skills` 非 git 源**强制**；`cli_tools` **强制** |
| `auth` | 无 | 仅对内联 `source` 有效；`content` 条目上非法 |
| `on_fetch_failure` | `keep_last` | 只有 `keep_last` / `fail`（§6.4） |

### 逐类目的键字段与覆盖区域

| 类目 | 键字段 | 约束 | 被覆盖的区域 |
| --- | --- | --- | --- |
| `identity` | `type` | 白名单枚举；claude_code 仅 `CLAUDE.md`；**`MEMORY.md` / `IDENTITY.md` 写入即拒** | identity 文件集合（减保留名单） |
| `skills` | `name` | 标识符，不含位置信息 | active skill set |
| `resources` | `path` | workspace 相对；`/` 结尾 = 目录条目；禁绝对路径/`../`；禁嵌套 | **仅被声明的 `path` 子树** |
| `mcp` | `server_code` | 平台注册表引用；**条目只有这一个字段** | 已启用的 server 集合 |
| `engine_config` | `config` 对象 | **第一期未开放** | 被声明的顶层键 |
| `cli_tools` | `name` | 命令名，同 bot 内唯一；**第一期未开放** | 清单下发的工具集合 |
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
- `skills` 非 git 源缺 `digest`；
- teclaw / desktop bot 写了 `script`；
- **第一期**：`engine_config`、`cli_tools`、`from`（命名源）、git 源。
