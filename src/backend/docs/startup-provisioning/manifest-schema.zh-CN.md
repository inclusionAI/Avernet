# Manifest Schema v1（草案）

> 状态：DRAFT（讨论稿）。设计论证见 `design.zh-CN.md`；本文只定义文档
> 形状、校验规则与到各引擎的映射。字段名以定稿评审为准。

## 1. 顶层结构

置备文档（经 `PUT /openapi/v1/bots/{bot_id}/provisioning` 写入）：

```yaml
schema_version: 1

manifest:                      # 声明式部分，所有引擎
  mcp: [ … ]                   # §3.1
  resources: [ … ]             # §3.2
  skills: [ … ]                # §3.3
  engine_config: { … }         # §3.4
  identity: [ … ]              # §3.5

script:                        # 命令式部分，能力门控（teclaw / desktop 拒绝）
  body: |                      # §3.6，即 #935 的 startup script
    #!/bin/bash
    …
```

两部分均可缺省。`manifest` 五个类别均可缺省，缺省的类别不参与 apply
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
| `on_fetch_failure` | `keep_last` | `keep_last` / `skip` / `fail`（design §4.3） |
| `apply_once` | —— | **v1 保留字，拒绝写入**；v2 语义见 design §3.2 |

## 3. 类别定义

各类别的映射一览（详见各小节）：

| 类别 | 平台实体（apply 落点） | ARCA 系交付 | teclaw 交付（artifact 字段） |
| --- | --- | --- | --- |
| mcp | MCP 启用配置（现 `openapi_v1/mcp` 同源服务） | 现有 per-MCP push（`/api/mcp`） | `mcp.servers[]`（`McpServerRef`，凭证 compose 时内联，现状机制） |
| resources | resource 记录（现 `openapi_v1/resources` 同源服务） | 现有 resource 交付 | `resources[]`（`ResourceRef {store,path}`） |
| skills | 本地 skill 记录（现 skills upload 同源服务） | 现有 skill 交付 / NAS | `skills[]`（`SkillRef, scope=user`） |
| engine_config | engine config（`EngineConfigServiceProtocol.write_bot_config`） | 现有 provider-blind 写 | `engine_overrides`（**待确认项 T3**） |
| identity | identity 文件记录（现 `openapi_v1/identity` 同源服务） | 现有 identity 交付 | `identity_files[]`（`FileRef`） |
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

```yaml
resources:
  - name: sales.csv              # 逻辑名（bot 工作区内的文件名/相对名，必填）
    source: https://my-svc.example.com/data/sales.csv
```

- `name` 是**逻辑名**，不是引擎路径；语义与现有 resources API 的资源名一致，
  物理位置由各引擎照现状决定。
- 校验：`name` 白名单字符、禁止路径穿越（`../`、绝对路径）；与现有
  resources API 的命名规则完全一致。

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
| fetch 单条目 | skills zip 100 MiB；resources 100 MiB；identity 1 MiB |
| 单次 apply fetch 总量 | 500 MiB |
| fetch 超时 | 单条 60s；单次 apply 总预算 300s |

超限在 PUT 时能校验的（文档大小、条目数、内联大小）当场拒绝；只能在
fetch 时发现的（远端内容大小）按 `on_fetch_failure` 处理并记入 apply
report。

## 6. 非目标（v1 明确不做）

- **机制层文件操作**：不提供「往任意路径写文件」的条目类型。资源均以逻辑
  名/类型声明，物理位置永远是引擎的决定。
- **secrets**：不提供凭证存储或引用；source URL 不得含长期凭证。
- **`engine_ext`**：不可经 manifest 读写。
- **删除资产**：manifest 只管理声明集合与 managed 标记，不级联删除用户
  资产。
- **teclaw 的 script**：不支持，且不承诺未来支持（需 teclaw 侧出现容器内
  执行通道后另行评估）。
