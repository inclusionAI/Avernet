---
agent: tc-code-reviewer
status: completed
created: 2026-08-08T18:25:09+0800
iteration: 2
---

# 代码评审报告

## 评审范围

- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/mixed-openclaw-claude-bots-dev`
- 分支: `feat/mixed-openclaw-claude-bots-dev`
- Base: `71f7c14188be19631edda103fa57864f8600a732`（当前 worktree 的未提交实现）
- 审查文件: 12 个本轮修复文件：Provider 生命周期/bridge/relay/frontend 及对应 shell、Node 契约测试。
- 输入说明: `002-code-report.md` 已补齐并与本报告确认的修复范围一致。仓库没有名为 `main` 的本地 ref，故 `git diff main...HEAD` 不可用；已改用 `git status --short` 与未提交 diff 评审实际变更。

## 逐条评审意见

### 固定检查维度

| 维度 | 结论 | 说明 |
|------|------|------|
| 正确性 | PASS | Provider 停止顺序先于 BCS；清理严格以保存的 provider ID 和三个 provider ref 为界。bridge 同时覆盖 h2c 流和 HTTP/1 回调；本地 `chat.abort` 在 Provider 协议没有安全目标 run ID 时显式拒绝，避免误取消活动流。前端将人类身份作为发送者元数据传到 Workbench `bot_id`，正常用户发送不复用 `bot_uuid`。 |
| 安全性 | PASS | Runtime token 文件以 `umask 077`/`0600` 创建；bridge 每次请求校验 Provider 级 bearer token 和允许的 provider ref；bridge 与前端发送诊断均只输出 method/run ID/ref/status 或长度/布尔值/计数，契约测试断言不会出现请求正文。CLI preflight 仅执行 `--version`，显式路径不可用时 fail-closed。 |
| 性能 | PASS | Provider ref 是常数规模集合；流响应通过背压逐块转发；CLI 探测有 5 秒同步超时，未新增轮询或非必要全量操作。 |
| 代码风格 | PASS | 新增 Shell/Node/TS 与既有项目格式一致；相关 Shell 通过 `bash -n`，Node 通过 `node --check`，四个前端文件 ESLint 零告警。 |
| 测试覆盖 | PASS | Shell 测试断言严格三角色、owned cleanup、生命周期回滚、CLI fallback 静态契约和 frontend 进程/发送字段；Node 合约测试实际拉起临时 bridge，验证 h2c stream、HTTP/1 inject/history、401/403 和 `chat.abort` 的显式拒绝。测试输出均为 PASS。 |
| ACI 覆盖率门禁 | PENDING | 本地未提供 PR base/head、JUnit、coverage XML 或远端 ACI job；不能用 shell/Node 通过替代 casePassRate、lineCoverage、changeLineCoverage 门禁。 |
| 静态检查 | PASS | `bash -n`、`node --check`、目标 TS ESLint 与 `git diff --check` 均成功。未发现本轮引入的未使用 import、未使用变量或空白字符问题。 |

### ACI 覆盖率证据

- Base / Head: PENDING / `71f7c14188be19631edda103fa57864f8600a732`；当前为未提交 worktree，且本地无 `main` ref。
- 用例: PENDING（未提供 JUnit）；threshold = 100%。
- 总行覆盖率: PENDING（未提供 coverage XML）；threshold >= 70%。
- 变更行覆盖率: PENDING（未提供 coverage XML 与目标 base/head）；threshold >= 90%。
- 未覆盖变更行: PENDING，需由远端 ACI job 按实际 PR base/head 计算。
- 远端 ACI job: PENDING。

### Review Spec 检查项

| 编号 | 检查项 | 结论 | 说明 |
|------|--------|------|------|
| R-01 | 当前 Provider 三 bot 的重复启动/停止不得遗留可误选的失效卡片 | PASS | `bcs_baas_provider_cleanup_registration` 用已保存的 Provider admin token DELETE 三个保存的 ref；失败时保留状态并拒绝重复注册。迁移旧无凭据状态只保留历史卡片且新名称带 `（当前）`。 |
| R-02 | Claude CLI 自动回退与显式路径 fail-closed | PASS | `spawnSync(..., ['--version'], { timeout: 5000 })` 判定可用性；显式路径失败直接退出，自动候选按 PATH/本机常见目录探测。 |
| R-03 | BCS Provider bridge 的授权、流转发、错误及取消 | PASS | runtime credentials 要求 BaaS token、BCS token 和绑定 ref；Node 合约测试覆盖 h2c split preface、final/error、HTTP/1 callbacks、ref mismatch、token mismatch 和中止上游流。 |
| R-04 | 前端人类发送者不可占用 `bot_uuid`，无 @ 时由 Driver 路由 | PASS | `UserCollabTab -> GroupChatPage -> useGroupChat` 将 `human_<id>` 作为 senderId，第 3 个 `botUuid` 参数显式为 `undefined`；transport 将 SDK `sender_id` 复制为 BCS `bot_id`。 |
| R-05 | 前端进程在 singlebox 退出后仍保持存活 | PASS | stdin keeper 和 npm 都处于同一个 `nohup bash -c` 进程组；守卫测试验证不会再留下 nohup 外的 `tail`。 |
| R-06 | 凭据、token、聊天正文不得写入新诊断 | PASS | 新增 bridge 与前端诊断只记录布尔值、计数、run ID 和引用；`useGroupChat` 的发送日志已由完整 `params` 改为 `contentLength`/布尔值/计数，守卫测试显式拒绝回归到完整参数日志。 |
| R-07 | standalone `status all` 的 BCSFuse PID 路径与运行时一致 | PASS | `bcsfuse_status` 在读 PID file 前调用 `bcsfuse_load_env`，使 standalone runtime 的 PID path 已解析；守卫测试覆盖该调用顺序，编排方随后实测 `status all` 报告 BCSFuse health PASS。 |

## 审查证据

```text
bash -n scripts/modules/bcs_baas_provider.sh scripts/modules/claude_relays.sh \
  scripts/modules/frontend.sh scripts/test_singlebox_mixed_claude_bots.sh \
  scripts/test_singlebox_service_guards.sh scripts/test_claude_relays.sh
