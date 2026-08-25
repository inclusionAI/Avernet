# Manifest Schema v1（草案）

> 状态：DRAFT（讨论稿）。设计论证见 `design.zh-CN.md`；每类配置的完整业务
> 案例见 `examples.zh-CN.md`。本文只定义文档形状、校验规则与到各引擎的
> 映射。字段名以定稿评审为准。

## 1. 顶层结构

置备文档（经 `PUT /openapi/v1/bots/{bot_id}/provisioning` 写入）：

```yaml
schema_version: 1

sources:                       # 命名源（可选，§2.3）：一处声明、多处引用
  content:
    git: https://code.example-corp.com/team/content.git
    ref: v1.2.0
    auth: corp-git-content

manifest:                      # 声明式部分，所有引擎
  mcp: [ … ]                   # §3.1
  resources: [ … ]             # §3.2（含文件与目录两种条目形态）
  skills: [ … ]                # §3.3
  engine_config: { … }         # §3.4
  identity: [ … ]              # §3.5
  cli_tools: [ … ]             # §3.7（schema 已定稿，交付排期按业务优先级后置）

script:                        # 命令式部分，能力门控（teclaw / desktop 拒绝）
  body: |                      # §3.6，即 #935 的 startup script
    #!/bin/bash
    …
```

三段均可缺省。`manifest` 六个类别均可缺省，缺省的类别不参与 apply
（不碰任何实体）。**类别存在但为空列表（`skills: []`）含义不同**：声明
「该类别下不应有 managed 实体」，会把此前 apply 落成的 managed 实体摘除
标记（不删资产，语义同 DELETE，见 design §6）。

### 1.1 内容归属：文本进 git，制品进制品库

六个类别按内容本性分成两组，这条线决定了各自的来源形态：

| 类别 | 内容本性 | 典型来源 |
| --- | --- | --- |
| identity / skills / resources | **文本表达**（md、SKILL.md、csv/json…） | git（§2.2/§2.3）；也可 URL |
| engine_config / mcp | 文本，但**内联在 manifest 内**（键值 / 注册表引用） | 无 source——它们的「文本」就是置备文档自身 |
| cli_tools | **二进制制品** | URL + 强制 `digest`（§3.7） |

`cli_tools` 是唯一的例外，且是原则性的：**git 管表达，制品库管产物**。
二进制进 git 是反模式（仓库膨胀、LFS 运维），而可执行物需要的是 digest
钉死的供应链通道。其余五类没有这个张力——它们本来就该被版本管理、被
评审，git 是它们的自然栖息地。

## 2. 条目通用字段

内容型条目（resources / skills / identity / cli_tools）的来源四选一，互斥：

| 字段 | 说明 |
| --- | --- |
| `from` + `subpath` | 引用一个**命名源**（§2.3）并取其中某个子路径。多类目共用同一仓库同一版本时的推荐写法 |
| `source` | 内联来源。两种形态：**HTTPS URL**（字符串），或**git 引用**（结构化对象，§2.2）。由平台在 apply 点经 guarded fetcher 拉取（design §4），支持变量替换（§4） |
| `content` | 内联 UTF-8 文本（YAML block scalar）。**不推荐**：内容游离于版本控制之外，仅用于 per-bot 一次性小片段；常规内容一律走取源。内联条目无 fetch 环节，`auth` / `digest` / `on_fetch_failure` 对它非法 |
| （注册项引用） | 仅特定类别：MCP 的 `server_code`；v2 的 `center://` skill 引用 |

通用可选字段：

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `subpath` | 无 | **源内路径**：命名源/git 仓库内、或归档内的子目录或文件。缺省 = 源的根 |
| `digest` | 无 | `sha256:…`。校验 fetch 内容，不匹配按 fetch 失败处理（钉扎可复现）。**仅适用于 URL 源**——git 源以 commit SHA 为天然 digest，写了报错 |
| `auth` | 无 | 租户级命名凭证的引用（§2.1）；仅对内联 `source` 有效（命名源的凭证声明在源上）。fetch 时注入为请求头 |
| `on_fetch_failure` | `keep_last` | `keep_last` / `skip` / `fail`（design §4.3） |
| `apply_once` | —— | **v1 保留字，拒绝写入**；v2 语义见 design §3.2 |

