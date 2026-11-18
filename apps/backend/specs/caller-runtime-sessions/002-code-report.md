---
agent: tc-code
status: completed
created: 2026-11-18T11:19:24.753449+08:00
iteration: 1
---

# 编码报告

## Worktree 信息
- 路径: /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/caller-runtime-sessions-20261118
- 基线: 最新 dev 89363b1407；带入 2c0de09306、8a504c556e 的未提交功能。
- 本编码 worker 未 commit/push/deploy，保留交接时的 staged 改动。

## 改动
- `expert_chat_caller_sessions.py`: 原 cron discovery 推广为独立 Caller 全量 runtime session discovery。只接纳非空白字符串 ID，保留原值，页内/跨页去重；保留真实运行时时间和索引写失败传播。
- `expert_chat_service.py`: 使用通用 Caller helper 和 merge 命名，保留有历史 rows 的既有生命周期。
- engine strategies: `supports_caller_session_discovery` 默认关闭、原 OpenClaw 范围启用，其他引擎不扩展。
- empty-index helper 在任何 Caller 账本读取前按 `requires_caller_chat_instance` 策略排除共享实例；无实例/未就绪只读且不 provision。
- README 同步全量会话和共享账本边界。
- 专属单测扩展普通/cron/subagent、重复和无效 ID；真实仓库双主体隔离用子会话验证。
- HTTP acceptance 原文件参数化 ordinary/cron/subagent，保留真实 Engine HTTP 创建、Backend HTTP 发现与真实所有权 SQL 回查。

## 验证
- RED: 新增普通/子会话发现测试只返回 cron，Teclaw empty 查询误读 Caller 账本，共 2 项按预期失败。
- GREEN: 专属单测 30/30 PASS，含精确筛选、favorites、分页、无效 ID/去重、真实仓库双主体隔离、missing/unready 不 provision、Teclaw authoritative metadata 优先且不读 Caller ledger。
- ExpertChat 全领域 + architecture: 715 PASS / 43 既有警告，69.13 秒。
- 独立 reviewer: 专属 + source guard 共 31 PASS，见 003 报告。
- ruff F401/F821/F841 PASS；git diff --check PASS。E203/E265 需 preview 的首次提示后以 preview 重验。
- 新 helper 121 行，主服务 969 行；相关源码均小于 1000。
- 所有测试使用独立 `uv sync --frozen` venv，进程 `GIT_TRACE2_EVENT=0`，PATH 前置 venv 和 openssl。
- 真实 HTTP acceptance 已扩展，尚未由本 worker 执行；full Backend CI、live module coverage、远端 CI 由编排器独立执行，不将单测替代真实服务证据。

## 引擎与实例矩阵
- OpenClaw：独立 Caller 的 published online（legacy draft selector仍按online）可发现全部实际ID；共享Owner/service继续index-only。
- Teclaw：draft/verify/online可同一物理实例；stage/binding差异不证明独占，empty GET不读Caller账本，不发现未登记会话；有rows保留原共享连接路径。
- 其他引擎：discovery capability默认false，不新增能力。
- 此变更不涉及IAM/ASC/换签/到期ledger，保留既有安装和刷新行为。

## 边界日志
沿用请求/成功/失败/跳过事件，推广为 `caller_runtime_sessions_*`；包含系统/操作/route/method/Bot/owner/user/binding/offset/limit、状态、selected keys、耗时。失败只记录异常类型，不记录异常原文或连接headers。成功/失败凭据不落日志测试通过。
