---
agent: tc-engine-regression
status: completed
created: 2026-08-08T18:30:00+08:00
---

# 本地回归报告：混合 Claude Code Bot

## 通过

| 范围 | 命令/套件 | 结果 |
| --- | --- | --- |
| Shell 与 bridge | `bash -n`、`node scripts/test_bcs_baas_provider_bridge.mjs` | PASS |
| 混合生命周期 | `bash scripts/test_singlebox_mixed_claude_bots.sh` | PASS |
| 服务防护与前端 sender/存活/日志、BCSFuse status 静态契约 | `bash scripts/test_singlebox_service_guards.sh` | PASS |
| CLI fallback | role relay 的真实候选 `--version` 探测 | PASS |
| 前端改动 | 四个改动文件的 ESLint | PASS |
| BaaS | `.venv/bin/pytest` 聚焦套件 + Ruff | 47 passed；Ruff PASS |
| Claude gateway | `egg-bin test` 聚焦套件 | 50 passing |
| 格式 | `git diff --check` | PASS |

## 运行时验收

真实 macOS Terminal 的 frontend 重启后，8000 持续监听；`status all` 显示三个 relay、BaaS、Backend、BCS、BCSFuse（health PASS）、Provider bridge、五个 OpenClaw bot 和三个 Claude bot 均运行。浏览器中的当前 Claude Developer 完成一次无副作用的 BCS 下行对话并渲染 final 回复，浏览器自动化未发现 JavaScript 错误。

## 环境说明

全局 `pytest` 缺少项目依赖而未执行测试；改用 worktree 自带 `src/baas/.venv/bin/pytest` 后通过。该情况不改变代码或服务状态。

## 迭代 3 回归：新群首条消息

| 范围 | 命令/检查 | 结果 |
| --- | --- | --- |
| BCS system-message | `cargo test -p bcs-system-message` | 45 unit + 7 conformance PASS |
| 单机防护 | `bash scripts/test_singlebox_service_guards.sh` | PASS |
| 混合配置/bridge | `bash scripts/test_singlebox_mixed_claude_bots.sh`、`node scripts/test_bcs_baas_provider_bridge.mjs` | PASS |
| 前端范围 | target ESLint、受管浏览器 UI smoke | PASS |
| 真实本机拓扑 | Terminal 启动后的 `status all` | 5 OpenClaw + 3 Claude、三个 relay、Provider bridge 与 Frontend 均 Running |

新建当前 Planner + Developer 群的初始化只投递 `chat.inject`。随后两个 Bot 都产生
final，BCS 会话历史及浏览器 DOM 中均未出现并发 session timeout。验收过程只使用
固定无副作用短语；报告不保存其正文、run ID 或任何凭据。