### 2.0 字段命名规范

条目字段分两组，**一组必须横向一致，一组刻意各不相同**：

- **来源侧字段横向强一致**：`from` / `subpath` / `source` / `content` /
  `auth` / `digest` / `on_fetch_failure` 在所有类目里拼写、语义、默认值
  一字不差——它们是同一套取数机器，不一致即缺陷。
- **实体键字段按实体本性各归各名**，字段名本身承载契约信息：
  - `identity.type`（`SOUL.md`）——值域是白名单枚举，**选而非造**；
  - `skills.name` / `cli_tools.name`——**标识符**，不含位置信息，装到
    哪里由引擎决定；
  - `resources.path`——**工作区位置**，且只有 resources 有权指定位置。

  强行统一成一个词会抹掉这条线：skills 若叫 `path`，用户会以为自己在
  选安装路径——路径感知就从字段名这个后门漏回去了。**这里的不一致是
  文档的一部分。**

注意区分两个「路径」：`resources.path` 是**落点**（写到 workspace 哪里），
`subpath` 是**源内路径**（从源的哪里取）。二者可同时出现在一个 resources
条目里，故必须异名。

### 2.1 凭证引用 `auth`

私有源的鉴权走**引用**，secret 永不出现在 manifest 里（设计论证与安全
规则见 design §4.5）。凭证是租户级命名对象，一次性写入。**名字
（URL 中的 `{name}`）是自由标识符**，`auth` 按它做字典查找取出凭证对象；
名字与 `allowed_prefixes` 里的域名之间不存在任何字符串匹配或推导关系。

**一个端点、一种形状**——凭证不区分 git / URL / OSS：它的全部职责是「往
请求头注入一个值」，而 git 源我们调托管服务的 HTTP API、URL 源发普通
GET，注入动作完全相同。**git-ness 属于 `source`，不属于凭证。**

```text
PUT /openapi/v1/provisioning/credentials/corp-git-content
{
  "header_name": "PRIVATE-TOKEN",                                  # 按托管服务定（O11）
  "secret": "…",                                                   # 仓库级只读 token / 机器人账号 token
  "allowed_prefixes": ["https://code.example-corp.com/team/content"]
}

PUT /openapi/v1/provisioning/credentials/oss-artifacts
{
  "header_name": "Authorization",
  "secret": "Bearer …",
  "allowed_prefixes": ["https://artifacts.example-corp.com/tools/"]
}
```

manifest 条目按名字引用（`auth:`），或声明在命名源上（§2.3）。

#### `allowed_prefixes`：凭证可被出示给谁

**必填，至少一项**，每项是绝对 https URL 前缀。fetch 前校验目标 URL 落在
某个前缀之下，否则该条目 `failed`——**不降级为「不带凭证继续请求」**
（静默降级会把配置错误或攻击企图伪装成 401，或在源站恰好允许匿名时掩盖
过去）。跨前缀重定向同样直接失败，凭证不会被重定向带走。

动机：git 托管服务与对象存储都是**单 origin 承载大量互不相关的内容**。
只按 origin 放行时，manifest 编辑者把 `source` 改指同 origin 下别人的
仓库/桶即可套用凭证——若 token 权限宽，即横向越权。前缀把授权粒度收到
「仓库」「桶前缀」这一层，且**由平台校验，不依赖托管服务具备任何能力**。

匹配规则（必须按**路径段边界**比较，否则前缀匹配本身就是漏洞）：目标
URL 规范化后，须等于前缀、或以「前缀 + `/`」开头——前缀
`…/team/content` **不得**匹配 `…/team/content-secret`。git 源比较仓库
URL（忽略可选的 `.git` 后缀），URL 源比较完整目标 URL。

想覆盖整个 origin 就显式写 `https://host/`——这是一个明确选择，不是默认。

#### secret 的存储与主密钥托管

