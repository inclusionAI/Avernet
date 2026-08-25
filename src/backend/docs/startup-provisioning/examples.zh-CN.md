# Manifest 完整示例与逐块讲解

> 状态：DRAFT（讨论稿）。本文给出**一份完整的置备文档示例**（§1），然后
> 逐块讲解每一段的含义、可选写法与 apply 时平台实际做了什么（§2 起）。
> 规范性定义见 `manifest-schema.zh-CN.md`，设计论证见 `design.zh-CN.md`；
> 本文所引端点、字段、路径均取自现有业务代码。

## 0. 场景设定

某业务团队运营一批「商家客服」bot（openclaw 引擎为主，部分租户在
teclaw）。他们的资产分两类，存放位置也不同：

| 资产 | 内容本性 | 存放位置 |
| --- | --- | --- |
| 人设 md、话术规范、知识库、质检 skill | **文本表达** | 公司 git 服务的内容仓库 `team/content`，以 tag 发版 |
| 内部查询工具 `shopctl`（静态二进制） | **二进制制品** | OSS 制品桶，路径含版本号 |

这条分界不是偶然：文本本来就该被版本管理和评审，git 是它的自然栖息地；
二进制进 git 是反模式（仓库膨胀、LFS 运维），制品库 + 强制 digest 才是
它的供应链通道（schema §1.1）。

**今天的痛点**：每新开一个 bot 或每次内容更新，要按顺序手工调 4~5 个
open API；容器重建后 bot 立即可用但内容可能滞后；scale-out 出来的实例
之间还可能不一致。

**置备文档解决的问题**：写一次，每个 bot 的每次拉起自动收敛到声明的状态。

## 1. 完整示例

### 1.1 前置：一次性注册凭证

两个源都是私有的，**业务方先调用平台的 TC Open API** 注册凭证（方向与
上传 skill、写 identity 相同：业务方 → 平台）。此后每个 apply 点由**平台
携带凭证访问业务方的源站**（平台 → 业务方）——这是平台对业务方唯一的
出向调用。secret 写后不可读回（schema §2.1）。

**一个端点、一种形状**——凭证不区分 git / OSS：它的职责只是「往请求头
注入一个值」，git-ness 属于 `source` 而非凭证。两个源各注册一次：

```text
PUT /openapi/v1/provisioning/credentials/corp-git-content
{
  "header_name": "PRIVATE-TOKEN",
  "secret": "…",                                                   # 仓库级只读 token 或机器人账号 token，不用个人 PAT
  "allowed_prefixes": ["https://code.example-corp.com/team/content"]
}

PUT /openapi/v1/provisioning/credentials/oss-artifacts
{
  "header_name": "Authorization",
  "secret": "Bearer …",
  "allowed_prefixes": ["https://artifacts.example-corp.com/tools/"]
}
```

`allowed_prefixes` **必填**：git 服务和对象存储都是单 origin 承载大量
互不相关的内容，只按域名放行的话，把 `source` 改指同域名下别人的仓库/桶
就能套用这个凭证。前缀把授权粒度收到「这个仓库」「这个桶前缀」，且由
平台校验、不依赖托管服务具备任何能力（schema §2.1）。

secret 落库前用 AES-GCM 加密（复用既有 `TokenVault`，形态
`enc:v1:<密文>`），主密钥存密钥库（Mist）、由 `SecretResolver` 解析；
fetch 前在内存中解密出示、用完即弃。生产环境解析不到主密钥则**拒绝写入
凭证**，绝不明文落库。

### 1.2 置备文档

