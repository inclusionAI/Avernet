# Skills 管理与发布机制：现状梳理及升级方案

> **定位**：技术架构说明，面向跨团队技术同步与架构评审。
>
> **范围**：说明当前已生效的 Skills 管理、发布与运行时加载机制，以及受管 Skill Center（SC）接入后的目标架构。
>
> **现状范围**：当前已生效的来源仅为 Git 公共 Skill 与 Local 自定义上传 Skill。SC 仅在“目标架构”章节中作为新增能力说明。

## 1. 背景与目标

Skill 是 Agent 在运行时加载的一组结构化能力文件，核心入口通常是 `SKILL.md`。Skill 的内容管理、发布分发和运行时加载横跨 Backend、存储、设备和 Engine；因此其架构需要同时保证：

- 公共能力可以稳定同步和分发；
- 用户自定义能力能够按 Bot 隔离与持久化；
- 运行时只加载被当前 Bot 能力集激活的 Skill；
- 发布内容与运行中的文件系统保持可追溯、可治理。

本次改版的目标是：在保留 Git 公共 Skill 数据源的前提下，引入 SC 承载新的私有及空间自主管理 Skill，形成多数据源共存、统一元数据与统一运行时激活的架构。

本文件不讨论具体产品交互、历史迁移节奏、发布排期或回滚预案。

## 2. 术语与边界

| 名称 | 含义 |
| --- | --- |
| `skills-repo` | Git 公共 Skill 的共享内容目录。 |
| `skills-local` | 当前用户上传 Skill 的 Bot 私有可写目录。 |
| SkillSet | Bot 的能力集；决定哪些 Skill 在该 Bot 运行时可见。 |
| 运行时激活 | Backend 将当前 SkillSet 转换为 `source -> target` 软链映射，并由 Engine 对账。 |
| Git 同步服务 | 当前 Git 公共 Skill 的同步、扫描和制品发布服务。 |
| Skill Center（SC） | 目标架构中的受管 Skill 内容、版本、扫描和发布系统。 |

当前生效的数据来源前缀用于表达内容的权威来源，而不是运行时目录：

| 前缀 | 当前状态 | 内容权威来源 | 目标定位 |
| --- | --- | --- | --- |
| `git://` | 已生效 | Git | 公共市场 Skill，长期保留。 |
| `local://` | 已生效 | Bot 私有目录 | 存量自定义上传 Skill。 |

## 3. 当前架构

### 3.1 数据面

当前线上有效数据面只有 Git 公共 Skill 与 Local 自定义上传 Skill：

```text
                    ┌─────────────────────┐
                    │   Git 公共 Skill     │
                    │ 公共 Git 仓库 / master │
                    └──────────┬──────────┘
                               │ GitSyncService
                               v
                    ┌─────────────────────┐
                    │  只读 skills-repo     │
                    │ 完整公共内容集合（内容库）│
                    └──────────┬──────────┘
                               │
              ┌────────────────┴────────────────┐
              v                                 v
     云端 Bot：只读挂载                 桌面端 Bot：ETag 拉取
              │                                 │
              └────────────────┬────────────────┘
                               v
                    ┌─────────────────────┐
                    │ Engine 运行时目录    │
                    │ 逐 Skill 软链激活    │
                    └─────────────────────┘

用户上传 ──> Backend 校验 SKILL.md / 登记元数据
                               │
                               v
                    Bot 私有 skills-local（RW）
                               │
                               └────> 同一套 Engine 软链激活
```

两条链路最终都不会让 Agent 直接改写 `skills-repo`。公共内容以共享只读目录提供；上传内容存放在 Bot 的私有数据目录中。
### 3.2 Git 公共 Skill 的管理与发布

Git 公共 Skill 的权威来源是公共 Git 仓库的 `master` 分支。代码合并至 `master` 后，新的 Skill 内容即进入公共发布基线。

Git 同步服务通过定时任务或手动触发执行同步：识别仓库中新增和变更的 Skill，解析并增量刷新 Skill 元数据，再将公共内容发布到运行时分发目录。元数据用于市场展示和检索，文件内容用于各运行环境加载。

因此，Git 既是公共 Skill 的版本基线，也是持续同步和运行时分发的来源，而不是一次性的初始化输入。