**必须可逆加密，不是哈希**：密码存储用哈希（单向、只需验证），而这里的
用途是**代表用户去出示 token**，必须能还原明文。

复用仓库既有实现，不新建加密方案：

| 需要什么 | 复用什么 |
| --- | --- |
| 加解密原语 | `utils/secret_utils.py` 的 `symmetric_encrypt/decrypt`（AES-GCM，SHA-256 派生 key，随机 nonce） |
| 落库封装 | `core/bot_management/token_vault.py` 的 `TokenVault`——既有用途正是「外部平台 token 落库前加密」，注释明说与具体平台无关 |
| 主密钥托管 | `SecretResolver`：企业环境从密钥库（Mist registry）解析；singlebox/CI 用 `LocalSecretResolver` |

落库形态 `enc:v1:<AES-GCM 密文>`——`TokenVault` 的既有前缀设计，读端可
区分新密文与存量明文（零迁移），将来换算法可升 `v2`。

**一条本场景必须新增的守卫**：`TokenVault` 在 master_key 为空时明文直落
（为本地联调，与 `outbound_rules` 单 box 同形）。这对 provisioning 凭证
在**生产环境绝不可接受**——生产 profile 下解析不到主密钥必须**拒绝写入
凭证**（fail closed），而不是静默明文存。否则一次密钥库配置疏忽，全租户
的 git token 就明文躺在 DB 里。

其余边界：**写后不可读回**（GET 只返回掩码元数据 `has_secret` /
`header_name` / `allowed_prefixes` / `updated_at`）；日志、apply report、
错误信息只出现凭证**名**，永不出现值；解密只发生在 fetch 前的内存中，
用完即弃。

#### 校验与行为

- `auth` 引用的凭证不存在 → PUT manifest 时警告、apply 时该条目 `failed`；
- 目标 URL 不落在 `allowed_prefixes` 内 → 条目 `failed`；跨前缀重定向直接失败；
- 轮换 = 重 PUT 同名凭证，下一个 apply 点生效，不触发 apply；
- 删除仍被引用的凭证 → 引用条目在下次 apply `failed`（「credential X 不存在」）。

v1 仅支持请求头注入；query 参数型、mTLS 见开放问题 O8。

token 选型（与 `allowed_prefixes` 叠加的纵深防御）：**首选仓库级/桶级
只读 token**（类 GitLab 的 Project/Deploy Token，天生单仓库有效）；托管
服务不支持时，**用机器人账号的 token**（账号只授予内容仓库的只读成员
权限，以成员关系收权）；**不使用个人 PAT**——权限面是个人全量可见仓库，
且生命周期绑定个人（转岗/离职即断）。托管服务的具体能力见 O11。

### 2.2 git 源

业务内容托管在公司 git 服务（类 GitLab）上、以 tag 管理版本时，`source`
写结构化 git 引用，**对所有带 source 的类目统一可用**：

```yaml
resources:
  - path: data/kb/                        # 落点：workspace 相对
    source:
      git: https://code.example-corp.com/team/content.git
      ref: v1.2.0                         # tag / branch / commit SHA
      subpath: kb/                        # 源内路径：仓库内子目录或文件，缺省 = 仓库根
    auth: corp-git-content                # 凭证引用（§2.1）

identity:
  - type: SOUL.md
    source:
      git: https://code.example-corp.com/team/content.git
      ref: v1.2.0
      subpath: bots/${OCB_BOT_ID}/soul.md # 变量替换照常可用
    auth: corp-git-content
```

（多个条目引用同一仓库同一 `ref` 时，改用命名源写法更短且升版本只改一处，
见 §2.3。）

语义：

- **收敛单位 = `ref` 解析出的 commit SHA**，即 git 源的天然 digest（条目
  `digest` 字段不适用）。apply report 同时记声明的 `ref` 与解析出的 SHA，
  审计线上版本。
- **`ref` 每个 apply 点重新解析**：tag 被重打 → 下次 apply 收敛到新内容
  （动 tag 即改声明的含义，声明获胜语义的自然延伸）；要绝对不可变，
  `ref` 直接写 SHA；追最新则写 branch。