```yaml
schema_version: 1

sources:                                     # 命名源：一处声明、多处引用
  content:                                   # ① 内容仓库（git）
    git: https://code.example-corp.com/team/content.git
    ref: v1.2.0                              # ← 整套配置升版本只改这一行
    auth: corp-git-content
  artifacts:                                 # ② 制品桶（URL 前缀）
    url: https://artifacts.example-corp.com/tools/
    auth: oss-artifacts

manifest:
  engine_config:                             # ③ 引擎配置：声明的顶层键获胜
    config:
      language: zh-CN
      reply_style: concise

  identity:                                  # ④ 人设/规则文件
    - type: SOUL.md                          # 每个 bot 一份，路径按 bot 变量拼
      from: content
      subpath: bots/${OCB_BOT_ID}/soul.md
    - type: RULES.md                          # 全体共享一份话术规范
      from: content
      subpath: kb/service-rules.md
    - type: SAFETY.md                         # 内联写法：仅用于一次性小片段
      content: |
        # 安全边界
        不承诺退款金额；涉及资损问题一律转人工。

  resources:                                 # ⑤ 工作区资源
    - path: data/faq.csv                     # 单文件：git 取
      from: content
      subpath: kb/faq.csv
      on_fetch_failure: keep_last
    - path: data/kb/                         # 整个目录：git 取，免打包
      from: content
      subpath: kb/
      on_fetch_failure: keep_last

  skills:                                    # ⑥ Local Skills：两种形态并存
    - name: quality-check                    # 形态 A：仓库里的 skill 目录
      from: content
      subpath: skills/quality-check/
    - name: order-lookup                     # 形态 B：OSS 上的 zip 包
      from: artifacts
      subpath: skills/order-lookup-1.4.0.zip
      digest: "sha256:3e7a…"                 # 非 git 形态：强制钉版

  cli_tools:                                 # ⑦ 给模型调用的命令行工具
    - name: shopctl
      from: artifacts
      subpath: shopctl/2.3.0/shopctl-linux-amd64
      digest: "sha256:9f2c…"                 # 本类目强制
      version: "2.3.0"

  mcp:                                       # ⑧ MCP servers：注册表引用
    - server_code: mcp.ant.homistudio.meetmcp

script:                                      # ⑨ 命令式长尾（ARCA 系专属）
  body: |
    #!/bin/bash
    set -euo pipefail
    # 仅沙箱网络可达的内部服务，平台侧 fetch 够不到，只能在容器内取
    curl -fsSL http://inner-ops.example.com/whitelist/today.json \
      -o "$HOME/workspace/data/whitelist.json"
```

### 1.3 这份文档产出的 apply 报告

```json
{
  "apply_id": "ap_01H…", "bot_id": "bot7",
  "trigger": "republish", "result": "SUCCEEDED",
  "sources": [
    {"name": "content", "ref": "v1.2.0", "resolved_sha": "9c1f4ae…"},
    {"name": "artifacts", "url": "https://artifacts.example-corp.com/tools/"}
  ],
  "entries": [
    {"category": "engine_config", "name": "language,reply_style", "action": "updated"},
    {"category": "identity",  "name": "SOUL.md",   "action": "updated",   "from": "content"},
    {"category": "identity",  "name": "RULES.md",  "action": "unchanged", "from": "content"},
    {"category": "identity",  "name": "SAFETY.md", "action": "unchanged", "source": "inline"},
    {"category": "resources", "name": "data/faq.csv", "action": "updated", "from": "content"},
    {"category": "resources", "name": "data/kb/",     "action": "updated", "from": "content"},
    {"category": "skills",    "name": "quality-check", "action": "unchanged", "from": "content"},
    {"category": "skills",    "name": "order-lookup",  "action": "created",
     "from": "artifacts", "source_digest": "sha256:3e7a…"},
    {"category": "cli_tools", "name": "shopctl", "action": "created",
     "from": "artifacts", "source_digest": "sha256:9f2c…", "version": "2.3.0"},
    {"category": "mcp",       "name": "mcp.ant.homistudio.meetmcp", "action": "unchanged"}
  ]
}
```

命名源的解析结果记在顶层：git 源记 `ref` 与解析出的 `resolved_sha`——
「这批 bot 线上跑的是哪一版内容」一眼可查。条目层记 `from`（来自哪个
命名源）、`source_digest`（有 digest 时）、以及 `action`
（`created` / `updated` / `unchanged` / `skipped` / `failed`）。

以下逐块讲解。

## 2. `sources` — 命名源

```yaml
sources:
  content:
    git: https://code.example-corp.com/team/content.git
    ref: v1.2.0
    auth: corp-git-content
  artifacts:
    url: https://artifacts.example-corp.com/tools/
    auth: oss-artifacts
```

**它解决什么**：identity、resources、skills 的内容通常来自同一个仓库的
同一个版本。若逐条目重复写 `{git, ref, auth}`，「升一版」就变成改 N 处，
且可能改漏——半新半旧。命名源把来源提取出来，**一次 `ref` 变更，所有
引用它的条目在同一个 apply 点原子地收敛到同一个 commit**。