node --check scripts/bcs_baas_provider_bridge.mjs
node --check scripts/test_bcs_baas_provider_bridge.mjs
bash scripts/test_singlebox_mixed_claude_bots.sh
# singlebox mixed Claude bot shell tests passed
bash scripts/test_singlebox_service_guards.sh
# PASS: singlebox service guard tests
node scripts/test_bcs_baas_provider_bridge.mjs
# BCS h2c Provider bridge contract tests passed
src/frontend/node_modules/.bin/eslint <four changed GroupChat files>
git diff --check
```

`test_claude_relays.sh` 会操作固定 18910–18912 relay PID，按本次只读评审约束未在已有本地栈上执行；CLI fallback 的单元契约已在该文件中审阅，实际 relay/BCS 浏览器 smoke 应由编排 QA 记录。

## 具体问题列表

无阻塞问题。

### 非阻塞观察

无。

## 补充评审（iteration 2）

本轮在第一轮 PASS 后追加的两个最小整改均通过只读复核：

1. `useGroupChat` 不再打印包含 `query` 和 `userMessage.content` 的完整 `params`；其新的 `console.debug` 仅记录 group ID、内容长度、mention 数和状态布尔值。
2. `bcsfuse_status` 先执行 `bcsfuse_load_env` 再读取 PID file，消除了 standalone 模式读取默认 `.runtime` 而误报 `pid_file: stale` 的路径错配。

补充实测：

```text
bash -n scripts/modules/bcsfuse.sh scripts/test_singlebox_service_guards.sh
bash scripts/test_singlebox_service_guards.sh
# PASS: singlebox service guard tests
src/frontend/node_modules/.bin/eslint src/frontend/src/pages/GroupChat/hooks/useGroupChat.ts
git diff --check
```

编排方另外在未重启的现有 local stack 上执行 `status all`，确认 BCSFuse health 为 PASS；该项作为运行时状态证据，不替代 ACI。

## 整体结论

**结论: PASS（本地代码与契约评审）**

代码实现满足本轮 Provider 生命周期、CLI fallback、WorkBench sender 映射和 frontend 存活契约。ACI 覆盖率门禁仍为 PENDING；在获得实际 PR base/head 与远端 job 前，不应把本报告表述为 ACI PASS 或部署门禁已完成。

## 迭代 3 独立复审

**结论：PASS（无阻塞问题）。**

- 普通 Chat 的 Provider 初始化仅在没有显式 `driver_delivery` 时静默 inject；
  `ManagerWorker` 仍总是 send，WebSocket-only 群仍维持原 Driver send 行为。
- 新增 Rust 用例覆盖 Provider 默认 inject、Provider + 显式 send override，以及
  Provider 参与的 ManagerWorker override 优先级。
- 前端正则只命中三个本地 Claude 角色的精确旧名称，并明确保留 `（当前）`
  版本；不会隐藏其他 Bot 或既有群成员。
- 本轮复审未发现凭据或完整聊天正文新增到诊断日志。ACI 覆盖率仍为 PENDING。