### 3.3 Local 自定义上传 Skill

用户通过上传接口提交包含 `SKILL.md` 的 Skill 内容。Backend 校验和解析内容后：

1. 登记 Skill 元数据；
2. 将文件写入该 Bot 的 `workspace/skills/skills-local/<skill-name>/`；
3. 在该 Skill 被激活时，将实际目录链接到 Engine 可发现的 Skill 目录。

`skills-local` 属于 Bot 数据的一部分，因此具有 Bot 级隔离和可写属性。它不是共享市场资产，也不依赖 Git 或 SC。

### 3.4 云端 Bot 与桌面端 Bot 的公共 Skill 分发差异

两端都消费同一份 Git 公共 Skill，但内容到达容器/虚拟机的方式不同：

| 部署形态 | `skills-repo` 到达方式 | 是否由 Backend 通过设备 API 写入 |
| --- | --- | --- |
| 云端 Bot | 运行容器直接以只读方式挂载共享对象存储中的 `skills-repo`，内容随挂载可见。 | 否。 |
| 桌面端 Bot | 桌面运行时内的 Engine 获取制品元信息，基于 ETag 判断是否更新，下载整包并原子替换本地 `skills-repo`。 | 否。 |

这个差异是目标架构必须保留的约束：云端 Bot 是只读挂载模型，桌面端 Bot 是自主拉取和原子替换模型；不能将两者简单视为同一条文件同步链路。

> `local` 是本地开发模式下的直接文件系统实现，不属于桌面端 Bot 的内容分发链路。生产运行形态应以“云端 Bot / 桌面端 Bot”区分，而不应依赖具体基础设施产品名。

### 3.5 当前多引擎目录矩阵

当前文件型引擎都区分三个目录概念：

- **active-skill-dir**：引擎扫描和 Agent 发现 Skill 的入口；
- **skills-repo-dir**：Git 公共 Skill 的完整内容目录；
- **skills-local-dir**：当前 Bot 已上传 Local Skill 的完整内容目录。

当前目录布局如下。Claude Code 的 active-skill-dir 是 CLI 扫描入口；其 `skills-repo-dir` 与 `skills-local-dir` 位于 `.claude_code` 下，并通过 `.claude/skills/` 内的兼容软链暴露给 CLI。除这些引擎专用目录外，完整内容集合（内容库）仍可从 active-skill-dir 进入：

| 引擎 | active-skill-dir | skills-repo-dir | skills-local-dir |
| --- | --- | --- | --- |
| OpenClaw | `~/.openclaw/workspace/skills` | `~/.openclaw/workspace/skills/skills-repo` | `~/.openclaw/workspace/skills/skills-local` |
| Claude Code | `~/.claude/skills` | `~/.claude_code/skills-repo` | `~/.claude_code/workspace/skills/skills-local` |
| AICoding | `~/.claude/skills` | `~/.aicoding/skills-repo` | `~/.claude/skills/skills-local` |
| Hermes | `~/.hermes/skills` | `~/.hermes/skills-repo` | `~/.hermes/skills/skills-local` |
| Teclaw | 引擎自行管理 | 引擎自行管理 | 引擎自行管理 |

其中，`skills-repo-dir` 与 `skills-local-dir` 是完整内容仓库；`active-skill-dir` 本应只表达当前 Bot 已激活的 Skill 集合。Claude Code 虽经兼容软链做了物理目录隔离，但 CLI 扫描入口仍能到达完整内容集合；当前布局整体上仍没有稳定地分离这两个概念。

### 3.6 当前目录布局带来的问题

当前 active-skill-dir 同时承载完整内容集合和逐 Skill 激活软链。例如 OpenClaw 的目录同时包含 `skills-local/`、`skills-repo/`，以及指向这两个目录中具体 Skill 的激活软链：

```text
~/.openclaw/workspace/skills/
├── skills-local/                 # 当前 Bot 上传过的完整 Local 内容集合
├── skills-repo/                  # 完整公共 Skill 内容集合
├── my-skill -> skills-local/my-skill
└── report-skill -> skills-repo/.../report-skill
```

这会带来三个直接问题：

