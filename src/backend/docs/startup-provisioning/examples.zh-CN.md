# Manifest 完整案例集

> 状态：DRAFT（讨论稿）。本文对七类配置各给出一个完整案例：业务场景、今天
> 的人工做法（真实端点）、manifest 写法、apply 逐步动作、两个引擎家族各自的
> 交付形态。所有端点、字段、路径均取自现有业务代码，出处随文标注。
> 规范性定义见 `manifest-schema.zh-CN.md`，本文只作示例。

## 0. 贯穿场景：一个客服 bot 的完整置备文档

某业务团队运营一批「商家客服」bot（openclaw 引擎为主，部分租户在
teclaw）。他们的内容资产维护在自己的 CMS（Content Management System，
内容管理系统）服务上：人设 md、话术规范、质检 skill、常见问题数据。
CMS 在本文中泛指**业务方自建的内容服务**（虚构示例域名
`cms.example.com`）——静态文件服务、Git raw 接口、自研运营后台均可，
方案对它的全部要求只有：平台侧网络可达、支持 HTTPS GET（私有源配合
凭证引用）。今天每新开一个 bot 或每次内容更新，他们要按顺序
手工调 4~5 个 open API；容器重建后 bot 立即可用但内容可能滞后。

CMS 是私有源，**业务方先调用平台的 TC Open API**，做一次性的租户级凭证注册
（方向与上传 skill、写 identity 相同：业务方 → 平台）。此后每个 apply 点由
**平台的 fetcher 携带此凭证访问业务方的 CMS**（平台 → 业务方源站）——这是
平台对业务方唯一的出向调用。fetch 时凭证注入为 `Authorization` 头，且只会
发给 `cms.example.com`；secret 写后不可读回（`manifest-schema.zh-CN.md`
§2.1）：

```text
PUT /openapi/v1/provisioning/credentials/cms-token
{
  "header_name": "Authorization",
  "secret": "Bearer eyJhbGciOi…",
  "allowed_origins": ["https://cms.example.com"]
}
```

置备文档写一次，每个 bot 的每次拉起自动完成全部动作：

```yaml
schema_version: 1

manifest:
  engine_config:
    config:
      language: zh-CN
      reply_style: concise

  identity:
    - type: SOUL.md
      source: https://cms.example.com/bots/${OCB_BOT_ID}/soul.md
      auth: cms-token
    - type: RULES.md
      source: https://cms.example.com/kb/service-rules.md
      auth: cms-token
    - type: SAFETY.md
      content: |
        # 安全边界
        不承诺退款金额；涉及资损问题一律转人工。

  resources:
    - path: data/faq.csv
      source: https://cms.example.com/kb/faq.csv
      auth: cms-token
      on_fetch_failure: keep_last
    - path: data/kb/                         # 目录条目：整个知识库
      source: https://cms.example.com/kb/knowledge-base.zip
      unpack: zip
      strip_components: 1                    # 消掉 `zip -r kb.zip kb/` 的壳目录
      auth: cms-token
      on_fetch_failure: keep_last

  skills:
    - name: quality-check
      source: https://cms.example.com/skills/quality-check.zip
      auth: cms-token
      digest: "sha256:9f2c…"          # 质检逻辑要求可复现，钉住版本

  mcp:
    - server_code: mcp.ant.homistudio.meetmcp   # 会议信息服务（注册表真实条目）

script:                                # 仅 ARCA 系租户的 bot 会写这一段
  body: |
    #!/bin/bash
    set -euo pipefail
    # 从仅沙箱网络可达的内部服务取一份当日商家白名单
    curl -fsSL http://inner-ops.example.com/whitelist/today.json \
      -o "$HOME/workspace/data/whitelist.json"
```

apply 报告（`GET …/provisioning/last-apply`）：