- **目录条目免打包**——枚举由仓库服务完成，这是「文件夹语义」的原生
  形态；zip/HTTP 形态保留给非 git 源。
- 同一 `{git, ref}` 被多个条目引用时，单次 apply 只拉取一次（按解析后
  SHA 缓存）。
- 落地后的全部语义（目录级 managed、原子替换、嵌套禁止、权限拍平、
  teclaw 逐文件展开）与 §3.2 完全一致——git 只是传输形态。

实现口径（backend 内部，见 design §10.5）：优先走托管服务的 HTTP API
（ref 解析 + 按 ref/子目录取归档），把 git 源编译为「一次 HTTPS 归档
拉取 + 解包」，复用 guarded fetcher 与归档管线；不在后端进程里跑
`git clone`。API 能力确认见 O11。

### 2.3 命名源 `sources` 与 `from`

多个类目的内容通常来自**同一个仓库的同一个版本**（identity、skills、
resources 都在业务的内容仓库里）。逐条目重复写 `{git, ref, auth}` 会让
「升一版」变成改 N 处、且可能改漏——半新半旧。顶层 `sources` 段把来源
提取为命名对象，条目用 `from` 引用：

```yaml
sources:
  content:                                  # 源名：自由标识符
    git: https://code.example-corp.com/team/content.git
    ref: v1.2.0                             # ← 整套配置升版本只改这一行
    auth: corp-git-content
  public-assets:                            # URL 源同样可命名
    url: https://cdn.example.com/assets/
    auth: cdn-token

manifest:
  identity:
    - type: SOUL.md
      from: content
      subpath: bots/${OCB_BOT_ID}/soul.md
  skills:
    - name: quality-check
      from: content
      subpath: skills/quality-check/
  resources:
    - path: data/kb/                        # 落点
      from: content
      subpath: kb/                          # 源内路径
```

规则：

- **原子升版**：一次 `ref` 变更，所有引用该源的条目在同一个 apply 点一起
  收敛到同一个 commit——不存在「identity 升了、skills 没跟上」的错位。
- `from` 与内联 `source` 互斥；引用不存在的源名 → PUT 时拒绝。
- 凭证声明在**源**上（`sources.<name>.auth`），条目不再写 `auth`。
- 命名源被引用零次不报错（允许先声明后使用），但会在 PUT 响应里提示。
- URL 源的 `url` 作为前缀，条目的 `subpath` 拼在其后；git 源的 `subpath`
  为仓库内路径。拼接前后均施加路径穿越校验。
- 内联 `source` 写法**保留**：单条目、跨仓库、一次性来源仍可直接写。

## 3. 类别定义

各类别的映射一览（详见各小节）：

| 类别 | 平台实体（apply 落点） | ARCA 系交付 | teclaw 交付 |
| --- | --- | --- | --- |
| mcp | MCP 启用配置（现 `openapi_v1/mcp` 同源服务） | 现有 per-MCP push（`/api/mcp`） | `mcp.servers[]`（`McpServerRef`，凭证 compose 时内联，现状机制） |
| resources | resource 记录（现 `openapi_v1/resources` 同源服务） | 现有 resource 交付 | `resources[]`（`ResourceRef {store,path}`） |
| skills | 本地 skill 记录（现 skills upload 同源服务） | 现有 skill 交付 / NAS | `skills[]`（`SkillRef, scope=user`） |
| engine_config | engine config（`EngineConfigServiceProtocol.write_bot_config`） | 现有 provider-blind 写 | 同一条 provider-blind 写（既有 `config/teclaw.json` 文件通道，**非 artifact 字段**；创建时序确认 T3） |
| identity | identity 文件记录（现 `openapi_v1/identity` 同源服务） | 现有 identity 交付 | `identity_files[]`（`FileRef`） |
| cli_tools | **新实体**（无现状对应） | 平台工具目录（NAS）+ PATH 注入 | **待确认（T4）**：可执行位 + PATH + 沙箱策略 |
| script | script 存储（#935 现状） | `after_create_cmd_hook` 启动链 | **不支持，写入时拒绝** |

