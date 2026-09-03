# Manifest Schema v1（草案）

> 状态：DRAFT（讨论稿）。设计论证见 `design.zh-CN.md`；每类配置的完整业务
> 案例见 `examples.zh-CN.md`；面向用户的操作说明见 `user-manual.zh-CN.md`。
> 本文只定义文档形状、校验规则与到各引擎的映射。
>
> **本文已与 `work-items.zh-CN.md` §2/§3 的已定决策对齐。**那些决策晚于
> `design.zh-CN.md`，凡两者不一致处**以本文为准**：收敛策略为**类目级覆盖**
> （§1）、替换变量改名为 `BOT_*`（§4）、`on_fetch_failure` 去掉 `skip`
> （§2）、源上新增 `mode`（§2.3）、identity 保留名单写入即拒（§3.5）、
> `script` 的首启顺序（§3.6）。设计文档本身未随之修订，其差异清单见
> `user-manual.zh-CN.md` 附录 D。
>
> **第一期尚未开放的构造见 §7。**它们写得出来但还没有物化器，`PUT` 一律
> 拒绝——不会被静默忽略，也不会「先存着以后生效」。

## 1. 顶层结构

配置清单文档（经 `PUT /openapi/v1/bots/{bot_id}/config-manifest` 写入）：

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
  cli_tools: [ … ]             # §3.7，命令行工具（W9 已交付；始终平台托管）

script:                        # 命令式部分，能力门控（teclaw / desktop 拒绝）
  body: |                      # §3.6，即 #935 的 startup script
    #!/bin/bash
    …
