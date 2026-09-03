# Skill Center — 链路速查

本文件记录 skill_center 模块**必须知道的判定规则、链路与陷阱**，供快速定向。不求覆盖全部细节，只锁定"改这块代码前不知道就会踩坑"的部分。

阅读顺序：先看 [一张图看懂全局](#一张图看懂全局)，再按需跳到对应小节。

---

## 一张图看懂全局

skill_center 有两条正交的维度，**别混为一谈**：

- **数据来源**（skill 的"料"从哪来）：git / local / center 三种前缀。
- **部署形态**（engine 的"料"和"链"怎么送达）：arca（云端 sandbox）/ baas（桌面 agentbox VM）/ local（本地开发）。

```
        ┌──────────────────────── 数据来源（料） ────────────────────────┐
        │  git://    公共市场，GitSyncService 从 git 同步 → OSS            │
        │  local://  用户自定义上传，写进 bot 的 skills-local              │
        │  center:// SkillCenter（SC），按 skill_uuid 同步（当前多为 no-op）│
        └────────────────────────────────────────────────────────────────┘
                                    │
                  DB(ac_skill / skill_set) 记录每个 skill 的来源前缀
                                    │
        ┌──────────────── 送达 engine（链 + 料的落地） ──────────────────┐
        │                                                                  │
        │  软链下发（链）        device_sync.sync_symlinks                 │
        │  文件读写（料）        device_filesystem.read/write/list/...     │
        │       │                                                          │
        │  按 device_provider 分流（唯一分流点 = Dispatcher）              │
        │       ├── arca  → proxypass 直连 sandbox                         │
        │       ├── baas  → BaaS invoke-http → tc-tauri → 桌面 VM          │
        │       └── local → 直接写本地文件系统（开发模式）                 │
        └──────────────────────────────────────────────────────────────┘
```

> **权威背景文档**：device_provider 三态分流、agentbox/arca 双链路拆分的完整设计见
> `docs/superpowers/backend-agentbox-arca-split-guide.md`。该文档是**目标态规划**；
> 本文件描述**当前已落地代码**，两者有差异时**以本文件 + 实际代码为准**（见 [迁移现状](#迁移现状与命名)）。

---

## 一、数据来源：git / local / center 三种前缀

每个 skill 在 DB（`ac_skill.git_path`）里带一个前缀，决定它的"料"在容器里的位置：

| 前缀 | 来源 | 容器内 source 路径 | 内容供给方 |
|------|------|-------------------|-----------|
| `git://<rel_path>` | 公共市场（GIT） | `skills-repo/<rel_path>` | `GitSyncService`（见[第三节](#三skills-repogit-来源的内容供给)） |
| `local://<name>` | 用户自定义上传 | `skills-local/<name>` | `upload_skill()` 直接写设备 |
| `center://<skill_uuid>` | SkillCenter（SC） | `skills-center/<uuid>/current/<name>` | `SkillCenterSyncService`（当前 `get_symlink_mappings` 不产 center 映射，多为 no-op） |

```
容器内 /home/admin/.openclaw/workspace/skills/
  ├── skills-repo/      ← git:// 来源（GitSyncService 同步）
  ├── skills-local/     ← local:// 来源（用户上传）
  ├── skills-center/    ← center:// 来源（SC 同步，当前基本未启用）
  ├── foo        →  skills-repo/business/foo     ← 软链（sync_symlinks 创建）
  └── my-skill   →  skills-local/my-skill        ← 软链（sync_symlinks 创建）
```

**`get_symlink_mappings()`（`skill_set_service.py`）只产 `git://` 和 `local://` 两种软链映射**，不处理 `center://`（center 链路曾被回滚，详见 `device_sync.py` 内注释）。

**"链"与"料"分离**：`sync_symlinks` 只下发软链（链），不上传文件、不 clone git。skills-repo / skills-local / skills-center 里的真实文件（料）由各自的供给链路保证存在。

---

## 二、部署形态：device IO 的三态分流

Service / API 层统一通过 `device_filesystem`（文件读写）和 `device_sync`（软链下发）两个 Plugin 操作 engine，**完全不感知部署形态**。

### 分流唯一入口

```
Service 层    device_fs / device_sync
                  │
Dispatcher    DeviceFilesystemDispatcher.for_bot() / DeviceSyncDispatcher.for_bot()
(唯一分流点)   （di/modules/skill_center_module.py）
                  │   按 device_provider 分流（不是 bot_type！）
       ┌──────────┼──────────────┐
   arca           baas           local
       │            │              │
 ArcaDeviceFileSystem/  BaasDeviceFileSystem/  LocalDeviceSyncPlugin
 ArcaDeviceSyncPlugin   BaasDeviceSyncPlugin   （开发模式直接写 FS）
       │            │
  agentclawproxy   BaaS invoke-http
   proxypass         │
       │            │
 {proxy_base}/    {baas_base}/api/v1/bots/
 proxypass/       {tenant}/{bot_uuid}/
 {target}{path}   invoke-http/{port}{path}
       └────────────┴──────────────┐
                              Engine 侧（不感知部署形态）
              两个 router，两种部署下行为/payload 完全一致：
                src/engine/api/file/router.py    (/api/file)
                  upload / read / remove / rmtree / list
                src/engine/api/skills/router.py  (/api/skills)
                  symlink / symlink/bindpath / symlink/clean / center/ensure
```

### bot_type × device_provider 合法性校验

分流**只看 `device_provider`**；`bot_type` 仅用于路由前的合法性校验（`_validate_bot_device_combination()`，`skill_center_module.py`）：

| bot_type | device_provider | 合法性 |
|----------|----------------|--------|
| `personal` | `arca` | ✅ |
| `personal` | `baas` | ❌ raise DeviceServiceError |
| `service` | `arca` | ✅ |
| `service` | `baas` | ❌ raise（待隐舟重构后开放） |
| `desktop` | `baas` | ✅（桌面 agentbox，目前唯一走 baas invoke-http 的链路） |
| `desktop` | `arca` | ❌ raise DeviceServiceError |
| `""` / 未知 | 任意 | ⚠️ 跳过校验（兼容旧数据） |

`device_provider="local"` 的第三态语义是 **"ARCA binding 存在但 sandbox 未就绪"**（被回收/未分配/用户没启动），不是本地开发模式——命名是历史遗留。此时 dispatcher 应让业务层感知"设备未就绪"。

---

## 三、skills-repo（git 来源）的内容供给

公共市场 skill 由 `GitSyncService`（`core/skill_center/services/git_sync.py`）从远程 `aiworkbench.git` 同步到本地磁盘、DB、OSS。**backend 这一侧不区分 arca/agentbox，统一产出**；区别全在消费端（见[第四节](#四arca-vs-agentbox消费-skills-repo-的两种方式)）。

### 关键路径

```
本地 bare repo:  ~/aiworkbench/aiworkbench.git          ← git clone --bare 的源
本地工作目录:    ~/aiworkbench/skills-repo|agents-repo  ← 从 bare repo 抽出的 subtree
OSS 散目录点位:  {bolt_shared}/skills-repo|agents-repo  ← rsync 上去（arca 容器 fuse 挂载读取）
OSS 整包:        aidesktop/aidesktop_{env}/bolt_shared/aiworkbench.tar.gz  ← agentbox VM 下载
OSS meta:        agentclaw-sys/skills-repo-meta-{env}.json  ← 含 url+etag，agentbox 判增量用
```

### 启动链路（Lifecycle）

`GitSyncService.startup()` → `sync_bootstrap()` → `start_periodic_sync()`。

`sync_bootstrap()`（⚠️ **方法名是 `sync_bootstrap`，不是 `bootstrap`**）：
1. 本地 bare repo 已存在 → 直接 `success=existing` 返回。
2. 不存在 → 抢分布式锁 `git_sync_bootstrap:{env}`（ttl 600s）：
   - **抢到锁**：clone 主路径（clone → fetch → 抽 skills subtree → 上传 OSS tar.gz → 刷 meta JSON），任一步异常则走 **OSS tar fallback**（下载整包解压，校验 HEAD + git fsck）。
   - **抢不到锁**：**轮询等待** `local_bare_repo.exists()` 直到就绪（每 2s 一次，超时 `bootstrap_wait_timeout`，默认 60s，env `BOOTSTRAP_WAIT_TIMEOUT`）；就绪返回 success，超时返回 `method=wait_timeout`（**不再乐观假成功**）。
3. 锁始终在 `finally` 释放；clone/fallback 失败记 ERROR。

### 定时同步链路（每 `SYNC_INTERVAL_MINUTES` 默认 30min）

`_sync_loop()`（带 0~`SYNC_JITTER_SECONDS` 抖动）→ 每轮调 `sync()`：
1. 抢进程内 `GlobalSyncLock` + 分布式锁 `skill_repo_sync:{env}`（抢不到则 skip）。
2. `_git_fetch()` —— **内联自愈**：bare repo 不在则当场调 `sync_bootstrap()` 重建后重试；重建失败返回 `self-heal bootstrap failed`（下一轮再试，不会永久卡死）。
3. `_sync_subtree(skills/agents)` 并发 rsync 到本地工作目录。
4. 有更新才 `_update_database()` + `_refresh_market_cache()` + 上传 OSS tar.gz；meta JSON 每轮都刷（presigned URL 会过期）。
5. **后台** OSS 散目录同步：仅当 `enable_oss_sync=true`（prod/dev/base 的 `application.yaml` 已配 true）时 `asyncio.create_task(_sync_to_oss_async)` → rsync 本地 → `{bolt_shared}/skills-repo`。

---

## 四、arca vs agentbox：消费 skills-repo 的两种方式

backend 统一产出（OSS 散目录 + tar.gz + meta），但 **engine 怎么拿到 skills-repo，两种部署完全不同**：

| 维度 | **arca（云端 sandbox）** | **agentbox（桌面 VM）** |
|------|--------------------------|-------------------------|
| skills-repo 获取 | sandbox 容器**直接 OSS 挂载** `bolt_shared/skills-repo`（散目录），文件天然可见 | **VM 内 engine 主动拉 OSS tar.gz 整包**，解压替换本地目录 |
| 消费代码 | 无（挂载，被动） | `src/engine/core/skills/skills_repo_download.py`（主动） |
| 增量机制 | 无需（挂载即最新） | **etag 比对**：本地 `.skills-repo-etag` vs meta 的 etag + 对 presigned URL 发 `HEAD If-None-Match`，304 跳过 |
| meta 依赖 | 不需要 | 读固定公开 URL `skills-repo-meta-{env}.json`，URL 改写成**办公网 endpoint**（VM 免 VPN） |
| 触发 | 容器随挂载即最新 | 启动 `bootstrap_on_startup()` + 后台每 **300s** `start_background_sync()` |
| 环境开关 | — | `MAC_CONTAINER=true` 才执行；ARCA 容器 no-op（但残留临时目录清理无差别执行） |
| skills-repo 路径 | `/aidesktop/.../skills-repo`（OSS-view，engine `_convert_path` 转写到 `~/.openclaw/...`） | `~/.openclaw/workspace/skills/skills-repo`（engine-view，直接） |
| 替换方式 | — | 临时目录解压 → 备份现有（留 1 个，7 天过期）→ `shutil.move` 原子 rename |

**关键认知**：差异全在消费端，backend 的 `GitSyncService` 对两种部署一视同仁。排查"agentbox 上 skill 没更新"应查 engine 侧 `skills_repo_download.py`（meta 拉取 / etag / 下载）；排查"arca 上没更新"查 OSS 散目录 rsync（[第三节](#三skills-repogit-来源的内容供给)第 5 步）。

---

## 五、运行时启动与重启后的软链自动恢复

**文件**：`core/skill_center/services/skill_symlink_listener.py`

运行时首次启动或重建后，软链可能尚未建立或已随上层文件系统重置。首次 `PENDING -> ACTIVE` 由 `DeviceActivatedEvent` 触发；BaaS 重启发布成功由 `RuntimeProjectionRequestedEvent` 触发。两者进入同一个监听器，按 Installation 等控制面期望态执行完整 Runtime Projection。

### 触发时机

| 场景 | 触发来源 | 行为 |
|------|----------|------|
| Bot 首次创建 | `desktop_bot_service` 启动流程 | VM 进入 active 状态时发布 `DeviceActivatedEvent` |
| VM 重启 | `desktop_bot_service` 健康检查回调 | 同上 |
| VM 重建（`upper.img` 删除） | `desktop_bot_service` 重建后激活 | 同上 |
| BaaS Bot 重启发布成功 | `BaasRestartPublishPollHandler` | 发布 `RuntimeProjectionRequestedEvent`，不重放其他激活副作用 |

### 实现路径

1. `SkillSymlinkListener.startup()` 将 `self.handle` 幂等订阅到 `DeviceActivatedEvent` 和 `RuntimeProjectionRequestedEvent`。
2. `SkillSymlinkListener.handle(event)` 接收到事件后：
   - 通过 `bot_repo.get_by_binding_id(event.binding_id)` 找到对应 Bot。
   - 调用 `BotRuntimeProjector.project(scope=ProjectionScope.everything())`，从 Installation 等 SSOT 重新解析 Skill、MCP、CLI、Passport 的完整期望态。
   - Projector 根据 Engine 与当前 Layout 选择实际交付路径；监听器本身不按 Legacy/Pool 分支。
3. `PENDING` / `DEGRADED` 与异常都维持 best-effort 语义，不回滚已成功的设备启动或重启。仅当 DI 未提供 Projector 时，才回退到历史 `get_symlink_mappings() → sync_symlinks()` 兼容路径。

> **关键结论**：运行时首次激活或 BaaS 重启发布成功后，系统都会按当前控制面期望态尝试一次完整投影；该动作是 best-effort，不改变同步 SkillSet 接口或任务终态语义。

---

## 六、迁移现状与命名

device 层正处于 agentbox/arca 双链路拆分迁移中（背景见 `docs/superpowers/backend-agentbox-arca-split-guide.md`）。**当前代码（本文件以此为准）与该规划文档的差异**：

| 项 | 规划文档（目标态） | 当前代码（实际） |
|----|-------------------|------------------|
| 分流入口 | `get_device_filesystem`（dependencies/skills.py） | `DeviceFilesystemDispatcher.for_bot()`（di/modules/skill_center_module.py） |
| baas 类名 | `AgentboxDeviceFileSystem` | `BaasDeviceFileSystem`（文件 `baas_device_filesystem.py`，尚未改名 agentbox_*） |
| baas URL | `/api/v1/paas/devices/{paas_device_id}/...` | `/api/v1/bots/{tenant}/{bot_uuid}/invoke-http/{port}{path}`（2026-05 已更新） |

改 device 层代码时，看实际类名/文件名，别照搬规划文档的 `Agentbox*` 命名。

---

## 七、关键文件索引

| 文件 | 职责 |
|---|---|
| `adapters/http/skill_center/skills.py` | 遗留 `/api/skills` 路由（读走 `SkillQueryService`，激活/去激活走 `DirectActivationService`） |
| `adapters/http/skill_center/skillsets.py` | 遗留 `/api/skillsets` 路由（写走 `SkillSetManagementService` 控制面） |
| `core/skill_center/services/skill_set_management_service.py` | `SkillSetManagementService` — Set 范围的期望态命令服务（Default-Set 编辑落为 per-Bot 排除行） |
| `core/skill_center/services/direct_activation_service.py` | `DirectActivationService` — 单能力（skill/MCP）直接激活命令服务；Platform Default MCP 拒绝 Direct，只能走 Default exclusion |
| `core/skill_center/services/skill_query_service.py` | `SkillQueryService` — Bot skill 的唯一查询缝（列表/详情/内容/参数，读前先 flush） |
| `core/skill_center/services/bot_capability_state_reader.py` | `BotCapabilityStateReader` — flush-then-read 的唯一激活态读模型 |
| `core/skill_center/policies/capability_ownership.py` | `CapabilityOwnershipPolicy` — R1/R2/R3 所有权规则的唯一裁决点 |
| `core/repository/implementations/skill_center/capability_desired_state.py` | `CapabilityDesiredStateRepository` — 期望态 UoW；`tables/` 子包是 Installation/排除表 SQL 的唯一属地 |
| `core/skill_center/services/skill_set_service.py` | `get_symlink_mappings()`（激活写路径已收敛到 desired-state 控制面） |
| `core/skill_center/services/skill_service.py` | `upload_skill()`、`activate_skill()` 单个软链操作 |
| `core/skill_center/services/skill_symlink_listener.py` | `SkillSymlinkListener` — 订阅激活/重投影事件，运行时就绪后按当前期望态执行全量投影 |
| `core/skill_center/services/git_sync.py` | `GitSyncService` — git 来源同步（startup / sync_bootstrap / periodic / OSS） |
| `core/skill_center/services/skill_center_sync_service.py` | `SkillCenterSyncService` — center:// 来源同步 |
| `core/skill_center/factories.py` | `SkillSetServiceFactory` / `SkillServiceFactory`，Factory 层解析路径 |
| `core/workspace/path_factory.py` | `get_bot_skills_local_dir()` / `get_bot_skills_repo_dir()`（按 `is_desktop` 分 engine-view / OSS-view） |
| `di/modules/skill_center_module.py` | `DeviceFilesystemDispatcher` / `DeviceSyncDispatcher`（分流唯一入口）；`_validate_bot_device_combination()` |
| `plugins/prod/device_sync.py` | `ArcaDeviceSyncPlugin` — arca proxypass 软链同步 |
| `plugins/prod/baas_device_sync.py` | `BaasDeviceSyncPlugin` — BaaS invoke-http 软链同步 |
| `plugins/prod/device_filesystem.py` | `ArcaDeviceFileSystem` — arca proxypass 文件读写 |
| `plugins/prod/baas_device_filesystem.py` | `BaasDeviceFileSystem` — BaaS invoke-http 文件读写 |
| `plugins/prod/baas_conn_info.py` | `build_baas_conn_info()` — BaaS ws_info → conn_info 映射 |
| `plugins/prod/baas_device.py` / `device.py` | baas / arca binding 的 `get_connection_info` |
| `plugins/local/device_sync.py` | `LocalDeviceSyncPlugin` — 本地开发直接写文件系统 |
| `src/engine/core/skills/skills_repo_download.py` | **（engine 侧）** agentbox VM 内主动拉 OSS skills-repo，etag 增量替换 |

---

## 八、必须知道的陷阱

**分流看 `(device_provider, bot_type)` 二元组**（2026-06-17 PR 起）：

- transport / plugin 选择由 Dispatcher 内部 `(ctx.provider, ctx.bot_type)` 决定
- arca / teclaw / local provider：bot_type 不参与 plugin 选择，仅 `_validate_bot_device_combination` 用
- **baas 内部按 bot_type 二次分**：
  - `(baas, desktop)` → `DesktopBaasDeviceSyncPlugin` / `DesktopBaasDeviceFileSystem`（走 `DesktopBaasInvokeTransport` 自拼 secbaas wrapper URL）
  - `(baas, personal)` / `(baas, service)`（未来灰度上线后启用）→ `BaasDeviceSyncPlugin` / `BaasDeviceFileSystem`（走 `BaasInvokeTransport` → `BaasService.invoke_http`）

  原因：desktop bot 的 agentbox VM 在用户机器，BaaS `get_http_info` 返 `http_url=http://localhost:20003/...` 裸 url backend 拨不通；只能走 secbaas transparent proxy router。云上 baas 则需要 `get_http_info` 给的动态 token / 负载 / device_affinity。

- Service / API 层出现 `if device_provider == "baas"` 或 `if bot_type == "desktop"` 的 transport 分支仍属架构违规 — 分流唯一发生在 Dispatcher。设计：`docs/superpowers/specs/2026-06-17-baas-transport-bot-type-strategy-design.md`。

**path 选择却看 bot_type（`is_desktop`）**：与分流相反，`path_factory.get_bot_skills_repo_dir(is_desktop=...)` **按 `bot_type == "desktop"` 选 engine-view / OSS-view 路径**——因为 service bot 的 device_provider 也是 baas，但走云端 OSS-view。这是**有意为之的双维度**：transport 用 device_provider，path 用 bot_type，别统一。

**自愈方法名是 `sync_bootstrap`**：`_git_fetch` 的内联自愈、lifecycle startup 都调 `sync_bootstrap()`。历史上叫 `bootstrap`，已改名——别再写 `self.bootstrap()`。

**bare repo 丢了能自愈**：bare repo 在 pod 本地磁盘，pod 重建即丢。periodic sync 的 `_git_fetch` 会自愈重建；若持续报 `Bare repo not found` 超过一个 sync 周期，才说明 bootstrap 本身失败（查 clone/OSS fallback 的 ERROR 日志）。

**OSS 散目录 rsync 容忍 fuse 噪音**：`_sync_to_oss_sync` 对 fuse 挂载点 rsync 时，`--exclude=.fuse_hidden*` 跳过占用文件，且**退出码 24（"some files vanished"）视为可接受**，其余非 0 才记 `OSS sync failed`。

**OSS 散目录与整包是两种形态**：散目录（`{bolt_shared}/skills-repo/<skill>/`）供 arca 容器 fuse 直接读；整包 `aiworkbench.tar.gz` + meta JSON 供 agentbox VM 下载。查"arca 某 skill 是否同步"看散目录；查"agentbox 能否拉到"看整包 + meta。两者由不同代码路径维护，可能不同步。

**enable_oss_sync 门控散目录同步**：散目录 rsync 受 `enable_oss_sync` 控制（代码默认 false，prod/dev/base 的 `application.yaml` 配了 true）。整包上传不受此 flag 控制，有更新就传。

**公开 bot 的 owner_id**：前端访问公开 bot 时同时传 `entity_id`（owner）和 `ctx.user_id`（访客）。`bot_type` 属于 owner，查 bot 信息必须用 `entity_id`，不能用 `ctx.user_id`。

**软链路径是 engine 视角的绝对路径**：`get_symlink_mappings` 产出 `/home/admin/.openclaw/...` 开头的路径（engine 容器内）。`LocalDeviceSyncPlugin` 会把它转为 `~/.openclaw/...`（本地开发模式）。

---

## 九、目录语义与路径所有权

### 内容库与激活入口必须分离

`skills-repo` 与 `skills-local` 保存完整内容集合；active Skills 目录是 Agent
发现当前 Bot 已激活 Skill 的入口。这两类目录不能混为一谈。

新版引擎可能递归扫描受信任目录和软链目标。若 active 目录能够进入完整内容
集合，未激活的公共或本地 Skill 仍可能被发现；取消激活只移除逐 Skill 入口，并
不会删除用户上传的内容。因此 Skills Pool 的目标约束是：完整内容放在
`skills-pool/`，active 目录只保留逐 Skill 的直接入口，不保留
`active/skills-repo` 或 `active/skills-local` 这类内容库桥接。

### Engine 是物理目录的唯一所有者

Backend 当前仍会生成 `source -> target` 的绝对路径映射，这属于兼容契约；修改
既有链路时必须保持它，但不得继续在 Backend 新增引擎目录硬编码。

目标态由 Engine Runtime 提供版本化的布局描述与唯一的目录解析器：

```text
Backend
  -> 发送 engine_type、layout_state、layout_contract_version
     以及逻辑 Skill 激活意图（scheme / locator / version / link_name）
  -> Engine Runtime

Engine Runtime
  -> 解析 active、legacy、pool、mount 与兼容桥的物理路径
  -> 准备并校验运行时布局
  -> 对账逐 Skill 激活入口
```

Backend、镜像启动脚本和部署工具都应消费同一份 Engine 布局解析结果。未知引擎或
未知布局契约必须失败关闭，不能回退到某个默认引擎路径。