### 3.1 `mcp` — MCP servers

```yaml
mcp:
  - server_code: github          # 平台 MCP 注册表引用（必填）
    config: { … }                # 可选，per-bot 配置，形状同现有 MCP config API
```

- 只接受注册表引用；**凭证永不出现在 manifest**（design §4.5）。
- 校验：`server_code` 必须存在于注册表且租户有权限（复用现有
  `check_mcp_permission` 逻辑）；apply 动作等价于现有「启用 + 配置」API。

### 3.2 `resources` — workspace 资源文件

条目分**文件**与**目录**两种形态，`path` 以 `/` 结尾即目录条目：

```yaml
resources:
  # 文件条目
  - path: data/sales.csv         # workspace 相对路径（必填）
    source: https://my-svc.example.com/data/sales.csv

  # 目录条目（归档形态）：source 为归档，内容按相对层次展开到 path 之下
  - path: data/kb/
    source: https://my-svc.example.com/kb/knowledge-base.zip
    unpack: zip                  # zip | tar.gz（归档形态必填）
    strip_components: 1          # 可选，默认 0：剥掉归档内的前 N 层目录
                                 # （语义同 tar --strip-components；业务用
                                 #  `zip -r kb.zip kb/` 打包出的壳目录用它消掉）

  # 目录条目（git 形态）：免打包，无需 unpack/strip_components
  - path: data/kb/
    from: content                # 命名源（§2.3）
    subpath: kb/                 # 源内路径
```

**共同规则**：
- `path` 是 workspace 相对的**逻辑路径**，不是引擎物理路径；寻址语义与
  现有 resources API 一致，物理位置由各引擎照现状决定。
- 校验：白名单字符、禁止路径穿越（`../`、绝对路径）。

**目录条目语义**（HTTP 没有目录语义，归档是把树运过来的约定形态）：
- **收敛单位是整个归档**：内容 hash 未变 → `unchanged`、零动作（不做
  逐文件比对）；变化 → 整目录替换。`digest` 仍为可选的钉版手段。
- **目录级声明获胜**：`path` 下整棵树归 manifest 管辖——归档中不存在的
  文件在 apply 时被清除（含手工添加的）；temp 目录解包 + 原子 rename，
  无半新半旧的中间态。目录之外不碰。
- **`strip_components` 不做魔法**：只按声明的层数剥，**不**自动探测单一
  顶层目录——同一份声明的行为不取决于归档内部长什么样。
- **嵌套禁止**：任何条目的 `path` 不得位于另一个目录条目之下（目录归
  manifest、内部文件又单独声明的所有权无法定义），PUT 时拒绝。
- **解包守卫**沿用 skills zip 的现成规则：路径穿越、绝对路径、symlink
  逃逸、文件数与总大小上限（§5）。**权限拍平为普通文件**——归档内的可
  执行位不保留，可执行物必须走 `cli_tools`（§3.7）。
- **teclaw**：物化后逐文件展开为 `ResourceRef`，artifact 契约零改动；
  `ResourceRef` 直接引用目录子树（`SkillRef` 已有目录先例）为可选优化，
  见确认项 T5。
- 目录条目的另一传输形态是 **git 源**（§2.2，免打包，业务内容在 git 上
  时优先）；索引文件、对象存储前缀列 v2 候选（design §9）。

### 3.3 `skills` — local skills

两种来源形态并存：

```yaml
skills:
  - name: quality-check          # 形态 A：git 仓库里的 skill 目录，免打包
    from: content
    subpath: skills/quality-check/
  - name: order-lookup           # 形态 B：制品库上的 zip 包
    from: artifacts
    subpath: skills/order-lookup-1.4.0.zip
    digest: "sha256:3e7a…"       # 非 git 形态：强制
```

- 语义等价于现有 `POST /openapi/v1/bots/skills/upload`（zip 校验、大小限制
  复用现状）+ activate。git/命名源形态取到的是 skill **目录**，平台在
  物化后按同一路径入库，与 zip 上传殊途同归。
