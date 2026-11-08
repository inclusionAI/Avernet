---
agent: tc-review
status: completed
created: 2026-10-19T22:00:00+08:00
iteration: 2
---

# 系分 Spec: 服务 Bot CLI 发布目录与运行权限

## 需求概述

服务 Bot 构建发布物时，将草稿 Home 下完整的 `.agentclaw` 目录纳入版本快照，包含 CLI 程序、依赖及内部相对软链。服务 Bot 在 verify（预发）和 online（生产）启动后，`/home/admin/.agentclaw` 与 `/home/admin/.local` 归 `root:root`；草稿态 `.local` 归 `admin:admin`，使 npm 用户目录可创建 `lib/node_modules`。最终交付 GitHub `inclusionAI/Avernet` 的 dev 目标 PR，先 rebase 最新远端 dev；PR 测试失败按实际证据修复。

## 已核验现状与边界

- Avernet 基线：GitHub dev `8ac9722d6b`，隔离分支 `feat/service-bot-runtime-ownership`。
- `BotBuildService._migrate_bot_instance` 在 NAS 构建路径调用 `sync_local_bin_artifact`，把 Home `.local/bin` 放在版本根目录旁，与引擎制品同级；草稿恢复调用 `restore_local_bin_artifact`。`.agentclaw` 尚未纳入。
- `local_bin_artifact.py` 故意跳过全部软链，仅覆盖 `.local/bin`。`.agentclaw/bin` 中 npm 工具可能通过相对链接指向 `../lib/node_modules/...`，不能直接复用跳过链接的语义。
- `ManagedDeployConfigComposer._get_set_read_only_rule` 下发现有 `--set_read_only` 参数，`is_editable_bot` 令 service 草稿可编辑，verify/online 不可编辑。不得把发布目录复制或目录权限遍历塞入 router、BaasService 或部署 composer。
- 当前托管镜像真源是 OCB：`engine/docker/arca-openclaw/Dockerfile` 将 `.staging/engine/bootloader/` 复制至 `/opt/agentclaw-daas-scripts/`。OCB `engine/bootloader/bootstrapping/service_bot_transition.sh` 已支持版本级 `.local/bin` 安装；独立旧 `agentclaw-daas-scripts` checkout 没有同版安装代码。
- OCB entrypoint 只给 `.local/bin`、`.local/share` 设 admin 属主，未覆盖父 `.local`。父目录 root:root 755 可导致 npm 用户前缀安装失败，即使 bin 可写。
- OCB `set_read_only.sh` 对目录执行 root ownership 和递归 755；其部分权限失败为 best effort。新增必达目录权限不能把失败伪报成功，也不能误删 CLI 执行位。
- **用户已明确迁移位置**：daas-script 已迁入 OCB `engine/bootloader`；运行态安装和草稿权限在 OCB bootloader / 镜像入口维护，旧独立仓非维护目标。配套改动按实际边界准备独立 OCB worktree/PR；Avernet 单仓变更不能报告为端到端已生效。当前 spec 仍待用户确认，不授权部署或合并。

## 编码 Spec

### 功能点

- [ ] 完整快照草稿 Home `.agentclaw` 至版本根同级 `.agentclaw`，不混入引擎工作目录。
- [ ] 保留目录内文件模式、可执行程序及内部相对软链，避免 `rsync -L` 跟随链接把外部数据纳入制品。
- [ ] 构建重试独立收敛 `.agentclaw` 快照；源缺失或目录被发布忽略规则排除时清理旧快照，避免旧版本残留。
- [ ] 草稿恢复将制品 `.agentclaw` 合并回 Home，归 admin；历史制品无该目录时兼容跳过。
- [ ] OCB 配套：安装 `.agentclaw` 到真实 Home；service 草稿 `.local` 为 admin，verify/online `.agentclaw` 和 `.local` 为 root。
- [ ] 所有新增路径遵守既有发布忽略规则及错误传播契约，保留 `.local/bin` 原有行为。