```json
{
  "trigger": "republish", "result": "SUCCEEDED",
  "entries": [
    {"category": "engine_config", "name": "language,reply_style", "action": "updated"},
    {"category": "identity", "name": "SOUL.md", "action": "updated", "source_digest": "sha256:1a…"},
    {"category": "identity", "name": "RULES.md", "action": "unchanged"},
    {"category": "identity", "name": "SAFETY.md", "action": "unchanged"},
    {"category": "resources", "name": "data/faq.csv", "action": "updated"},
    {"category": "resources", "name": "data/kb/", "action": "unchanged", "source_digest": "sha256:7b…"},
    {"category": "skills", "name": "quality-check", "action": "unchanged", "source_digest": "sha256:9f2c…"},
    {"category": "mcp", "name": "mcp.ant.homistudio.meetmcp", "action": "unchanged"}
  ]
}
```

以下逐类展开。每类的「apply 做什么」都是对**现有服务**的调用编排——这正是
路线 B 的可信度来源：apply 没有任何私有旁路，每一步都等价于一次今天已经
存在的 open API 调用。

## 1. `mcp` — 为 bot 启用一个 MCP server

**场景**：客服 bot 需要会议信息能力，业务希望新扩出来的每个实例都带上
`mcp.ant.homistudio.meetmcp`（MCP 注册表真实条目，见
`core/mcp/services/_defaults.py`——平台已按引擎维护默认 MCP 集合，本类目
是把「默认集合之外的按 bot 增配」也变成声明）。

**今天的人工做法**：
1. `GET /openapi/v1/mcp/servers?keyword=会议` 找到 server_code；
2. 如需凭证/环境，`PUT /openapi/v1/mcp/servers/{server_code}/config`
   （字段 `api_key / headers / endpoint_env / transport_protocol`，合并写，
   写完即推送到设备，推送失败整体回滚——`openapi_v1/mcp/router.py`）；
3. 把 server 加进 bot 的 MCP 集合。

**manifest 写法**：

```yaml
mcp:
  - server_code: mcp.ant.homistudio.meetmcp
  - server_code: mcp.internal.example.kbsearch
    endpoint_env: PRE          # 可选：非默认环境
```

**apply 做什么**：校验 `server_code` 存在于注册表且租户有权限（复用现有
`check_mcp_permission` 路径）→ 确保它在该 bot 的 MCP 集合中。凭证**不在
manifest 里**：需要 `api_key` 的 server，其配置仍走现有统一配置存储；若
必需配置缺失，该条目记 `failed` 并给出明确错误（「server X 需要先配置
api_key」），不影响其余条目。

**交付**：ARCA 系走现有按-MCP 推送（设备 `/api/mcp` 路径）；teclaw 在
artifact 组装时进入 `mcp.servers[]`（`McpServerRef{server_code, name,
endpoint, transport, headers}`，凭证按现状于 compose 时从平台配置解出并
内联——`kernel/bot_config/artifact.py` 的既有机制，本设计不触碰）。

## 2. `resources` — 工作区数据文件

**场景**：质检 skill 需要一份 FAQ 对照表 `faq.csv`，内容每周更新。今天
业务在每次更新后对每个 bot 调一次 resources 上传；漏调的 bot 用旧表。

**今天的人工做法**：`POST /openapi/v1/bots/resources`（上传，workspace
相对 `path` 寻址）；列表/存在性检查同样按 `path`
（`openapi_v1/resources/router.py`——workspace 是文件资源的唯一权威，
路径经 `_safe_path` 防穿越）。

**manifest 写法**：

```yaml
resources:
  - path: data/faq.csv                    # workspace 相对路径，寻址规则与现有 API 完全一致
    source: https://cms.example.com/kb/faq.csv
    auth: cms-token                             # 租户级命名凭证的引用（schema §2.1）
    on_fetch_failure: keep_last           # CMS 抖动时沿用上一版，不阻塞拉起
```

**apply 做什么**：fetch 就是平台 guarded fetcher 对该 URL 的一次 HTTPS
GET；`auth: cms-token` 让它带上名为 `cms-token` 的凭证所声明的请求头
（凭证在创建时绑定了 `allowed_origins`，只会发给 `cms.example.com`，
secret 不出现在 manifest 里）→ 与上次物化的 digest 比对 → 变化则经现有
resource 写路径落盘并记 `updated`，未变记 `unchanged`。