- **非 git 形态 `digest` 强制**：skill 含会被 agent 加载执行的脚本，属
  「代码」而非「数据」；git 形态有 commit SHA 天然兜底，URL/制品库形态
  无钉子即等于每个 apply 点盲取最新。（resources 的归档不强制——那是
  数据，`keep_last` 兜底足够。）
- **归档自动识别**：平台按内容类型/扩展名判定是否需要解包，`unpack` 仅在
  扩展名不可靠时作为显式覆盖。两种形态下用户声明的都是「我要这个
  skill」，怎么取回来是平台的事。
- teclaw：物化进 bot-data store 后以 `SkillRef(scope="user")` 进 artifact，
  与今天手工 upload 的 skill 走完全相同的路。
- v2 预留来源：`source: center://<skill_uuid>@<version>`（skill center 引用，
  不经 fetch，直接引 store）。

### 3.4 `engine_config` — 引擎配置

```yaml
engine_config:
  config:                        # 键值对象，形状同现有 engine-config API
    model: …
    …
```

- **整类别只有一个对象**，不是列表。合并语义：**声明的顶层键获胜**（逐键
  覆盖），未声明的键不碰——managed 边界按顶层键计。
- 明确排除：`engine_ext` 是引擎自有的不透明数据（平台承诺「存储原样、永不
  解释」），**manifest 永远不能触碰它**。
- 校验：形状校验沿用现有 engine-config 写路径；引擎相关的键合法性由该路径
  的现状规则负责。

### 3.5 `identity` — identity 文件

```yaml
identity:
  - type: SOUL.md                # 必须属于该引擎的合法 identity 文件集
    source: https://my-svc.example.com/bots/${OCB_BOT_ID}/soul.md
  - type: RULES.md
    content: |                   # 小文件可内联
      # 团队规范
      …
```

- `type` 合法集按引擎校验（`core/services/identity.py` 现状）：通用集为
  `VALID_IDENTITY_FILES`（RULES/OKR/SAFETY/SOUL/OUTPUT/MEMORY/IDENTITY/
  AGENTS/USER/TOOLS/HEARTBEAT/BOOTSTRAP/KNOWLEDGE/CLAUDE/GREETING/README
  .md）；**claude_code 引擎仅允许 `CLAUDE.md`**。写入时按 bot 当前引擎
  校验并明确报错，而不是 apply 时静默跳过。
- 引擎**生成**的文件（MEMORY.md 等运行期状态）不建议声明——声明获胜语义
  会在每个 apply 点重置它们；文档如实警示，不做硬禁止（SOUL.md 等人设文件
  正是主场景）。

### 3.6 `script` — 启动脚本

```yaml
script:
  body: |
    #!/bin/bash
    set -euo pipefail
    curl -fsSL https://internal.example.com/setup.sh | bash
```

- 即 #935 的 startup script，全部现状约束不变：≤ `MAX_SCRIPT_BYTES`
  （24 KiB）、以 `admin` 身份执行、300s 超时、输出仅在容器日志、
  **体内无密**（下发链路日志可见）、退出码不影响平台就绪判定。
- 顺序保证：在 manifest 实体交付完成后执行（design §3.4），脚本可以假定
  声明的 skill / identity 已就位。
- 能力：ARCA 系支持；teclaw、desktop、LOCAL/singlebox、ARCA-direct 遗留
  形态写入时拒绝（fail closed，见 `engine-requirements.zh-CN.md` 矩阵）。

### 3.7 `cli_tools` — 给模型调用的命令行工具

> schema 已定稿；**交付排期按业务优先级后置**（业务反馈优先级低于目录
> 资源）。设计动机：把仓库内 `bcs-cli` 的手工模式（二进制挂 PATH +
> SKILL.md 教用法）产品化、声明化。