### 技术方案与边界日志
Avernet 在 service_bot/services 新增聚焦 `.agentclaw` 快照与恢复模块，通过既有 `_run_local_command` 执行 rsync，仅在现有 NAS Home 快照接点接线。制品与 `.local/bin` 独立收敛；验证固定源/目标父目录为真实目录，不解引用内部链接，不跟随入口软链。存在但复制失败须阻断构建，缺失/忽略源须清理旧快照；恢复合并、不删除无关 Home 内容，沿用 admin UID/GID 与既有总超时。
OCB 配套在原发布安装脚本落实复制，在启动前按 stage 落实 owner；不得向 composer 塞文件安装规则。原地重启不重复制，但仍处理权限。按本次要求仅落实 owner，不扩张全局 chmod 规则。
复用既有 logger/util，记录 request/start、success/skip、failure，以及 Bot/device/version、source、target、stage、目标 owner、缺失/忽略原因、退出码、异常类型和耗时。不记录文件内容、token、Cookie 或凭据；保留原失败语义。

### 关键方法抽象

| 抽象/方法 | 所在层或模块 | 职责与边界 | 输入与输出 | 协作对象与副作用 |
|---|---|---|---|---|
| `sync_agentclaw_artifact`（建议名） | service_bot/services 独立模块 | 快照固定 Home 子目录、清理本版本旧快照；不改源目录属主 | source_home、target_dir、device/version、ignore、run_command；成功无返回，失败抛错 | rsync 文件复制与日志 |
| `restore_agentclaw_artifact`（建议名） | 同上 | 合并恢复为 admin；旧制品缺目录跳过 | artifact_dir、target_home、bot/version、run_command、剩余超时 | rsync；不删除无关 Home 数据 |
| `_migrate_bot_instance` / 草稿恢复既有方法 | BotBuildService | 在既有快照/恢复顺序中调用新模块 | 现有契约不变 | 传播失败；不承担目录复制实现 |
| 发布安装/阶段权限 helper（配套命名沿用脚本风格） | OCB bootloader | 安装固定目录，按 service stage 设置属主 | source_dir、bot_type、stage；非零退出阻断启动 | 文件复制、chown、诊断日志 |

调用方选择版本并提供命令执行器；helper 负责文件规则、失败传播和日志，避免扩张大文件。脚本 helper 保留镜像安装和启动顺序职责。

### 关键领域模型设计

不适用：无需新增数据库实体、API 字段或持久化状态。现有版本根、EngineBuildPlan 与 PublishStage 已能表达该需求；`.agentclaw` 为版本级 Home 制品布局约定，不将目录误表述为 `extra_include_files` 单文件。布局增加为向后兼容扩展，旧制品无目录时跳过。