```

三段均可缺省。**收敛策略是类目级覆盖**——让清单生效 = 把每个**被声明的
类别**覆盖到恰好等于声明：

| 写法 | 含义 | apply 做什么 |
| --- | --- | --- |
| 类别缺席 | 「不表态」 | **完全不碰**该类别 |
| 类别存在且非空 | 「这个类别应当恰好是这些」 | 把该类别的**区域**覆盖成声明的样子，区域内其余条目被移除 |
| 类别存在但为空（`skills: []`） | 「这个集合是空的」——空集合**也是**声明 | **移除该区域内的全部条目** |
| `DELETE` 整份文档 | 没有任何类别被声明 | **什么都不删**：此前落成的实体原样留在 bot 上 |

后两行不矛盾，是同一条规则的两端：`[]` 是声明，缺席不是声明。

**「区域」逐类别定义，不是全局的**——搞错这一点，一条本意是收敛 skill 列表
的规则就会删掉 bot 的工作目录：

| 类别 | 被覆盖的区域 |
| --- | --- |
| `skills` | **active skill set**：它等于声明，未列出的 skill 被移除 |
| `identity` | **identity 文件集合**，减去保留名单（§3.5） |
| `resources` | **仅被声明的 `path` 子树**；声明的 `path` 之外一律不碰——workspace 是 bot 的工作区，不是清单的 |
| `mcp` | **已启用的 server 集合** |
| `engine_config` | **被声明的顶层键**（逐键覆盖，§3.4） |
| `cli_tools` | 这个 bot **由平台安装**的工具集合（`ac_bot_cli_tool` 里的行；镜像自带命令不在内）。移除是按表算的，不看引擎的清单 |

**类别是 all-or-nothing 写入的**：被声明的类别里只要有任何一条无法物化，
该类别**完全不覆盖**（关于它的一切保持原样），逐条结果照常记进 apply
report；一个类别的失败**不牵连**别的类别。在覆盖语义下，一个不完整的集合
是**破坏性**的：声明 `{A, B}` 却只写入 `{A}` 就等于删掉了 B——一次瞬时的
取源失败不能删掉一个正在工作的实体。

**v1 不在实体上盖 `managed by manifest` 标记**：清单落成的实体与手工创建
的存储完全一样，下游（reconcile / compose / 界面 / 引擎）无从区分，也不
需要区分。「我当前的配置是什么、上次 apply 做了什么」由
`GET …/config-manifest` 与 `GET …/last-apply` 回答。

### 1.1 内容归属：文本进 git，制品进制品库

六个类别按内容本性分成两组，这条线决定了各自的来源形态：

| 类别 | 内容本性 | 典型来源 |
| --- | --- | --- |
| identity / skills / resources | **文本表达**（md、SKILL.md、csv/json…） | git（§2.2/§2.3）；也可 URL |
| engine_config / mcp | 文本，但**内联在 manifest 内**（键值 / 注册表引用） | 无 source——它们的「文本」就是配置清单文档自身 |
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
| `on_fetch_failure` | `keep_last` | **只有两个取值**：`keep_last`（用平台上一次为这一条成功物化的副本补全集合）/ `fail`（该类别不写）。**`skip` 已删除**——在类目覆盖语义下它会意味着「把这一条删掉」，与字面相反 |
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

**一个端点、一个 body schema**。判别键是**认证机制**（凭证怎么作用到请求
上），**不是存储类型**——`git` / `oss` / `url` 是**源**的属性，凭证不关心：
git 源调托管服务 HTTP API、URL 源发普通 GET，注入动作相同。

```text
PUT /openapi/v1/bots/source-credentials/{name}
{
  "type": "header",                     # 判别键；v1 唯一实现，缺省即 header
  "allowed_prefixes": ["…"],            # 所有 type 共有（见下）
  "header_name": "PRIVATE-TOKEN",       # ↓ type=header 专有
  "secret": "…"
}
```

两个实际调用（同一端点、同一 schema，只是 `{name}` 与取值不同）：

```text
PUT /openapi/v1/bots/source-credentials/corp-git-content
{
  "type": "header",
  "header_name": "Authorization",                                  # 按托管服务定（下方「token 选型」）
  "secret": "Basic <base64('git:<访问令牌>')>",                     # 机器账号的只读令牌，不用个人 PAT
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

两次调用是因为**存了两个不同的 secret**（git 一个、制品库一个），不是两个
接口——`{name}` 是凭证名，同 `PUT /users/alice` 与 `PUT /users/bob` 的关系。
一个凭证装多个 secret 是反模式：轮换周期、权限边界、可出示范围都会糊在一起。

**预留的 `type`（v1 写入即拒绝，等真实需求再实现）**：

| type | 字段 | 何时需要 |
| --- | --- | --- |
| `header`（v1） | `header_name` + `secret` | 静态请求头：git token、多数对象存储的临时 token |
| `oss_aksk`（预留） | `access_key_id` + `access_key_secret` | 阿里云 OSS 等的 **AK/SK 请求签名**——不是固定头，而是每个请求现场按签名算法计算，装不进 header 形状 |
| `basic`（预留） | `username` + `password` | HTTP Basic 源 |

判别键留在 `type` 上而非源类型上，是为了让 AK/SK 这类**机制不同**的凭证将来
能直接加进同一个端点，而不必新开接口。

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
（为本地联调，与 `outbound_rules` 单 box 同形）。这对 源凭证
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
服务不支持时，**用机器账号的 token**（账号只授予内容仓库的只读成员
权限，以成员关系收权）；**不使用个人 PAT / 个人私有令牌**——权限面是个人
全量可见仓库，且生命周期绑定个人（转岗/离职即断）；**也不使用带完整读写
scope 的 API 令牌**——为一件只读的工作换来对一切的写权限。

**Ant Code（本部署的内容源）的口径已定**：它不提供 Deploy Token，且两种
scope 里只读的那个（`read_repository`）**只有 Git-over-HTTP、没有 API**。
所以用**专用机器账号 + `read_repository` 访问令牌**，按 HTTP Basic 注入
（`Authorization: Basic base64("git:<token>")`）——**v1 的凭证模型不需要
任何改动**。**令牌过期由凭证 owner 自管**，平台不轮换；因此认证失败
（401/403）在 apply report 里必须报成「凭证 `<name>` 被拒绝」，与通用取源
错误区分开，否则一次该去轮换的故障会被读成网络问题。

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
      subpath: bots/support-agent/soul.md # 源内路径；变量替换照常可用
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
- 落地后的全部语义（目录级所有权、整树替换及其非原子窗口、嵌套禁止、
  权限拍平、teclaw 逐文件展开）与 §3.2 完全一致——git 只是传输形态。

实现口径（backend 内部）：**git over HTTPS 的浅层单 ref fetch**，复用
guarded fetcher 的大小/超时/并发上限，并对解开后的 checkout 施加与归档
同样的包含性检查（符号链接逃逸、gitlink/submodule、设备特殊条目）与体积
上限。**这取代了 design §10.5 的「走托管服务归档 API」方案**：本部署的
git 宿主没有只读的 **API** scope，只有只读的 **Git-over-HTTP** scope，走
API 那条路就得把一个「对一切可读写」的凭证放进数据库（§2.1）。契约本身
与托管方无关——任何经 HTTPS 可达的 git 服务都满足它。

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
      subpath: bots/support-agent/soul.md
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

**移动引用与 `mode`**：分支（以及会被重打的 tag）可能在一次没人把它跟配置
变更联系起来的重启里解析出不同内容。源上的可选字段 `mode` 控制这件事：

| `mode` | 行为 |
| --- | --- |
| `non_strict`（**默认**） | 应用新内容，并在 apply report 里对该条目**告警**，写明前后两个 SHA |
| `strict` | 解析出的 SHA 与上次 apply 记录的不同时，该条目**失败**，bot 继续跑它现在跑的 |

- **写在源上**，不是按 bot、也不是按清单——要描述的性质是「这个 ref 允不允许
  在我脚下移动」，它属于持有 `ref` 的那个东西。一份清单里同时有一个钉死的
  外部依赖和一个快速变动的内部仓库是常态。
- **SHA 形式的 `ref` 忽略这个模式**（它动不了，两个分支都触发不了）——是
  「接受但无效」，不是报错。
- 未知取值 `PUT` 时拒绝：拼错的 `mode` 若静默落到默认值，等于什么都没钉住。
- 内联 git `source` 同样接受 `mode`（它也持有 `ref`）。

## 3. 类别定义

各类别的映射一览（详见各小节）：

| 类别 | 平台实体（apply 落点） | ARCA 系交付 | teclaw 交付 |
| --- | --- | --- | --- |
| mcp | MCP 启用配置（现 `openapi_v1/mcp` 同源服务） | 现有 per-MCP push（`/api/mcp`） | `mcp.servers[]`（`McpServerRef`，凭证 compose 时内联，现状机制） |
| resources | resource 记录（现 `openapi_v1/resources` 同源服务） | 现有 resource 交付 | `resources[]`（`ResourceRef {store,path}`） |
| skills | 本地 skill 记录（现 skills upload 同源服务） | 现有 skill 交付 / NAS | `skills[]`（`SkillRef, scope=user`） |
| engine_config | engine config（`EngineConfigServiceProtocol.write_bot_config`） | 现有 provider-blind 写 | 同一条 provider-blind 写（既有 `config/teclaw.json` 文件通道，**非 artifact 字段**；创建时序确认 T3） |
| identity | identity 文件记录（现 `openapi_v1/identity` 同源服务） | 现有 identity 交付 | `identity_files[]`（`FileRef`） |
| cli_tools | `ac_bot_cli_tool` + 平台在对象存储里的字节副本（W9 新增） | 引擎的 `install` 端点（按命令名调用；落点、可执行位、暴露给 agent 都在这一次调用内由引擎完成） | `cli_tools[]`（`{name, store, path, md5, version}`，artifact `schema_version` **保持 4**；规格见 `teclaw-cli-contract.zh-CN.md`） |
| script | script 存储（#935 现状） | `after_create_cmd_hook` 启动链 | **不支持，写入时拒绝** |

### 3.1 `mcp` — MCP servers

```yaml
mcp:
  - server_code: github          # 平台 MCP 注册表引用（必填，且是唯一的字段）
```

- **一个条目就是一个注册表引用，没有别的字段。**只接受注册表引用；**凭证永不
  出现在 manifest**（design §4.5）。
- 校验：`server_code` 必须存在于注册表且租户有权限（复用现有
  `check_mcp_permission` 逻辑）。
- apply 动作是**收敛这个 bot 的已启用 server 集合**——也正是 §3.2 给这个类目
  定义的区域：声明了但未启用的启用，已启用但不再声明的停用，已经一致的记
  `unchanged`。经既有的 per-bot 启用服务（`DirectActivationService` →
  `ac_bot_mcp_installation`）完成。

#### `config` 已从 v1 移除（W4 评审结论）

本节早先写着 `config: { … }  # 可选，per-bot 配置，形状同现有 MCP config API`。
**那两句不可能同时为真**，而这个字段已被删除、写入时按名拒绝（与已废弃的
`cli_tools.entrypoints` 同样处理）。

- **那个 API 不是 per-bot 的。**它写 `ac_user_mcp_config`，键为
  `(user_id, server_code)`，且写入路径会调用
  `sync_mcp_detail_to_all_bots`——**扇出到该 owner 名下的每一个 bot**。于是
  「应用某一个 bot 的 manifest」会改掉他所有 bot 的 MCP 配置：这个影响范围是
  别的类目都没有的，§3.2 的「区域逐类目定义」也从未授权它。
- **它装的正是凭证。**`api_key` 与 `custom_headers` 是它的载荷，而 design §4.5
  规定凭证不得进入 manifest。
- **真正 per-bot 的那个东西已经被覆盖了**：`ac_bot_mcp_installation`，也就是
  §3.2 说的「已启用的 server 集合」。

所以账号级的 MCP 配置（api_key / headers / endpoint_env / transport_protocol）
仍然通过既有的公开端点管理，它本来就是账号级的：

```text
GET  /openapi/v1/bots/mcp/servers/{server_code}/config
PUT  /openapi/v1/bots/mcp/servers/{server_code}/config
```

**留作后续（可加性，不破坏兼容）：**`ac_bot_mcp_call_config` 的 `call_type`
（`owner` / `caller`）是另一个确实 per-bot 的 MCP 事实，端点为
`PATCH /openapi/v1/bots/{bot_id}/mcps/{server_code}/call-type`。它没有被纳入
v1，因为它在 §3.2 给本类目定义的区域**之外**，而且它的写入带有 draft 状态、
lock epoch 与不可逆语义（`CallerLockEpochError`、`CallerIdentityReadOnlyError`），
一个幂等、可重跑的 apply 必须先回答这些问题。往一个封闭集合里加一个键是可加
的，所以这件事可以以后做，且不需要 v2。

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
- **收敛单位是整棵树**（归档形态即归档内容 hash，git 形态即 commit SHA），
  不做逐文件比对。但**「未变」要以已下发的内容为准，不能只看源**：源没变而
  树发生了漂移（上次 apply 之后有人在 `path` 下加了或改了文件）时，仅凭源
  报 `unchanged` 会把漂移原样留着，直接击穿下面那条所有权规则。因此 v1 的
  口径是**每个 apply 点整体替换**；「读并哈希已部署的子树再比对」是可选优化，
  只有当读这棵树比重写它更便宜时才值得做。
- **目录级声明获胜**：`path` 下整棵树归 manifest 管辖——源中不存在的
  文件在 apply 时被清除（含手工添加的）。目录之外不碰。
- **替换只原子到传输层允许的程度**：平台侧解包到临时位置（保证一次失败的
  取源或一个坏归档永远到不了 bot），但下发只有 `delete_tree` + 逐文件写
  ——设备文件系统契约里没有 rename/move。所以 `path` 下的树存在一个真实的
  中间窗口，下发中途失败会停在那里：该条目记 `failed`，apply report 说明
  这棵树处于未知状态。**别把 bot 运行期要写的目录声明成资源目录。**
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
- **W5 起 `content` 内联在 skills 条目上被拒绝**（`content_not_a_skill_package`）：
  skill 是包（SKILL.md + 其引用的文件），一段内联文本成不了包——按「这个面
  不接受它物化不了的东西」的既定规则在 `PUT` 拒绝。identity 类目不受此
  限制（一个 identity 文件本身就是一段文本）。
- teclaw：物化进 bot-data store 后以 `SkillRef(scope="user")` 进 artifact，
  与今天手工 upload 的 skill 走完全相同的路。
- v2 预留来源：`source: center://<skill_uuid>@<version>`（skill center 引用，
  不经 fetch，直接引 store）。

### 3.4 `engine_config` — 引擎配置

> **第一期不开放**（§7）：跨引擎确认把它移出了第一期，所以现在写了会被
> `PUT` 拒绝。下面是它回来时的形状。

```yaml
engine_config:
  config:                        # 键值对象，形状同现有 engine-config API
    model: …
    …
```

- **整类别只有一个对象**，不是列表。合并语义：**声明的顶层键获胜**（逐键
  覆盖），未声明的键不碰——本类别的**覆盖区域按顶层键计**（§1）。
- 明确排除：`engine_ext` 是引擎自有的不透明数据（平台承诺「存储原样、永不
  解释」），**manifest 永远不能触碰它**。
- 校验：形状校验沿用现有 engine-config 写路径；引擎相关的键合法性由该路径
  的现状规则负责。

### 3.5 `identity` — identity 文件

```yaml
identity:
  - type: SOUL.md                # 必须属于该引擎的合法 identity 文件集
    source: https://my-svc.example.com/bots/support-agent/soul.md
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
- **保留名单：`MEMORY.md` 与 `IDENTITY.md` 声明即拒。**这两个是引擎生成的
  运行期状态，apply **永不写入、也永不移除**它们——它们是类目覆盖（§1）
  唯一的例外。两者都在 `VALID_IDENTITY_FILES` 里，所以上一条会放行，必须
  在 `PUT` 时单独拒绝：否则用户会得到一份「被接受、却永远收敛不了」的文档。
  名单**有限且可枚举**，正因如此它才是两个引擎系都能同意并检查的契约。
- 其余引擎可能写入的文件（运行期会被引擎更新的那些）技术上可声明，但声明
  获胜语义会在每个 apply 点重置它们——SOUL.md、RULES.md 等人设/规则文件
  才是本类目的主场景。

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
- **生效时机**：`PUT` 之后**立即下发、下次启动时执行**。它是唯一效果被延后
  的类别——其余类别 `PUT` 后立即生效、且不需要重启。
- ⚠️ **第一期：`script` 不得依赖同一份清单声明的任何内容。**脚本被烤进启动
  命令，而 `identity` / `resources` / `skills` 在容器**起来之后**才下发，所以
  **首启时脚本跑在它们之前**。这**反转了 design §3.4** 的顺序承诺（「脚本
  可以假定声明的实体已就位」）——那句话要等所有类别都能在启动前下发之后
  才成立，届时本条限制删除。
- 能力：ARCA 系支持；**teclaw、desktop 写入时拒绝**（fail closed）。本特性
  只覆盖**新建 bot**，而生产上新建 bot 只会解析到 `baas` / `teclaw` 两种
  provider，所以 `engine-requirements.zh-CN.md` 矩阵里 LOCAL/singlebox 与
  ARCA-direct 那两行属于**范围之外**，不是新增的强制拒绝。

### 3.7 `cli_tools` — 给模型调用的命令行工具

> **W9（#1477）已交付。**设计动机：把仓库内 `bcs-cli` 的手工模式（二进制
> 挂 PATH + SKILL.md 教用法）产品化、声明化。
>
> 本类目**始终由平台托管**（与 `mcp` 同列），与 `teclaw_platform_managed`
> 开关无关：平台自己取源、验签、留一份字节副本，表里那行就是期望状态。
> 除清单外，还有一组同源的管理 API：`POST` / `GET` /
> `DELETE /openapi/v1/bots/{bot_id}/cli-tools`。两个入口调的是同一个组件，
> 因此对同一份声明给出同样的拒绝理由。

```yaml
cli_tools:
  - name: mycli                              # 命令名。一个条目 = 一个命令 = 一个文件
    source: https://my-svc.example.com/tools/mycli-linux-amd64
    digest: "sha256:…"                       # 本类目强制，无 digest 拒绝写入
    version: "1.4.2"                         # 元数据，进 apply report，审计线上版本
  - name: tk                                 # 压缩包形态：用 subpath 指出包内哪个文件是命令
    source: https://my-svc.example.com/tools/toolkit.tar.gz
    subpath: bin/tk                          # 源内路径（§2），此处即包内路径
    unpack: tar.gz                           # 可选，扩展名不可靠时显式指定
    digest: "sha256:…"
    version: "0.9.0"
```

规则：
- **一个条目 = 一个命令 = 一个文件。**`name` 即命令名（同一 bot 内唯一、不含
  路径分隔符）；平台物化出来的、进入交付通道的也是**那一个文件**。一个压缩包
  里有两个命令，就写**两个条目**，各自用 `subpath` 指向包内的那个文件。
  > **没有 `entrypoints` 字段。**早期草案里一个条目是「一个目录 + 一份包内
  > 要暴露成命令的相对路径清单」，这一层已被摊平。摊平换掉的是一整套只为约束
  > 它而存在的规则（包内路径穿越、符号链接逃逸、包内 basename 撞名），也顺带
  > 消掉了「包内被 exec 的辅助程序无法置可执行位」这个已知缺口——见下一条。
- **v1 交付的是自包含的单个可执行文件**（静态二进制是典型形态）。压缩包只是
  **传输形态**：平台解包、取出 `subpath` 指定的那个文件，**包内其余文件不下发**。
  所以**需要同包辅助程序或运行时读同包 `lib/` 的工具，v1 不支持**——请打成
  自包含二进制。需要跑包管理器（npm/pip/apt）的安装属命令式领域，走 script
  （ARCA-only）——与「机制层操作不进 manifest」同一条原则。
- **`digest` 强制**：平台代为分发**可执行物**，供应链必须钉死。它算的是**取回来
  的源对象**（单二进制即该二进制，压缩包形态即整个包），同时是收敛判断的依据
  （连同 `subpath`：同一个包换取另一个文件是真实变更）。
- **`md5` 由平台补，不是用户字段**：物化之后，平台对最终那一个文件计算 MD5 并
  写进交付契约，供引擎在落地前校验字节完整性（teclaw 侧的字段定义见
  `teclaw-cli-contract.zh-CN.md` §3.2/§3.3.1）。用户侧的钉扎手段仍然只有
  `digest`（sha256）。
- **交付的是一个自包含的可执行文件**：平台不下发同包的其他文件，需要同包
  辅助程序的工具请打成静态二进制（同上一条）。
- **落点归引擎，不归平台**：平台按**命令名**调用引擎的 `install`，由引擎决定
  目录、置可执行位、并把它暴露给 agent——这一切在那一次调用内完成。后端**不
  知道**工具落在哪里，两个方向上都没有容器路径穿过这个边界；平台也不发
  `chmod`、不跑 shell 命令。各引擎的目录常量记在
  `engine-requirements.zh-CN.md`。
- **v1 里 agent 怎么找到工具（重要，且是一项已知代价）**：早先此处写的是
  「平台保证工具在 agent 进程的 PATH 上、用户不感知物理路径」。**那是不准确
  的，现已更正**：v1 不做 PATH 注入。工具落点由默认技能集里的一个 skill 告诉
  agent，agent **以绝对路径调用**。
  代价要说清楚：`mycli --help` 这样直接敲命令名**不工作**；每次调用都依赖那个
  skill 被读到；一个脚本内部 shell 调同目录的另一个工具**也找不到**。
  之所以敢把 PATH 推后：把目录加进 PATH 是**引擎侧**的改动，不牵动 schema、
  管理 API、`ac_bot_cli_tool` 表或 artifact 契约中的任何一个字段——真做的
  时候，本节这段描述是唯一要改的东西。
- **用法认知不归本类目**：安装只保证「这个 bot 有这个命令」；模型如何知道并
  正确使用它，走用户自己声明的 identity（`TOOLS.md` 是合法类型）或配套
  skill——`bcs-cli` 的「二进制 + SKILL.md」双件套即推荐姿势。
- **能力门控**：ARCA 系与 teclaw 均支持；desktop 与未知引擎拒绝。ARCA 走引擎
  的 CLI 端点装进运行中的容器；teclaw 由**编排出的 artifact 承载**
  （`cli_tools` refs 指向平台在对象存储里的那份副本），没有单独的上传调用。
  artifact 的 `schema_version` **保持 4**——`cli_tools` 自 v4 起就是可选字段，
  平台开始填它不构成契约破坏（详见 `teclaw-cli-contract.zh-CN.md`）。

## 4. 变量替换

`source` URL 与 `script` 环境中可用一小组平台注入变量（契约的一部分，随
`schema_version` 版本化）：

| 变量 | 含义 |
| --- | --- |
| `BOT_ENGINE_TYPE` | 当前引擎类型 |
| `BOT_ENV` | 环境（dev/prod/…） |
| `BOT_TENANT` | 租户标识 |
| `BOT_ARCH` | 目标 CPU 架构；当前解析为常量 `amd64` |

> **命名是 `BOT_*`，不是 `OCB_*`。**`OCB` 是内部代号，只出现在内部机件上，
> 从来不是面向用户的命名空间；写 `${OCB_BOT_ID}` 会被 `PUT` 拒绝。前缀保留
> 是必要的——它们会作为环境变量注入 `script`，不带前缀的 `${ENV}` 会跟脚本
> 作者自己的变量撞车。`BOT_ARCH` **现在就实现**（而不只是占个名字）：将来
> 机群若变成混合的，改的只是这个值从哪来，不改 schema、用户什么都不用重写。

> **没有 `BOT_ID`。**上表里每个变量都是**机群**的属性——环境、租户、引擎、
> 架构——这正是一份文档能被多个 bot 复用的原因。bot 标识不是：它在创建时生成
> （日期 + 8 位随机字符），调用方并不能指定，所以在 git 仓库里准备内容的作者
> 根本无从得知。真要按 bot 区分的内容，写在那个 bot 自己的 manifest 里、直接
> 写字面量即可。`${BOT_ID}` 和任何未知占位符一样会被 `PUT` 拒绝。

- manifest 中以 `${BOT_*}` 占位、apply 时替换；仅允许白名单变量，未知占位
  报错。
- **替换发生在取源之前、也在凭证的前缀授权之前**（§2.1），所以替换出来的
  URL 逃不出 `allowed_prefixes`。
- script 中以环境变量注入（注意：#935 的 base64 封装保证 BaaS
  `_safe_format_hook` 的 `{token}`/`{client_id}` 替换不会触碰脚本体，
  `${BOT_*}` 在脚本里就是普通 shell 变量展开）。

## 5. 限额（建议值，评审定稿）

| 项 | 建议上限 |
| --- | --- |
| 配置清单文档总大小 | 64 KiB（script 部分另按现状 24 KiB） |
| 每类别条目数 | 50 |
| `content` 内联单条 | 64 KiB |
| `source` URL 单条 | 2048 字符（与 provenance 列宽一致；PUT 时拒绝——长度是准入可见的，不是取完后才该知道的） |
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
- **删除**声明 ≠ 删除资产：`DELETE` 整份文档什么都不删（§1）。但要分清
  ——**被声明的类别，其区域内未被声明的条目会在 apply 时被移除**（§1 的
  覆盖语义），包括通过界面装上的。清单不做的是「级联删掉未被任何类别覆盖
  的用户资产」。
- **`apply_once` 逃生舱**：v1 保留字，写入即拒（§2）。
- **teclaw 的 script**：不支持，且不承诺未来支持（需 teclaw 侧出现容器内
  执行通道后另行评估）。

## 7. 第一期尚未开放的构造

**规则：这个面绝不接受它 apply 不了的东西。**下面这些在本文里表达得出来，
但第一期没有对应的物化器/解析器，因此 `PUT` 会明确报 `unsupported` 并拒绝
——不会被静默忽略，也不会「先存着以后生效」。往词汇里加东西的人，要么在这张
表里加一行，要么把 apply 它的代码一起加上。

| 构造 | 为什么还不能 apply | 何时解禁 |
| --- | --- | --- |
| 类别 `engine_config`（§3.4） | 按跨引擎确认的结论移出第一期 | 其物化器回来时 |
| `from` 指向**命名源**（§2.3） | 命名源解析属于 git/命名源工作项 | 该工作项落地 |
| **git 源**（§2.2，含内联 git `source`） | 同上——git 解析器属于同一工作项 | 该工作项落地 |

命名源与 git 源是本文推荐的主力写法，且在关键路径上、按计划进 v1；在它们
落地之前，可用的取源形态是**条目内联的 HTTPS `source` URL**，之后迁到命名
源只是把 `source` 换成 `from` + `subpath`。

**写客户端的人：能力表是唯一的事实来源**——先
`GET /openapi/v1/bots/{bot_id}/config-manifest/capabilities`，它与 `PUT`
的判定是同一个函数，不会出现「声称支持而随后拒绝」。
