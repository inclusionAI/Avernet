# Singlebox Coverage PR Train

日期：2026-07-11

## 1. 目标

把当前 singlebox 架构和覆盖率工作拆成一条可独立评审、可持续恢复的 PR 链：

```text
dev
└── PR-A Profile / Env 分离
    └── PR-B Device + Coverage 基础框架（更新现有 PR #62）
        ├── PR-C1 Auth
        ├── PR-C2 Bot Chat
        ├── PR-C3 Bot Dormant
        ├── PR-C4 Cron
        ├── PR-C5 Files
        └── PR-C6 Resources
```

所有 PR 的最终目标分支都是 GitHub `inclusionAI/Avernet:dev`。依赖尚未合并时，
允许使用 stacked base 保持 diff 干净；上游合并后必须把 base 切回 `dev` 并重新验证。

## 2. 不可违背的架构原则

1. `DeployProfile` 决定安装哪些实现，Env 只表示字段和数据分区。
2. singlebox Backend 使用：
   - `DEPLOY_PROFILE=singlebox`
   - `SERVER_ENV=dev`
   - `WORKSPACE_ENV_FOLDER=aidesktop_singlebox`
3. 覆盖率测试代码不能侵入 Router、Core 或业务 Service。
4. 通用 coverage reporter、manifest 和 dashboard 脚手架只在 Device PR 中落一次。
5. 每个业务模块 PR 只包含该模块的 manifest、真实 user story、acceptance/E2E，以及
   为真实链路所必需的最小通用修复。
6. Source of truth 是 GitHub `inclusionAI/Avernet`；内源镜像不是 PR 目标。

## 3. PR-A：Profile / Env 分离

### 范围

独立实现并评审以下文档：

- Spec：`docs/superpowers/specs/2026-07-11-profile-env-separation-design.md`
- Plan：`docs/superpowers/plans/2026-07-11-profile-env-separation.md`

PR-A 只解决：

- Profile 驱动 YAML overlay。
- Test 与 Singlebox 的 Access / HTTP Client DI 绑定拆分。
- Env 移除 `singlebox` 第四档语义。
- Device Env 严格限制为 `dev/pre/prod`。
- singlebox 启动参数与 Backend/BAAS 物理路径对齐。
- legacy `SERVER_ENV=singlebox` fail-fast。
- 静态架构守卫、Backend 全量测试和真实 singlebox 回归。

### PR 组织

- 标题：`refactor(runtime): separate deploy profile from data env`
- Reviewer：`totalfrank`
- PR body 中明确要求先读 Spec，再看 Config、DI、Env/启动代码。
- Avernet 开源 PR 只运行 community/test/singlebox 可执行验证，不伪造
  `agentclaw.corp` 或 CORP_TEST composition root。
- 每个实现 commit 的正文引用：

```text
Spec: docs/superpowers/specs/2026-07-11-profile-env-separation-design.md
```

### 跨仓集成门

Avernet PR 合并并完成内源镜像同步后，在外层 OCB 的 gitlink 更新 PR 中：

1. 更新 `ocb-public` gitlink 到已同步的 Avernet merge commit。
2. 同步更新 `src/backend/tests/corp/di/test_profile_and_modules_for.py`。
3. 使用 corp-present composition root 验证 `CORP_TEST` 最终解析为真实
   `PolicyService`，且四个 qualified HTTP key 全部解析为 `LocalHttpClient`。
4. 在 OCB `src/backend` 下运行：

```bash
DEPLOY_PROFILE=corp_test uv run pytest \
  tests/corp/di/test_profile_and_modules_for.py -q
```

这是 Avernet 合并后的跨仓集成门，不属于 Avernet 开源 PR 内可执行的测试；Avernet
仓库不得通过 fake `sys.modules`、伪造 `agentclaw.corp` 或裁剪版 corp module column
来替代它。

### 完成条件

- Profile/Env Plan 的四个 Task 全部完成并经过 task review。
- 最终 whole-branch review 无 Critical / Important finding。
- Backend community tests、shell guards、真实 singlebox 和 coverage 入口通过。
- 创建到 `dev` 的 ready PR，并请求 totalfrank review。
- Avernet 合并后的 OCB gitlink 更新 PR 已完成上述 corp-present `CORP_TEST`
  解析验证；在该门通过前，PR-A 的跨仓集成状态不得标记完成。

## 4. PR-B：修复 Device Coverage PR #62

现有 PR：<https://github.com/inclusionAI/Avernet/pull/62>

### 当前已确认状态

- 远端 head：`7be5322`。
- PR 状态：`CHANGES_REQUESTED`。
- totalfrank 有两个 unresolved thread：
  - `devices/schemas.py` 不应在 Schema 层把 singlebox 映射成 dev，应使用 Profile。
  - `singlebox_coverage.py` 的当前手工 instrumentation 后续应改成 AOP；本次允许留 TODO。
- 本地 Device 分支有三个未推送的临时 Profile/Env commit；不得直接推入 PR #62。
- `docs/arch/generated/` 是未跟踪产物，不得进入任何 PR。

### 重建策略