**交付**：ARCA 系写入 bot 工作区（现有 device filesystem 路径，NAS 持久，
重建即见）；teclaw 物化进 bot-data store 后以
`ResourceRef{name, store, path}` 进 artifact——与今天手工上传的资源在
artifact 里**无法区分**（对照 `artifact.py` 中 `EXAMPLE_ARTIFACT` 的
`resources` 条目：`path="staff_u123/bot7/openclaw/workspace/data/sales.csv"`）。

**目录形态**：知识库这类含大量子文件夹/文件的资源，不逐文件声明——HTTP
的一个 URL 只能是一个字节流，「文件夹」必须以归档为约定形态整体运输：

```yaml
resources:
  - path: data/kb/                           # 展开根（workspace 相对，/ 结尾）
    source: https://cms.example.com/kb/knowledge-base.zip
    unpack: zip
    strip_components: 1                      # 业务 `zip -r kb.zip kb/` 打出的壳目录，剥一层
    auth: cms-token
    on_fetch_failure: keep_last
```

`kb.zip` 内的 `kb/intro.md`、`kb/faq/refund.md` 落地为 `data/kb/intro.md`、
`data/kb/faq/refund.md`。**收敛单位是整个归档**：内容 hash 未变即
`unchanged` 零动作（几百个文件也不逐一比对）；变化则 temp 解包 + 原子
rename 整树替换——归档里删掉的文件随之消失，手工塞进该目录的文件也会被
清（目录级声明获胜，schema §3.2）。teclaw 侧 compose 时逐文件展开为
`ResourceRef`，契约零改动。

**注意**：`type: link` 类资源（仅记录、无文件的链接资源，现有 API 支持）
可作为 v1.x 扩展条目类型，本期先不进 schema。

## 3. `skills` — Local Skill

**场景**：质检 skill `quality-check` 由业务的算法团队维护，产出物是标准
skill zip（内含 `SKILL.md` 元数据——`skill_parser.py`：SKILL.md 优先、
README.md 兜底）。要求：全量 bot 生效、版本一致、可审计当前线上版本。

**今天的人工做法**：对每个 bot 依次
1. `POST /openapi/v1/bots/skills/upload?bot_id=…`，body 为原始
   `application/zip`（201 新建 / 200 安全替换 / 413 超限——
   `openapi_v1/skills/router.py`）；上传产物是 **inactive** 状态；
2. `POST /openapi/v1/bots/skills/{skill_id}/activate` —— 同步 reconcile
   运行时。

**manifest 写法**：

```yaml
skills:
  - name: quality-check
    source: https://cms.example.com/skills/quality-check.zip
    auth: cms-token
    digest: "sha256:9f2c…"      # 声明即锁版：digest 变了才算新版本
```

**apply 做什么**：fetch zip → digest 校验 → 走现有 upload 服务
（created/updated 语义照旧）→ activate。`unchanged` 时零动作——skills 是
六类里体量最大的，收敛比对（digest）避免了每次拉起重传 zip。

**交付**：ARCA 系照现有 skill 交付（activate 的同步 reconcile 现状生效）；
teclaw 物化进 bot-data store 后以
`SkillRef{name, scope="user", store, path}` 进 artifact，路径形状即现状的
`…/workspace/skills/skills-local/quality-check`（对照 `EXAMPLE_ARTIFACT`
与 `config_compose/teclaw_paths.py` 的 `skills-local/<skill>/SKILL.md`
记录形状）。

**与 skills-pool reconcile 的关系**：因为 apply 走的就是正规 upload +
activate，落成的 Local Skill 对 reconcile / quarantine 完全可见——不存在
「侧载目录被当 drift 清掉」的问题。这是路线 B 相对「脚本直接写文件系统」
的决定性优势，值得在示例里点名。

## 4. `engine_config` — 引擎配置

**场景**：业务要求这批 bot 统一 `language: zh-CN`、回复风格 `concise`，
且新租户开 bot 时不允许漏配。

**今天的人工做法**：`PUT /openapi/v1/bots/{bot_id}/engine-config`
（`openapi_v1/bots/router.py`）。它 provider-blind 地读写 bot 设备上逻辑
路径 `config/teclaw.json` 的 JSON 文档（文件名是历史命名，**并非 teclaw
专用**——`core/services/engine_config.py` 的 `_CONFIG_LOGICAL_PATH`，
按 provider 由 `DeviceFilesystemDispatcher` 映射到物理位置）。

