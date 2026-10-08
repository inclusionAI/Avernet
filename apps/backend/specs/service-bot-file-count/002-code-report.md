# 文件计数接口：编码与集成汇总

- Worktree：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-file-count-rel20260922`
- Feature：`feat/service-bot-file-count-rel20260922`
- 基线：GitHub `inclusionAI/Avernet` 的 `REL20260922 @ b2014be448b887068881ffafa7d9e09a1e4272ac`。
- 实现范围与文件明细见 `002-code-backend-report.md`、`002-code-engine-report.md`；前端合同见 `004-frontend-api.md`。

## 结果

新增 `GET /api/service-bot/publish/ops/file-count`，bot_id/entity_id/stage/path 必填。管理权限、阶段绑定、双 provider 定址复用现有设施；多实例分别返回数量或 null 失败。Backend 并发上限 2、实例调用截止 30 秒。

Engine 新增 `GET /api/file/count`。生产 OpenClaw 文件端口递归统计普通文件，不展开压缩包、不跟随链接、不应用 ignore。限定当前引擎根，fd 相对操作防路径替换；固定独立 worker 截止 10 秒、2 槽、繁忙立即失败；取消和截止终止回收 worker。Claude Code/内存 local double 未定义唯一物理根，明确 unsupported，不猜根或回退全量列表。

## 已执行验证

- 修改前基线：Backend 123 passed；Engine 53 passed。
- Backend 新增行为/协议/API：53 passed；注册端点 7 passed；架构 314 passed。
- 主编排跨组件合同：真实 Backend runtime → Engine HTTP → OpenClaw adapter/port → 临时文件系统，20 passed（BaaS/ARCA × 三阶段 × 相对/绝对路径，以及不存在/文件/链接/越界失败）。仅设备发现与 HTTP 连接为本地替身。
- Backend 全量：19,626 passed、43 skipped、0 failed，400.73 秒；最终新增用例和跨层集成补跑 73 passed，覆盖已合并。
- Engine 独立全量：2,845 passed、0 skipped、0 failed，按 CI 排除 5 个 corp-profile 用例，97.25 秒；总行覆盖 41,150/43,922（93.69%）。
- 新增模块 lint、Engine SAST 和 git diff --check 通过。详细覆盖与最终提交增量门禁见独立评审/回归报告，尚未执行远端 CI。
- 对不可变待提交树 `e445d39d6a4e43d538d0705289b3a0533bbb4226` 运行仓库 report_check：Backend 总行 89.21%、变更行 205/205（100%）；Engine 总行 93.69%、变更行 604/606（99.67%，CI source-root 包括 Engine 内嵌测试）。两侧脚本门禁通过；Backend 脚本用无失败用例含 skipped 计算通过率，不能据其 19,669/19,669 声称没有跳过。

## 范围与验证限制

全量 Backend SAST `bash scripts/ci/python_sast_local.sh src/backend 1` 失败，4 条均在基线 `b2014be44` 已存在，三文件对基线 diff 为空：`core/task/task_runner/client/__init__.py:22` 的 F821（返回类型 TaskExecutor 仅在函数体导入）；`tests/community/core/workspace/test_path_factory_skills_dir.py:6` 与 `tests/community/plugins/test_resource_tenant_guard.py:20,22` 的 E702。未修复无关文件，等待用户决定是否授权单独的最小基线修复。不能将全量 SAST 写为通过；当前尚未 commit/rebase/push。

没有部署、重启 Bot、改数据库、修改 ignore、调用真实模型或变更旧 worktree。没有运行全局旧 OCB/硬编码凭据的模型回归脚本；这些外部环境场景不能视为通过。Singlebox 与远端 CI 状态将在 PR 阶段按真实 job 更新。

扫描是瞬时观测而非原子快照；目录并发变动时允许明确失败。配置根各级必须为实际目录，不能穿越符号链接。前端只能在 status=success 时展示计数，不累加副本，不把 null 显示为零。