1. 保留现有 PR #62，避免丢失 review thread 和历史讨论。
2. 以 PR-A 的稳定 head 为新基线，重放 Device/coverage 有效提交。
3. 删除 `schemas.py` 中 `singlebox -> dev` alias，由 PR-A 的正式设计解决。
4. 保留 coverage reporter、manifest、runtime hit 记录和 Device acceptance 用例。
5. 在 `singlebox_coverage.py` instrumentation 边界增加：

```python
# TODO(totalfrank): replace this explicit coverage instrumentation with an AOP boundary.
```

6. 不在 PR-B 中实现 AOP 重构。
7. 运行 reporter 单测、Device E2E、真实 singlebox 和三项指标回归。
8. force-with-lease 更新 PR #62，并回复两个 totalfrank thread。

### Review 回复口径

Schema thread：

```text
Agreed. The distinction now lives in DeployProfile via the Profile/Env foundation PR.
This PR no longer maps singlebox at the API schema boundary; singlebox persists data
with env=dev.
```

AOP thread：

```text
Added the requested TODO at the instrumentation boundary. This PR keeps the current
behavior to establish the coverage baseline; the AOP refactor remains a follow-up.
```

### 完成条件

- PR-A 已合并，或 PR #62 暂时以 PR-A branch 为 stacked base。
- 两个 unresolved thread 已用新代码和 PR 链接回复。
- Core / Router API / Plugin API 指标重新生成并写入 PR body。
- CI、真实 singlebox、Device acceptance 全部通过。

## 5. PR-C：六个模块独立扩展

六个模块必须从同一个 PR-B 稳定 head 创建兄弟分支，不能互相依赖。

| 模块 | 可复用旧 commit | 主要风险 |
| --- | --- | --- |
| Auth | `fa94e7c` | 身份实现必须由 Profile DI 选择，不能重新读取 Env |
| Bot Chat | `128c33d` | 必须验证真实日志/消息 user story，避免只制造覆盖率 hit |
| Bot Dormant | `91c90f8` | 旧提交修改生产 DI Module，需证明是通用装配而非测试侵入 |
| Cron | `69cadfc` | 旧提交修改共享 TestDevicesModule，需避免跨模块 fake 污染 |
| Files | `4080afa` | 验证 Backend -> BaaS -> Engine 文件链和真实磁盘产物 |
| Resources | `dd021c8` | 旧提交修改生产 ResourcesModule，需重新判断边界是否合理 |

### 每个模块统一流程

1. 从 PR-B head 创建独立 worktree/branch。
2. 只 cherry-pick 表格中的模块 commit，不 cherry-pick 整条旧分支。
3. 对照最新 Profile/Env 和 coverage 架构重新 code review。
4. 删除 coverage-only 的生产代码、Router 手工 hit、Core recorder 调用。
5. acceptance 用例必须表达真实 user story，并检查最终状态或物理产物。
6. 运行模块 focused tests、真实 singlebox acceptance 和 coverage 报告。
7. PR body 记录：
   - 用户故事。
   - Core / Router API / Plugin API 分母。
   - 改造前后指标。
   - 测试命令和结果。
   - 是否包含生产代码变化及其业务理由。
8. 创建独立 Draft PR；PR-B 合并后把 base 切回 `dev`，rebase 并转 Ready。

### 推荐 review 顺序

1. Auth：直接受 Profile/Env DI 影响。
2. Bot Dormant、Resources：含生产 DI 代码变化，风险较高。
3. Cron、Files：都触及 TestDevicesModule，重点消除共享 fake 耦合。
4. Bot Chat：主要为 acceptance，用来校验模板的最小形态。

## 6. 今晚执行顺序

```text
Phase 1  实现 PR-A + task review + whole-branch review + 创建 PR
Phase 2  在 PR-A head 上重建 PR #62 + 回复 totalfrank comments
Phase 3  在稳定 Device head 上创建六个独立模块分支
Phase 4  逐模块 review / 修复 / 测试 / 创建 Draft PR
Phase 5  根据上游合并状态 retarget dev，并补最终 rebase/CI
```

Profile/Env 和 Device 是串行依赖。六个业务模块在 Device head 稳定后可以并行分析，
但实现、提交和 review 仍按独立 worktree 隔离。

## 7. 恢复与防遗忘机制

- 本文档是整个 PR train 的长期 source of truth。
- PR-A 的细节进度记录在 `.superpowers/sdd/progress.md`，该文件是本地 ledger，
  不进入 Git。
- 每完成一个 SDD Task，ledger 记录 commit range 和 review 结论。
- compaction 或会话恢复后，先读本文档，再读 ledger，再读 `git log`；不得凭记忆
  重复执行已完成 Task。
- 每个后续 PR 建立自己的 handoff/progress 文件，并引用本文档。

## 8. 禁止事项

- 不把 PR-A、PR-B 和六个模块合成一个大 PR。
- 不直接推送 Device 分支上三个临时 Profile/Env commit。
- 不把整个旧 Device 分支 cherry-pick 到业务模块分支。
- 不让六个业务模块 PR 互相成为代码依赖。
- 不为了提高指标向业务代码写 recorder、hit 或 singlebox-only 条件分支。
- 不在 Device PR 内顺带实现 AOP 重构。
- 不在 coverage 结果未重新生成时复用旧指标。