**manifest 写法**：

```yaml
engine_config:
  config:
    language: zh-CN
    reply_style: concise
```

**apply 做什么**：读现有配置 → 对**声明的顶层键**逐键覆盖（`language`、
`reply_style` 归 manifest 管；文档里其他键——引擎或用户后来写入的——
一概不碰）→ 经 `EngineConfigServiceProtocol.write_bot_config` 写回。
键的语义归引擎所有，平台只做形状校验，与现有 PUT 一致。

**交付**：两个家族走同一条 provider-blind 写路径——分派器把 teclaw 的
engine-config 落点解析为逻辑路径 `config/teclaw.json`，经
`TeclawDeviceFileSystem` 转发到引擎的 `/api/v1/file/upload`，由引擎落到
自己的挂载上（`DeviceFilesystemDispatcher.engine_config_path` 现状）。
artifact **不为此类目新增或启用任何字段**（`engine_overrides` 保持不用）；
唯一待确认的是新建 bot 场景下该文件到达首个实例的时序（待确认项 T3，见
`engine-requirements.zh-CN.md`）。

**注意**：`engine_ext` 与此类目无关且永不可经 manifest 触碰——那是引擎
自有的不透明数据（`artifact.py`：backend「存储原样、永不解释」）。

## 5. `identity` — 人设/规则文件

**场景**：人设集中运营——SOUL.md 按 bot 从 CMS 取（每个 bot 一份），
RULES.md 全体共享一份话术规范，SAFETY.md 是三行红线、直接内联。

**今天的人工做法**：逐文件
`PUT /openapi/v1/bots/identity/{bot_id}/{file_type}`（`file_type` 为白名单
枚举：RULES / OKR / SAFETY / SOUL / OUTPUT / MEMORY / IDENTITY / AGENTS /
USER / TOOLS / HEARTBEAT / BOOTSTRAP / KNOWLEDGE / CLAUDE / GREETING /
README，物理文件为 `<type>.md`——`openapi_v1/identity/schemas.py`）。

**manifest 写法**：

```yaml
identity:
  - type: SOUL.md
    source: https://cms.example.com/bots/${OCB_BOT_ID}/soul.md   # 平台注入变量按 bot 取内容
    auth: cms-token
  - type: RULES.md
    source: https://cms.example.com/kb/service-rules.md
    auth: cms-token
  - type: SAFETY.md
    content: |
      # 安全边界
      不承诺退款金额；涉及资损问题一律转人工。
```

**apply 做什么**：逐条 fetch（或取内联）→ 经现有 IdentityService 写入。
因为走的是同一服务，现有派生行为原样生效——例如 REFERENCE_FILES
（RULES/OKR/SAFETY/OUTPUT）向 AGENTS.md 的同步（`core/services/identity.py`），
manifest 用户无需知道这个机制的存在。

**交付**：ARCA 系走现有 identity 交付（openclaw 落在
`…/workspace/` 下，路径差异由 `path_factory` 现状处理）；teclaw 物化后以
`FileRef{name, store, path}` 进 `identity_files[]`。

**两条边界**：
- 引擎按 bot 校验合法集：**claude_code 引擎的 bot 仅允许 `CLAUDE.md`**
  （`CLAUDE_CODE_IDENTITY_FILES` 现状规则），写入时报错而非 apply 时跳过；
- MEMORY.md 等引擎运行期生成的文件技术上可声明，但声明获胜语义会在每个
  apply 点重置它们——文档如实警示，人设/规则类（SOUL/RULES/SAFETY…）才是
  本类目的主场景。

## 6. `script` — 命令式长尾（ARCA 系专属）

**场景**：当日商家白名单存在一个**仅沙箱网络可达**的内部运维服务上，且
需要在拉起时才确定内容——平台侧 fetch 够不到，属于声明式无法吸收的残留。

**manifest 写法**（承接 §0 的 `script` 段）：

```yaml
script:
  body: |
    #!/bin/bash
    set -euo pipefail
    curl -fsSL http://inner-ops.example.com/whitelist/today.json \
      -o "$HOME/workspace/data/whitelist.json"
```