1. **阻塞 OpenClaw 新版本升级**：新版 OpenClaw 会递归扫描受信任目录和软链目标。active-skill-dir 内保留完整内容集合时，扫描器会将未激活的内容一并识别为可用 Skill，不能安全升级到递归扫描能力。
2. **Agent 看到的集合超出用户配置**：当用户让 Agent 查询“有哪些 Skill”时，Agent 可能通过递归 `find` 等方式发现完整公共仓库或未激活的 Local Skill，而不是仅发现当前能力集中的 Skill。这会造成“明明没有启用却被发现”的体感问题。
3. **取消激活不等于内容不可发现**：取消激活当前只删除 active-skill-dir 下的逐 Skill 软链；内容仍保留在 `skills-local-dir`，以保证用户资产不被误删。但由于该目录仍位于或可从 active-skill-dir 进入，Agent 仍可能发现这部分存量内容，表现为“取消后没有删除干净”。
4. **目录契约分散且被 Backend 感知**：active、repo、local 等引擎目录目前分别硬编码在 Backend 的路径工厂和 SkillSet 服务、各 Engine 实现、部署脚本，以及运行时镜像的 `entrypoint.sh`、挂载和准备脚本等位置。新增引擎、调整目录或切换挂载方式时，需要跨多处同步修改，容易出现路径不一致和行为漂移。

前 3 个问题的根因是“内容仓库”和“生效 Skill 集合”共用了同一个可扫描目录边界；第 4 个问题则是引擎目录的所有权没有收敛到 Engine。

### 3.7 SkillSet 激活与运行时软链

当前实现中，无论 Skill 内容来自 Git 还是 Local，Backend 都会根据 Bot 的有效 SkillSet 计算实际内容目录 `source` 和 Engine 可发现目录 `target`，再由 Engine 进行增量软链对账。

```text
Bot SkillSet
  -> Backend 解析每个 Skill 的来源与实际目录
  -> [source, target] 映射列表
  -> Engine skills router
  -> 创建 / 更新 / 删除运行时软链
  -> Agent 从 target 目录发现 SKILL.md
```

此处的软链用于实现“共享内容、按 Bot 激活”：内容目录可以被多个 Bot 消费，但只有被能力集激活的 Skill 才会出现在该 Bot 的可发现目录中。

该契约也是目录硬编码扩散的直接原因：Backend 为了生成物理路径，需要了解每种 Engine 的目录形态。目标架构将把这一职责下沉到 Engine。

## 4. 目标架构

### 4.1 总体模型：多源治理，统一激活

目标架构不以 SC 替换 Git，而是让不同来源承担各自明确的职责：

```text
控制面：定义“什么内容、什么版本、对哪个 Bot 生效”

                    ┌──────────────────────────────┐
                    │          Backend               │
                    │ 元数据 / 版本 / SkillSet / 引用 │
                    └──────────────┬───────────────┘
                                   │ 逻辑激活请求
                                   │ source kind + locator + version + link_name
                                   v
                    ┌──────────────────────────────┐
                    │            Engine              │
                    │  目录解析 / 挂载 / 软链对账 / 发现 │
                    └──────────────────────────────┘

数据面：提供“实际内容在哪里”

 Git 公共 Skill                  SC 管理 Skill                 Local 上传 Skill
      │                                │                              │
      v                                v                              v
 skills-repo (RO)              skills-center (RO)            skills-local (RW)
      │                                │                              │
      └──────────────────┬─────────────┴─────────────┬────────────────┘
                         │       skills-pool          │
                         └───────────┬────────────────┘
                                     │ 逐 Skill source
                                     v
                              active-skill-dir
```

职责边界如下：

| 组件 | 责任 |
| --- | --- |
| Git | 公共市场 Skill 的内容基线与同步来源。 |
| SC | 私有 Skill 的内容包、版本、扫描、发布与下载。 |
| Backend | Skill 元数据、空间归属、权限、Bot 引用关系和逻辑激活编排；不维护引擎物理目录。 |
| 对象存储 / Skills Pool | 已发布公共与 SC Skill 的只读运行时内容分发。 |
| Engine | 唯一维护各引擎的目录布局、挂载和路径解析；接收逻辑激活请求，解析物理 source/target 并完成软链对账。 |