**两种源类型**：

- **git 源**（`git` + `ref`）：`ref` 可以是 tag、branch 或 commit SHA。
  收敛单位是**解析出的 commit SHA**，即天然 digest，条目不需要写
  `digest`。`ref` 在每个 apply 点重新解析——tag 被重打即声明含义变化，
  下次 apply 收敛到新内容；要绝对不可变就直接写 SHA；追最新则写 branch。
- **URL 源**（`url`）：作为前缀，条目的 `subpath` 拼在其后。适合 OSS
  制品桶这类「一个前缀下放很多制品」的场景。

**凭证声明在源上**（`auth`），引用它的条目不再各写一遍。

**什么时候不用它**：单条目、跨仓库、一次性来源，直接在条目里写内联
`source`（schema §2.2）即可，两种写法可以在同一份文档里共存。

## 3. `engine_config` — 引擎配置

```yaml
engine_config:
  config:
    language: zh-CN
    reply_style: concise
```

**含义**：这批 bot 统一语言与回复风格，新开 bot 不允许漏配。

**与其他类目的不同**：整类目只有一个对象、不是列表，且**没有 source**
——它的内容就是 manifest 自身（键值本来就是配置，不是需要取回来的文件）。

**合并语义**：**声明的顶层键获胜**，逐键覆盖；未声明的键不碰——managed
边界按顶层键计。所以 `language`、`reply_style` 归 manifest 管，文档里
其他键（引擎或用户后来写入的）一概不动。

**apply 做什么**：读现有配置 → 逐键覆盖 → 经
`EngineConfigServiceProtocol.write_bot_config` 写回。等价于今天手工调
`PUT /openapi/v1/bots/{bot_id}/engine-config`。

**交付**：两个引擎家族走同一条 provider-blind 写路径——分派器把 teclaw
的落点解析为逻辑路径 `config/teclaw.json`，经 `TeclawDeviceFileSystem`
转发到引擎的 `/api/v1/file/upload`。artifact **不为此类目新增或启用任何
字段**（`engine_overrides` 保持不用）；待确认的只是新建 bot 场景下该文件
到达首个实例的时序（T3）。

**边界**：`engine_ext` 是引擎自有的不透明数据（平台承诺「存储原样、永不
解释」），**manifest 永远不能触碰它**。

## 4. `identity` — 人设/规则文件

```yaml
identity:
  - type: SOUL.md
    from: content
    subpath: bots/${OCB_BOT_ID}/soul.md
  - type: RULES.md
    from: content
    subpath: kb/service-rules.md
  - type: SAFETY.md
    content: |
      # 安全边界
      不承诺退款金额；涉及资损问题一律转人工。
```

**含义**：人设集中运营——SOUL.md 每个 bot 一份（用平台注入变量
`${OCB_BOT_ID}` 拼源内路径），RULES.md 全体共享一份，SAFETY.md 是三行
红线。

**`type` 是白名单枚举**，不是自由命名：RULES / OKR / SAFETY / SOUL /
OUTPUT / MEMORY / IDENTITY / AGENTS / USER / TOOLS / HEARTBEAT /
BOOTSTRAP / KNOWLEDGE / CLAUDE / GREETING / README（物理文件为
`<type>.md`）。**claude_code 引擎的 bot 仅允许 `CLAUDE.md`**，写入时按
bot 当前引擎校验并明确报错，而不是 apply 时静默跳过。

### 4.1 两种写法：`source` 取源 vs `content` 内联

| 写法 | 何时用 | 代价 |
| --- | --- | --- |
| **取源**（`from`+`subpath`，或内联 `source`）——**推荐** | 常规内容：需要版本管理、评审、多 bot 共享、会持续演进的文件 | 需要在仓库里有对应文件 |
| **内联**（`content`） | per-bot 一次性小片段：为一句话在仓库开文件成本过高时 | 内容**游离于版本控制之外**——改它要改配置而不是改内容仓库；久了会变成「藏在配置里的第二份人设」，排查「这句话哪来的」要看两个地方 |

示例里 SAFETY.md 用内联，只是为了展示这种写法存在；**真实项目建议一律
走取源**——统一的取源意味着所有内容都有版本、可评审、单一真相源。