```yaml
cli_tools:
  - name: mycli                              # 单二进制形态：name 即命令名
    source: https://my-svc.example.com/tools/mycli-linux-amd64
    digest: "sha256:…"                       # 本类目强制，无 digest 拒绝写入
    version: "1.4.2"                         # 元数据，进 apply report，审计线上版本
  - name: toolkit                            # 压缩包形态
    source: https://my-svc.example.com/tools/toolkit.tar.gz
    unpack: tar.gz
    strip_components: 1                      # 可选，同 §3.2
    digest: "sha256:…"
    entrypoints: [bin/tk, bin/tk-helper]     # 包内哪些文件暴露为命令（必填）
```

规则：
- **v1 只支持静态二进制 / 压缩包**两种形态。需要跑包管理器
  （npm/pip/apt）的安装属命令式领域，走 script（ARCA-only）——与「机制层
  操作不进 manifest」同一条原则。
- **`digest` 强制**：平台代为分发**可执行物**，供应链必须钉死；digest 同时
  是收敛判断的唯一依据（未变 → `unchanged` 零动作）。
- **落点与 PATH**：平台定义引擎无关的逻辑「工具目录」，工具落入其中并由
  平台保证其在 agent 进程的 PATH 上——用户不感知物理路径。
- **用法认知不归本类目**：安装只保证「命令在 PATH 上」；模型如何知道并
  正确使用它，走用户自己声明的 identity（`TOOLS.md` 是合法类型）或配套
  skill——`bcs-cli` 的「二进制 + SKILL.md」双件套即推荐姿势。
- **能力门控**：ARCA 系支持（PATH 注入点见 engine-requirements A2）；
  **teclaw 待确认（T4）**——可执行位、PATH 注入、以及对用户提供二进制的
  沙箱策略（其能力面与 script 相邻，须由 teclaw 表态）；其余形态见能力
  矩阵。

## 4. 变量替换

`source` URL 与 `script` 环境中可用一小组平台注入变量（契约的一部分，随
`schema_version` 版本化）：

| 变量 | 含义 |
| --- | --- |
| `OCB_BOT_ID` | bot 标识 |
| `OCB_ENGINE_TYPE` | 当前引擎类型 |
| `OCB_ENV` | 环境（dev/prod/…） |
| `OCB_TENANT` | 租户标识 |

- manifest 中以 `${OCB_*}` 占位、apply 时替换；仅允许白名单变量，未知占位
  报错。
- script 中以环境变量注入（注意：#935 的 base64 封装保证 BaaS
  `_safe_format_hook` 的 `{token}`/`{client_id}` 替换不会触碰脚本体，
  `${OCB_*}` 在脚本里就是普通 shell 变量展开）。

## 5. 限额（建议值，评审定稿）

| 项 | 建议上限 |
| --- | --- |
| 置备文档总大小 | 64 KiB（script 部分另按现状 24 KiB） |
| 每类别条目数 | 50 |
| `content` 内联单条 | 64 KiB |
| fetch 单条目 | skills zip 100 MiB；resources 文件 100 MiB；identity 1 MiB；cli_tools 单工具 200 MiB |
| resources 目录条目 | 单归档 200 MiB；解包后 500 MiB；单归档文件数 5000 |
| 单次 apply fetch 总量 | 500 MiB（目录条目计解包后大小） |
| fetch 超时 | 单条 60s；单次 apply 总预算 300s |

超限在 PUT 时能校验的（文档大小、条目数、内联大小）当场拒绝；只能在
fetch 时发现的（远端内容大小）按 `on_fetch_failure` 处理并记入 apply
report。

## 6. 非目标（v1 明确不做）

- **机制层文件操作**：不提供「往任意路径写文件」的条目类型。资源均以逻辑
  名/类型声明，物理位置永远是引擎的决定。
- **内联 secrets**：manifest / script 体内与 source URL 中不得出现任何
  凭证；私有源鉴权一律走凭证引用（§2.1），secret 只存在于租户凭证存储、
  写后不可读回。
- **`engine_ext`**：不可经 manifest 读写。
- **删除资产**：manifest 只管理声明集合与 managed 标记，不级联删除用户
  资产。
- **teclaw 的 script**：不支持，且不承诺未来支持（需 teclaw 侧出现容器内
  执行通道后另行评估）。