### 4.2 运行时目录布局

目标布局将公开与 SC 内容置于对偶的只读分发目录，Local 保留为存量私有可写目录：

```text
<workspace>/
├── skills-pool/
│   ├── skills-repo/                       # Git 公共 Skill，只读
│   │   └── <git-relative-path>/
│   ├── skills-center/                     # SC Skill，只读
│   │   └── <space_key>/<skill_uuid>/
│   │       └── v<N>/<skill_name>/
│   │           └── SKILL.md
│   └── skills-local/                      # 历史 Local Skill，Bot 私有 RW
│       └── <skill_name>/
│           └── SKILL.md
└── skills/                                # 当前 Bot 的激活软链
    └── <skill_name> -> 对应来源中的实际目录
```

以一个同时使用公共、私有和 Local Skill 的 Bot 为例，运行时文件关系如下：

```text
<workspace>/
├── skills-pool/                           # 完整内容仓库，不作为发现入口
│   ├── skills-repo/security/log-query/    # Git 公共 Skill（RO）
│   ├── skills-center/team-a/<uuid>/
│   │   └── v3/risk-review/                # SC 已发布 Skill（RO）
│   └── skills-local/data-cleaner/         # Bot 私有上传 Skill（RW）
│
└── skills/                                # active-skill-dir，仅含已激活入口
    ├── log-query   -> ../skills-pool/skills-repo/security/log-query
    ├── risk-review -> ../skills-pool/skills-center/team-a/<uuid>/v3/risk-review
    └── data-cleaner -> ../skills-pool/skills-local/data-cleaner
```

因此，取消激活 `data-cleaner` 时只移除 `skills/data-cleaner` 这一入口；内容仍安全保留在 `skills-pool/skills-local/data-cleaner`，但不再处于 Agent 的发现边界内。

统一 Pool 改造的关键是把“内容仓库”和“生效 Skill 集合”物理分开：

- `skills-pool` 保存完整内容集合；
- active-skill-dir 只保留逐 Skill 的直接激活入口；
- active-skill-dir 不再保留指向完整 `skills-repo` 或 `skills-local` 的内容集合入口（内容库桥接）；
- 因此递归扫描只能发现用户明确激活的 Skill，而不会沿目录入口发现完整仓库。

P3 的多引擎目录契约如下：

| 引擎 | active-skill-dir | Pool repo | Pool local |
| --- | --- | --- | --- |
| OpenClaw | `~/.openclaw/workspace/skills` | `~/.openclaw/workspace/skills-pool/skills-repo` | `~/.openclaw/workspace/skills-pool/skills-local` |
| Claude Code | `~/.claude/skills` | `~/.claude_code/workspace/skills-pool/skills-repo` | `~/.claude_code/workspace/skills-pool/skills-local` |
| AICoding | `~/.claude/skills` | `~/.aicoding/workspace/skills-pool/skills-repo` | `~/.aicoding/workspace/skills-pool/skills-local` |
| Hermes | `~/.hermes/skills` | `~/.hermes/workspace/skills-pool/skills-repo` | `~/.hermes/workspace/skills-pool/skills-local` |
| Teclaw | 不参与本期文件型 Pool 协议 | 不变 | 不变 |

Pool repo 是完整 Git corpus 的 canonical 真实目录或挂载点，不能是指回 Legacy
active root 的软链。Cloud 把 OSS 内容直接挂载到 Pool repo；Desktop downloader
把 target、临时解压、单份 backup 与 ETag 状态放在同一 Pool root 下。Desktop
preparation 原子迁移旧真实 repo 后，只在 cutover 前保留
`legacy_repo -> pool_repo` 反向兼容桥；cutover 验证逐 Skill mapping 后移除 active
root 可达的完整 corpus 入口。同一 runtime 内，canonical repo move、历史 locator
bridge 发布与结构校验在 Pool root 的本地 advisory directory lock 内完成，使并发启动
收敛到同一布局；该锁不跨 Bot、不持久化业务状态，也不是分布式 rollout 锁。

SC 接入后，`skills-center` 作为 `skills-pool` 下与 `skills-repo`、`skills-local` 并列的第三个内容目录；它同样不进入 active-skill-dir，只能通过逐 Skill 激活入口暴露给 Agent。