内联用的是 YAML 标准的 block scalar（`|` 保留换行、`>` 折叠换行），不是
自定义语法。但它对缩进敏感，内容里若有以 `#` 开头的行或不规则缩进容易
误写，这也是长内容不适合内联的工程理由。

内联条目没有 fetch 环节，因此 `auth` / `digest` / `on_fetch_failure`
对它一律非法（写了报错）；apply 报告里记 `"source": "inline"`。

**apply 做什么**：逐条取内容（或读内联）→ 经现有 IdentityService 写入。
因为走的是同一服务，现有派生行为原样生效——例如 REFERENCE_FILES
（RULES/OKR/SAFETY/OUTPUT）向 AGENTS.md 的同步，manifest 用户无需知道
这个机制的存在。

**交付**：ARCA 系走现有 identity 交付（openclaw 落在 `…/workspace/` 下，
路径差异由 `path_factory` 现状处理）；teclaw 物化后以
`FileRef{name, store, path}` 进 `identity_files[]`。

**提醒**：MEMORY.md 等引擎运行期生成的文件技术上可声明，但声明获胜语义
会在每个 apply 点重置它们——人设/规则类才是本类目的主场景。

## 5. `resources` — 工作区资源（单文件与目录）

```yaml
resources:
  - path: data/faq.csv          # 单文件
    from: content
    subpath: kb/faq.csv
    on_fetch_failure: keep_last
  - path: data/kb/              # 目录（path 以 / 结尾）
    from: content
    subpath: kb/
    on_fetch_failure: keep_last
```

**两个「路径」不要混淆**：`path` 是**落点**（写到 workspace 的哪里，
逻辑路径、非引擎物理路径）；`subpath` 是**源内路径**（从源的哪里取）。
二者可同时出现在一个条目里，故必须异名。

**目录条目**：`path` 以 `/` 结尾即目录条目。**HTTP 没有目录语义**——
一个 URL 只能是一个字节流，所以「文件夹」要么用带目录枚举能力的协议
（git，枚举由仓库服务完成，**免打包**），要么以归档为约定形态整体运输：

```yaml
  # 归档形态（源不在 git 时）
  - path: data/kb/
    source: https://cms.example.com/kb/knowledge-base.zip
    unpack: zip                 # 可选：扩展名不可靠时显式指定
    strip_components: 1         # 可选，默认 0：剥掉归档内前 N 层目录
                                # （业务 `zip -r kb.zip kb/` 的壳目录用它消掉）
    auth: cms-token
```

**目录条目的语义**：
- **收敛单位是整棵树**（git 形态即 commit SHA，归档形态即归档内容
  hash）：未变 → `unchanged` 零动作，几百个文件也不逐一比对；
- **目录级声明获胜**：`path` 下整棵树归 manifest 管辖——源中不存在的
  文件在 apply 时被清除（含手工添加的）；temp 目录物化 + 原子 rename，
  无半新半旧的中间态；目录之外完全不碰；
- **嵌套禁止**：任何条目的 `path` 不得位于另一个目录条目之下（所有权
  无法定义），PUT 时拒绝；
- **权限拍平**：源里的可执行位不保留——可执行物必须走 `cli_tools`；
- **`strip_components` 不做魔法**：只按声明的层数剥，**不**自动探测单一
  顶层目录，同一份声明的行为不取决于归档内部长什么样。

**`on_fetch_failure`**：`keep_last`（默认，源站抖动时沿用上一次成功的
版本，不阻塞 bot 拉起）/ `skip` / `fail`。

**交付**：ARCA 系写入 bot 工作区（NAS 持久，重建即见）；teclaw 物化进
bot-data store 后以 `ResourceRef{name, store, path}` 进 artifact——目录
条目逐文件展开，与今天手工上传的资源在 artifact 里**无法区分**（子树
引用为可选优化，见 T5）。

## 6. `skills` — Local Skills（两种形态）

```yaml
skills:
  - name: quality-check         # 形态 A：仓库里的 skill 目录
    from: content
    subpath: skills/quality-check/
  - name: order-lookup          # 形态 B：OSS 上的 zip 包
    from: artifacts
    subpath: skills/order-lookup-1.4.0.zip
    digest: "sha256:3e7a…"
```