**执行语义**（全部为 #935 现状，本设计不改动）：以 `admin` 身份、300s
超时、base64 免注入封装、输出在容器内
`/home/admin/logs/startup_script.log`、退出码不影响平台就绪判定。
顺序保证是本设计新增的唯一承诺：**script 执行时，manifest 声明的实体已经
就位**——上例中 script 可以放心假定 `data/faq.csv`、`quality-check` skill
已存在。

**能力边界**：teclaw / desktop / LOCAL / ARCA-direct 遗留形态在 `PUT` 时
即拒绝（fail closed），错误信息指明原因；业务据此在建 bot 时就知道该租户
能不能用 script，而不是启动后静默不执行。

## 7. `cli_tools` — 给模型调用的命令行工具（排期后置）

**场景**：业务有一个内部数据查询 CLI `shopctl`（静态 Go 二进制），希望
每个客服 bot 里都有，agent 处理工单时自己 bash 调它查订单。

**今天的做法（仓库内的手工先例）**：`bcs-cli` 正是这个模式——二进制目录
挂进 openclaw gateway 的 PATH（`scripts/modules/bots.sh`），配一个
`SKILL.md`（`allowed_tools: [exec]`）教 agent 用法。全程手工、不可复现、
容器重建即失效。本类目是它的产品化。

**manifest 写法**：

```yaml
cli_tools:
  - name: shopctl
    source: https://cms.example.com/tools/shopctl-linux-amd64
    auth: cms-token
    digest: "sha256:…"           # 本类目强制：平台代发可执行物，供应链钉死
    version: "2.3.0"             # 进 apply report，审计线上版本
```

**apply 做什么**：fetch → digest 校验（不符即 `failed`）→ 落入平台定义的
逻辑「工具目录」（NAS 持久）+ 置可执行位；PATH 注入由平台保证（注入点见
engine-requirements A2）。收敛以 digest 为准，未变零动作。

**配套**：安装只保证「`shopctl` 在 PATH 上」；agent 怎么知道并正确使用它，
推荐照 `bcs-cli` 双件套——skills 类目里配一个教用法的 skill，或 identity
里写 `TOOLS.md`。

**能力**：ARCA 系支持；**teclaw 待确认（T4）**——可执行位 + PATH 注入 +
用户二进制的沙箱策略，与 script 的能力边界相邻，须 teclaw 表态；v1 只做
静态二进制/压缩包，包管理器安装走 script。

## 8. 案例对照总表

| 类目 | 场景一句话 | 等价的人工调用 | apply 动作 | teclaw 落点 |
| --- | --- | --- | --- | --- |
| mcp | 全量实例带会议 MCP | mcp servers/config API | 权限校验 + 入 bot MCP 集合 | `mcp.servers[]` |
| resources（文件） | FAQ 表每周更新自动同步 | resources 上传 API | fetch → digest 比对 → 资源写路径 | `resources[]` |
| resources（目录） | 整个知识库随源收敛 | 无（逐文件上传不可行） | fetch 归档 → 解包守卫 → 原子整树替换 | `resources[]`（逐文件展开；子树引用见 T5） |
| skills | 质检 skill 锁版全量生效 | skills upload + activate | fetch → upload(created/updated) → activate | `skills[]`（scope=user） |
| engine_config | 语言/风格统一且不可漏配 | engine-config PUT | 声明键逐键覆盖 → provider-blind 写 | 既有 `config/teclaw.json` 文件通道（非 artifact 字段，T3） |
| identity | 人设集中运营、红线内联 | identity PUT × N | fetch/内联 → IdentityService 写 | `identity_files[]` |
| cli_tools | 内部查询 CLI 全量可用 | 手工挂 PATH（bcs-cli 模式） | fetch → digest 强校验 → 工具目录 + PATH | 待确认（T4） |
| script | 沙箱内网取当日白名单 | ssh/手工（无 API） | #935 启动链现状 | ——（不支持） |

「apply 动作」列的新机制只有三个：guarded fetcher、归档解包（复用 skills
zip 守卫）、工具目录 + PATH 注入；其余全部是对现有服务的编排。这就是本
设计的实现面与说服点。