`skills-repo` 与 `skills-center` 只读是架构约束，而不是实现细节。已发布 Skill 的运行时内容不可被 Agent 对话直接修改；写操作必须经过相应的治理和发布路径。

### 4.3 引擎目录所有权与统一激活 API

目标架构中，Backend 不再拼接或解释任何引擎物理目录，例如 `~/.openclaw/...`、`~/.claude_code/...`。路径、挂载和兼容桥的所有权统一收敛到 Engine Runtime。

当前的问题不是“缺少一个路径配置文件”，而是多个独立发布单元各自维护路径表，彼此没有唯一事实来源：

```text
现状：同一份引擎目录知识散落在多个位置

 Backend                         Runtime / 镜像                         运行时消费者
 ┌────────────────────┐          ┌────────────────────────┐             ┌──────────────────┐
 │ path_factory        │          │ entrypoint.sh          │             │ OpenClaw plugin  │
 │ SkillSet 映射生成   │          │ mount_storage.sh       │             │ Claude Code      │
 │ 部署目录准备        │          │ prepare_skills_pool.py │             │ AICoding         │
 └─────────┬──────────┘          └───────────┬────────────┘             │ Hermes           │
           │                                 │                          └────────┬─────────┘
           │  各自硬编码 / 维护路径表         │                                   │
           └────────────────┬────────────────┴───────────────────────────┘
                            v
        ~/.openclaw/...  ~/.claude_code/...  ~/.aicoding/...  ~/.hermes/...

 服务启动与部署工具也维护独立的运行时路径规则
```

这会导致某个引擎目录或挂载方式变更时，必须跨 Backend、镜像、部署工具和多个 Engine 插件同步修改；任一处滞后就可能出现目录漂移。当前已知的 Claude Code、AICoding、Hermes 路径差异正是这一架构债务的表现。

目标态引入版本化、只读的 `EngineSkillLayoutDescriptor` 和唯一的 `EngineSkillLayoutResolver`：

```text
目标：Engine Runtime 成为唯一的目录事实来源

                     ┌───────────────────────────────────┐
                     │       Engine Runtime               │
                     │ EngineSkillLayoutDescriptor        │
                     │  - active / legacy / pool roots    │
                     │  - structural compatibility links  │
                     │  - filesystem / artifact kind      │
                     │ EngineSkillLayoutResolver           │
                     └──────────────┬────────────────────┘
                                    │ 统一解析接口
          ┌─────────────────────────┼─────────────────────────┐
          v                         v                         v
   镜像启动与挂载脚本          Engine Skills 插件       服务启动与部署工具
          │                         │                         │
          └─────────────────────────┴─────────────────────────┘
                                    │
                                    v
                       各引擎真实目录、挂载和激活入口

 Backend ── 仅下发逻辑状态与 Skill 激活意图 ──> Engine Runtime
```

Descriptor 至少覆盖 active 入口、Legacy/Pool 的 local 和 repo 根目录、兼容桥，以及 Teclaw 的显式 artifact 行为。未知引擎或未知布局契约必须失败关闭，不能默认回退为 OpenClaw。

Resolver 使用 Engine 持久化的逻辑引擎身份和当前运行时 home 解析拓扑：

```text
LayoutIdentity {
  engine_type
  layout_contract_version
}

RuntimeLayoutContext {
  home
}

ResolvedSkillLayout {
  engine_type
  layout_contract_version
  capability
  active_root
  legacy_local
  legacy_repo
  pool_root
  pool_local
  pool_repo
  ready_marker
  active_marker
  structural_bridges
}
```

一次解析同时返回 Legacy 与 Pool 的完整拓扑。Resolver 不读取 marker、Backend
状态或 rollout 配置，不执行文件操作，也不选择当前权威布局。preparation、
probe、activate、mapping、rollback 和 cleanup 根据各自协议消费命名路径；
Backend 状态机负责决定 CRUD、mapping 和 locator 当前使用 Legacy 还是 Pool。