**`name` 是标识符，不含位置信息**——skill 装到引擎的哪个目录由引擎决定
（openclaw 与 claude_code 的 skills 目录本就不同），用户不感知。

**形态 A（仓库目录）**：skill 本质是文本包（`SKILL.md` + 脚本），放在
内容仓库里最自然——和 identity、resources 共享同一个 tag，一起升版本。
收敛靠 commit SHA，不需要 `digest`。

**形态 B（OSS zip）**：适合由构建流水线产出 zip 制品的 skill。**此形态
`digest` 强制**——skill 里有会被 agent 加载执行的脚本，属于「代码」而非
「数据」；git 形态有 commit SHA 天然兜底，OSS 形态没有钉子就等于每次
apply 盲取最新。（相比之下 resources 的归档不强制 digest：那是数据，
`keep_last` 兜底足够。）

**归档自动识别**：平台按内容类型/扩展名判定是否需要解包，`unpack` 只在
扩展名不可靠时作为显式覆盖。两种形态下用户声明的都是「我要这个 skill」，
怎么取回来是平台的事。

**apply 做什么**：取内容 → （zip 形态）digest 校验 → 走现有 upload 服务
（`created` / `updated` 语义照旧）→ activate（同步 reconcile 运行时）。
`unchanged` 时零动作——skills 是体量最大的类目，收敛比对避免每次拉起
重传。

**与 skills-pool reconcile 的关系**：因为 apply 走的就是正规 upload +
activate，落成的 Local Skill 对 reconcile / quarantine 完全可见——不存在
「侧载目录被当 drift 清掉」的问题。这是「apply 落成平台实体」相对「脚本
直接写文件系统」的决定性优势。

**交付**：ARCA 系照现有 skill 交付；teclaw 物化进 bot-data store 后以
`SkillRef{name, scope="user", store, path}` 进 artifact，路径形状即现状的
`…/workspace/skills/skills-local/<name>`。

## 7. `cli_tools` — 给模型调用的命令行工具

```yaml
cli_tools:
  - name: shopctl
    from: artifacts
    subpath: shopctl/2.3.0/shopctl-linux-amd64
    digest: "sha256:9f2c…"
    version: "2.3.0"
```

**含义**：内部数据查询 CLI，希望每个客服 bot 里都有，agent 处理工单时
自己 bash 调它查订单。

**为什么放 OSS 而不是 git**：这是唯一**不建议进 git** 的类目。二进制进
git 是反模式（仓库膨胀、LFS 运维），而可执行物需要的是 digest 钉死的
供应链通道——制品库（OSS）+ 强制 `digest` 才是它的形态。路径里带版本号
（`shopctl/2.3.0/…`）让制品不可变，配合 digest 双保险。

**平台做什么（三件事）**：取二进制 → **digest 强校验**（不符即 `failed`）
→ 落进平台定义的逻辑「工具目录」（NAS 持久）+ 置可执行位 → 保证该目录
在 agent 进程的 PATH 上。平台的承诺到此为止，就一句话：**`shopctl`
这个命令敲得到**。

**用户做什么（模型怎么会用）**：平台不解析工具、不向模型宣告任何东西。
「知道有这个工具、什么时候用、参数怎么传」是内容问题，走 `skills`（配一个
教用法的 skill）或 `identity`（写 `TOOLS.md`）。

背景：模型不会「查看 PATH」。它敢直接写 `jq`、`git` 这类命令，是因为
训练先验告诉它标准环境里通常有——平台把知名工具放上 PATH，等于让这个
赌注成真。但**私有工具（如 `shopctl`）不在模型的先验里**，它不会凭空
敲一个没见过的命令名，`which shopctl` 也不会去试（探测的前提是先知道
名字）。所以私有工具的「存在性」只能靠上下文注入——这就是配套 skill /
TOOLS.md 的必要性根源。仓库里的 `bcs-cli` 正是这个双件套的现成先例
（二进制挂 PATH + `SKILL.md` 教用法），本类目是它的产品化。

**v1 范围**：静态二进制、压缩包（含 `entrypoints` 声明包内哪些文件暴露
为命令）。需要跑包管理器（npm/pip/apt）的安装属命令式领域，走 script
（ARCA-only）——与「机制层操作不进 manifest」同一条原则。

