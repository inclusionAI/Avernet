# Manifest Schema v1（草案）

> 状态：DRAFT（讨论稿）。设计论证见 `design.zh-CN.md`；每类配置的完整业务
> 案例见 `examples.zh-CN.md`。本文只定义文档形状、校验规则与到各引擎的
> 映射。字段名以定稿评审为准。

## 1. 顶层结构

置备文档（经 `PUT /openapi/v1/bots/{bot_id}/provisioning` 写入）：

```yaml
schema_version: 1

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

两部分均可缺省。`manifest` 六个类别均可缺省，缺省的类别不参与 apply
（不碰任何实体）。**类别存在但为空列表（`skills: []`）含义不同**：声明
「该类别下不应有 managed 实体」，会把此前 apply 落成的 managed 实体摘除
标记（不删资产，语义同 DELETE，见 design §6）。

## 2. 条目通用字段

内容型条目（resources / skills / identity）的来源三选一：

| 字段 | 说明 |
| --- | --- |
| `source` | HTTPS URL，平台在 apply 点经 guarded fetcher 拉取（design §4）。支持变量替换（§4） |
| `content` | 内联 UTF-8 文本，适合小的 md/配置。与 `source` 互斥 |
| （注册项引用） | 仅特定类别：MCP 的 `server_code`；v2 的 `center://` skill 引用 |

通用可选字段：

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `digest` | 无 | `sha256:…`。校验 fetch 内容，不匹配按 fetch 失败处理（钉扎可复现） |
| `auth` | 无 | 租户级命名凭证的引用（§2.1）；仅对 `source` 条目有效。fetch 时注入为请求头 |
| `on_fetch_failure` | `keep_last` | `keep_last` / `skip` / `fail`（design §4.3） |
| `apply_once` | —— | **v1 保留字，拒绝写入**；v2 语义见 design §3.2 |

### 2.1 凭证引用 `auth`

私有源的鉴权走**引用**，secret 永不出现在 manifest 里（设计论证与安全
规则见 design §4.5）。凭证是租户级命名对象，一次性写入。**名字
（URL 中的 `{name}`，下例 `cms-token`）是自由标识符**，`auth` 按它做
字典查找取出凭证对象；名字与 `allowed_origins` 里的域名之间不存在任何
字符串匹配或推导关系——URL 的匹配只发生在「source 的 origin ∈ 该凭证的
`allowed_origins`」这一步：

```text
PUT /openapi/v1/provisioning/credentials/cms-token
{
  "header_name": "Authorization",
  "secret": "Bearer eyJhbGciOi…",
  "allowed_origins": ["https://cms.example.com"]
}
```

manifest 条目引用它：

```yaml
resources:
  - path: data/faq.csv
    source: https://cms.example.com/kb/faq.csv
    auth: cms-token
```

校验与行为：

- `auth` 引用的凭证不存在 → PUT manifest 时警告、apply 时该条目 `failed`
  （「credential cms-token 不存在」）；
- fetch 目标 URL 的 origin 不在该凭证的 `allowed_origins` 内 → 条目
  `failed`（防凭证被 `source` 改指处套取）；跨 origin 重定向直接失败；
- GET 凭证只返回掩码元数据（`has_secret` / `header_name` /
  `allowed_origins` / `updated_at`）；
- 轮换 = 重 PUT 同名凭证，下一个 apply 点生效，不触发 apply；
- apply report 只记凭证名，永不记值。

v1 仅支持请求头注入；query 参数型、mTLS 见开放问题 O8。

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

  # 目录条目：source 为归档，内容按相对层次展开到 path 之下
  - path: data/kb/
    source: https://my-svc.example.com/kb/knowledge-base.zip
    unpack: zip                  # zip | tar.gz（目录条目必填）
    strip_components: 1          # 可选，默认 0：剥掉归档内的前 N 层目录
                                 # （语义同 tar --strip-components；业务用
                                 #  `zip -r kb.zip kb/` 打包出的壳目录用它消掉）
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
- 目录源的其他传输形态（git 子树、索引文件、对象存储前缀）列 v2 候选
  （design §9）。

### 3.3 `skills` — local skills

```yaml
skills:
  - name: reviewer               # skill 名（必填，唯一）
    source: https://my-svc.example.com/skills/reviewer.zip   # zip，形状同现有 upload API
```

- 语义等价于现有 `POST /openapi/v1/bots/skills/upload`（zip 校验、大小限制
  复用现状）+ activate。
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