repo 的挂载或下载属于 provider 的交付方式，不改变引擎目录身份，也不进入
Resolver 输入。Cloud 与 Desktop 对同一逻辑引擎解析得到相同 descriptor，差异
只存在于内容如何到达这些路径以及文件操作由谁执行。

`aicoding` 与 `claude_code` 是不同的逻辑布局身份。两者共用
`~/.claude/skills` 作为 CLI active 入口，但分别使用 `~/.aicoding` 与
`~/.claude_code` 存储 Legacy/Pool 内容。既有 Skills Service 继续负责 physical
路径转换和 CLI 兼容桥；Resolver 不用实现别名归一化替代 Bot 的逻辑身份。

Skill 激活本身继续以逻辑来源表达，不携带物理路径：

```text
{
  source_layout: "pool",
  desired_skills: [
    {
      link_name: "risk-review",
      source: {
        scheme: "center",           # git / center / local
        locator: "<skill_uuid>",
        version: 3,
        space_key: "team-a"
      }
    }
  ]
}
```

Engine 对外提供统一的 Skill 激活 API，并在内部完成以下工作：

```text
逻辑激活请求
  -> Engine Skill Layout Resolver
      -> 根据当前引擎确定 active-skill-dir 与 Skills Pool 目录
      -> 根据 source.scheme / locator / version 解析真实内容路径
      -> 确保只读挂载或本地目录可用
      -> 对账逐 Skill 软链
  -> 返回激活结果与诊断信息
```

过渡期间，如果 Backend 必须持久化物理 locator，Engine Runtime 应返回已解析的 locator 与校验证据；Backend 只保存结果，不再自行重建路径。

产品激活集合是受管逐 Skill mapping 的权威源。若 activate/deactivate 与数据面
cutover 并发，Backend 在 cutover 提交后重读最新集合，并把旧快照中已退出的精确
identity 作为 `retired_mappings` 与最新完整 mapping 一起发布和验证；提交
`POOL_ACTIVE` 前再次确认快照稳定。快照变化只重跑 post-cutover mapping 收敛，
不重复数据面 cutover。Engine 只删除仍指向该精确旧受管 source 的入口，同名最新
mapping、未登记文件系统 entry 与外部 entry 均必须保留。

这样，Backend、部署工具和镜像启动脚本不再各自维护引擎路径常量；它们只负责向 Engine Runtime 请求解析或提供内容分发所需的输入，目录布局的演进由 Engine 单点维护。

### 4.4 统一标识与版本模型

为避免路径、展示名称和跨系统身份相互耦合，一个 Skill 使用三类标识：

| 字段 | 生命周期 | 用途 |
| --- | --- | --- |
| `skill_uuid` | 创建后不可变 | 路径身份、数据库主索引。 |
| `skill_code` | 创建后不可变 | SC 业务标识，格式为 `{space_key}_{skill_name}_{user_id}_{env}`。 |
| `skill_name` | 创建后不可变 | `SKILL.md` 名称、运行时软链名。 |
| `description` | 可更新 | 展示说明。 |

数据模型分为 Skill 主体与不可变版本快照：

| 表 | 核心内容 |
| --- | --- |
| `ac_skill` | Skill 身份、来源、所属空间、所有者、`current_version_id`。 |
| `ac_skill_version` | 整数版本、发布状态、内容位置、SC 版本映射、校验和扫描结果。 |

`ac_skill.current_version_id` 是当前生效版本的唯一结构化指针。运行时映射直接使用该指针，避免通过“多行状态过滤”或“最大版本聚合”猜测当前版本。

### 4.5 三源逻辑激活规则

目标架构下，Backend 只描述 Skill 的逻辑来源，物理路径由 Engine 解析。不同来源的业务定位如下：

| 来源 | 逻辑 locator | Engine 内部解析 |
| --- | --- | --- |
| `git://<rel>` | Git 仓库相对路径 | 从该引擎的 Pool repo 目录定位内容。 |
| `center://<uuid>` | `skill_uuid`、`space_key`、版本 | 从该引擎的 Pool center 目录定位内容。 |
| `local://<name>` | Bot 私有 Skill 名称 | 从该引擎的 Pool local 目录定位内容。 |

Engine 仍不负责 Git、SC 或 Local 的业务治理；它只负责把逻辑 locator 映射到自身的物理目录，并维持 active-skill-dir 的逐 Skill 激活入口。