**能力**：ARCA 系支持（PATH 注入点见 A2）；**teclaw 待确认（T4）**——
可执行位、PATH 注入、以及对用户提供二进制的沙箱策略，与 script 的能力
边界相邻，须 teclaw 表态。

## 8. `mcp` — MCP servers

```yaml
mcp:
  - server_code: mcp.ant.homistudio.meetmcp
```

**含义**：让这批 bot 都带上会议信息能力。

**只接受注册表引用**，不接受任意 URL——`server_code` 指向平台 MCP 注册表
（`mcp.ant.homistudio.meetmcp` 是注册表真实条目）。**凭证永不出现在
manifest**：需要 `api_key` 的 server，其配置仍走现有统一配置存储；若必需
配置缺失，该条目记 `failed` 并给出明确错误（「server X 需要先配置
api_key」），不影响其余条目。

**apply 做什么**：校验 `server_code` 存在于注册表且租户有权限（复用现有
`check_mcp_permission` 路径）→ 确保它在该 bot 的 MCP 集合中。

**交付**：ARCA 系走现有按-MCP 推送（设备 `/api/mcp` 路径）；teclaw 在
artifact 组装时进入 `mcp.servers[]`，凭证按现状于 compose 时从平台配置
解出并内联——既有机制，本设计不触碰。

## 9. `script` — 命令式长尾（ARCA 系专属）

```yaml
script:
  body: |
    #!/bin/bash
    set -euo pipefail
    curl -fsSL http://inner-ops.example.com/whitelist/today.json \
      -o "$HOME/workspace/data/whitelist.json"
```

**为什么这段不能声明化**：当日商家白名单在一个**仅沙箱网络可达**的内部
运维服务上——平台侧 fetch 够不到。这正是声明式无法吸收的残留：需要在
容器内、拉起时才能取到的内容。

**执行语义**（全部为 #935 现状，本设计不改动）：以 `admin` 身份、300s
超时、base64 免注入封装、输出在容器内
`/home/admin/logs/startup_script.log`、退出码不影响平台就绪判定、
**体内不得有密**（下发链路日志可见）。

**顺序保证**（本设计新增的唯一承诺）：script 执行时，manifest 声明的
实体**已经就位**——上例中脚本可以放心假定 `data/faq.csv`、`data/kb/`、
`quality-check` skill、`shopctl` 命令都已存在。

**能力边界**：teclaw / desktop / LOCAL / ARCA-direct 遗留形态在 `PUT` 时
即拒绝（fail closed），错误信息指明原因——业务在建 bot 时就知道该租户能
不能用 script，而不是启动后静默不执行。

## 10. 全文对照表

| 块 | 声明什么 | 来源形态 | apply 动作 | teclaw 落点 |
| --- | --- | --- | --- | --- |
| `sources` | 命名源 | git（ref→SHA）/ URL 前缀 | 解析 ref、缓存拉取 | ——（编译期概念） |
| `engine_config` | 引擎配置键值 | 无（内联键值） | 声明键逐键覆盖 → provider-blind 写 | 既有 `config/teclaw.json` 文件通道（非 artifact 字段，T3） |
| `identity` | 人设/规则文件 | 取源 **或** 内联 `content` | 取内容 → IdentityService 写 | `identity_files[]` |
| `resources`（文件） | 工作区单文件 | 取源 | 取内容 → 资源写路径 | `resources[]` |
| `resources`（目录） | 工作区整棵树 | git（免打包）/ 归档 | 物化 → 守卫 → 原子整树替换 | `resources[]`（逐文件展开；T5） |
| `skills` | Local Skill | git 目录 / OSS zip（digest 强制） | 取内容 → upload → activate | `skills[]`（scope=user） |
| `cli_tools` | 模型可调命令 | OSS 制品（digest 强制） | digest 强校验 → 工具目录 + PATH | 待确认（T4） |
| `mcp` | MCP server | 注册表引用 | 权限校验 + 入 bot MCP 集合 | `mcp.servers[]` |
| `script` | 容器内命令式逻辑 | 内联 body | #935 启动链现状 | ——（不支持） |

「apply 动作」列的新机制只有三个：guarded fetcher（含 git 归档拉取）、
归档解包、工具目录 + PATH 注入；其余全部是对现有服务的编排——每一步都
等价于一次今天已经存在的 open API 调用。这是本设计的实现面与说服点。