关系与不变量：制品始终属于指定 Bot/版本；`.agentclaw` 与引擎目录平级；复制不能逃逸指定 Home；草稿 `.local` 属 admin，verify/online 两个指定目录属 root；复制失败不能进入成功发布状态。
### 文件改动范围
| 文件路径 | 改动类型 | 改动说明 |
|---|---|---|
| `apps/backend/src/agentclaw/community/core/service_bot/services/agentclaw_artifact.py`（建议名） | 新增 | 完整目录快照/恢复 |
| `apps/backend/src/agentclaw/community/core/service_bot/services/bot_build_service.py` | 修改 | 两处最小接线 |
| `apps/backend/tests/community/core/service_bot/services/test_bot_build_service_agentclaw.py`（建议名） | 新增 | 真实 rsync 行为与失败/日志测试 |
| `apps/backend/tests/community/core/service_bot/services/test_bot_build_service_draft_restore.py` | 视需要修改 | 草稿恢复接线验证 |
| OCB `engine/bootloader/bootstrapping/service_bot_transition.sh` | OCB 配套 | 制品目录安装 |
| OCB `engine/bootloader/bootstrapping/start_service.sh` 和聚焦 helper | OCB 配套 | 草稿/verify/online 阶段权限 |
| OCB `engine/docker/arca-openclaw/opt/bin/entrypoint.sh` | 视初始化归属必要性 | 创建 `.local` 父目录默认属主 |
| OCB `engine/bootloader/tests/` 对应测试 | OCB 配套 | 真 shell 目录安装与阶段权限验证 |
### 验收标准
- [ ] 制品包含 `.agentclaw/bin`、`.agentclaw/lib/node_modules` 及任意其他普通子目录；内部 npm 相对链接恢复后可执行。
- [ ] 不带入链接指向的外部目录正文，固定根的链接或非目录异常不会越界写入。
- [ ] 重试删除源中已移除文件；源缺失/忽略时无旧快照残留；旧无目录制品兼容。
- [ ] 实际 shell 或隔离容器验证草稿 admin 能在 `.local` 创建目录；verify/online 两目录递归 owner 为 root，CLI 可执行。
- [ ] 失败日志和非零失败传播可验证，不记录文件内容或原始凭据。
- [ ] 修改范围单测通过；针对新增/改动文件用 `pytest --cov --cov-report=term-missing` 实测行覆盖率 >90%，并独立报告全文件与变更行覆盖率。
## Review Spec
关注完整目录与单 bin 制品的语义差异、npm 相对链接、重试清理、NAS 路径、草稿恢复与启动时序。关键方法职责、输入输出、错误/空结果与上述契约一致。
- [ ] 无目录数据塞入 extra_include_files；无新增跨层依赖或传输层业务规则。
- [ ] 不复用 `.local/bin` 全跳链接策略到 `.agentclaw`；不通过 `-L` 复制链接外部数据。
- [ ] 不全量递归 chown Home；权限失败不会被吞成成功。
- [ ] 无新增未使用 import/变量、孤儿代码或 Python 冒号/注释格式告警。
- [ ] Review 必须区分 Avernet PR 与 OCB 配套状态；缺配套时不判端到端完成。
- [ ] 实测覆盖率 >90%；未达标 REJECT，不通过降低门槛或无行为断言测试规避。
## QA Spec
| 编号 | 用例名称 | 操作步骤 | 预期结果 |
|---|---|---|---|
| TC-01 | 完整目录 | 草稿建 bin/lib/config/多级文件，执行构建 | 所有普通目录文件进入版本级 `.agentclaw` |
| TC-02 | npm 相对链接 | bin 链接 lib 内脚本，构建后安装执行 | 链接保留，可执行并返回预期输出 |
| TC-03 | 不解引用外部链接 | 在目录内建立指向外部目录的链接并构建 | 不将外部正文复制进制品，无越界写入 |
| TC-04 | 重试收敛 | 构建后删源文件，再构建相同版本；再移除源目录 | 已删文件和旧快照内容清理 |
| TC-05 | ignore | 配置 `.agentclaw` 根及子路径忽略 | 对应内容不进入制品，不破坏 engine/.local |
| TC-06 | 异常与日志 | 注入 rsync/权限失败、非目录或链接根 | 明确失败，关键路径与退出码可定位，无正文/凭据日志 |
| TC-07 | 草稿恢复 | 恢复新制品与旧无目录制品 | 新目录归 admin，旧制品跳过且既有数据保留 |
| TC-08 | 草稿安装 | 初始 `.local` 为 root，启动 service draft 后以 admin 创建 lib/npm 内容 | 父子目录均可按预期写入 |
| TC-09 | verify/online | 发布后检查两固定目录及 CLI 执行 | root ownership 正确，CLI 仍可执行 |
| TC-10 | 重启与兼容 | 原地重启、普通重启、personal 启动 | service 权限仍满足阶段规则，personal 无新增发布锁定 |
前置条件：有 rsync 的本地隔离目录；root/admin owner 实测使用具备对应 UID/GID 的隔离容器或现有 shell 测试工具。不得在开发者真实 Home 上测试权限。真实生产/预发验证另需明确目标和部署授权。
## Ship Spec
部署目标环境：本任务交付源码 PR；不执行预发/生产部署。用户已明确脚本迁入 OCB；spec 确认后按实际边界准备 OCB 隔离 worktree 和配套 PR。
分支策略：Avernet feature 基于 GitHub dev；最终重新 fetch 并将功能提交 rebase 至最新 dev，核对新增提交范围后以非强推发布新 PR 分支。源码远端为 `inclusionAI/Avernet`，不能推到内部镜像。
回滚：回退对应源码 PR 的业务提交；已构建的含 `.agentclaw` 制品不需要数据库迁移。若 OCB 配套已上线，应同步回退其安装/权限行为，说明已有文件属主不会因源码回退自动恢复。源码 PR 创建不等于镜像已更新或容器已验收。