## 5. 目标核心链路

### 5.1 Git 公共 Skill

Git 公共 Skill 继续沿用现有 Git 同步服务：Git 同步、扫描、缓存刷新、制品发布和各运行环境消费方式保持不变。SC 改版不改变 Git 公共市场的权威来源。

### 5.2 SC Skill 发布

SC 承接新增的私有和空间自主管理 Skill。发布链路为：

```text
内容编辑完成
  -> Backend 写入草稿版本元数据
  -> 调用 SC 上传、扫描、发布接口
  -> SC 生成指定版本的发布包与下载地址
  -> 发布内容进入 skills-center 的只读分发目录
  -> Backend 将 current_version_id 指向已发布版本
  -> Backend 向引用该 Skill 的 Bot 下发逻辑激活请求
  -> Engine 刷新该 Bot 的逐 Skill 激活入口
```

发布后的版本不可原地修改；新内容以新整数版本发布。这样，内容版本、SC 发布记录、只读文件目录和 Bot 激活映射能够一一对应。

### 5.3 Bot 激活、升级和下线

- **激活**：Backend 下发当前版本的逻辑标识，Engine 解析实际内容目录并创建激活入口。
- **升级**：创建并发布新版本，切换 `current_version_id`，再按引用关系刷新受影响 Bot 的逻辑激活配置。
- **下线**：先查询引用该 Skill 的 Bot；存在引用时阻止直接破坏运行环境，需先解除引用或完成受控变更。

Backend 负责引用血缘和变更编排，Engine 只负责最终的文件系统对账。

## 6. 关键架构原则

1. **Git 长期保留**：公共市场的稳定内容基线仍由 Git 提供，SC 不是 Git 的替代品。
2. **SC 是新增权威来源**：SC 应承载内容、版本和发布状态，不能仅以 `center://` 前缀代表接入完成。
3. **发布内容只读**：公开和 SC 内容均从只读分发目录进入容器，运行时不允许直接篡改发布包。
4. **来源与激活解耦**：来源决定逻辑 locator，SkillSet 决定是否激活，Engine 负责将其解析为自身的物理路径和软链。
5. **名称与稳定身份解耦**：路径使用 UUID，跨系统使用 Skill Code，运行时软链使用固定 Name。
6. **版本不可变**：每次发布生成新版本，主记录只保留当前版本指针。
7. **平台差异显式处理**：云端的只读挂载与桌面端的 ETag 拉取并存，SC 接入必须分别适配两种内容到达方式。

## 7. 关键接口与代码入口

| 层次 | 当前入口 | 在目标架构中的角色 |
| --- | --- | --- |
| Git 同步与制品发布 | `src/backend/src/agentclaw/community/api/skill_center/` | 保持 Git 公共 Skill 主链路。 |
| SkillSet 与映射生成 | `src/backend/src/agentclaw/community/api/skill_set_service_factory.py` | 当前激活编排入口；目标态演进为下发逻辑激活请求。 |
| HTTP 路由 | `src/backend/src/agentclaw/community/adapters/http/skill_center/` | Skill API 的入口。 |
| Engine 软链接口 | `src/engine/src/engine/community/api/skills/router.py` | 接收统一映射并完成文件系统对账。 |
| Engine 布局实现 | `src/engine/src/engine/community/plugins/skills_pool/` | Pool 目录解析、准备和激活对账的实现。 |
| SC Client 适配层 | `src/backend/src/agentclaw/community/plugin_api/skill_center_client.py` | 调用 SC 内容、版本、扫描和发布能力的边界。 |

## 8. 结论

当前 Skills 体系已经具备两条清晰的生产链路：Git 公共 Skill 的共享只读分发，以及 Local 自定义上传 Skill 的 Bot 私有可写存储。两者通过 SkillSet 和运行时软链在 Engine 侧汇合。

目标架构的关键不是“把 Git 和 Local 全量替换为 SC”，而是在保留 Git 公共来源的前提下，以 SC 建立私有 Skill 的内容、版本和发布权威，并将三类来源统一收敛到稳定的“版本目录 + SkillSet 映射 + Engine 软链对账”运行时模型。
