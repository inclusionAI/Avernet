# 基线复现

基线为 `d2a964de9f5ceab3c33ac648df86fd575d6d3c64`。在独立 Chat 对局中，使用独立主持人、五名 Bot 玩家和一名人类，并以本机 SQLite 保存运行状态。

玩家节点运行时，确认进程身份后结束该玩家进程。run 在约 420 秒后失败；未生成主持人结果消息，也未观察到主持人后续活动。

另一次保持原有 15 分钟期限的人类输入超时，在约 900.7 秒后失败，同样没有结果消息和主持人后续活动。原始日志及运行数据保存在本机忽略目录。

## 修复后的自动化门禁（2026-11-02）

在组合测试分支执行默认全部启用模块的 Singlebox，预期所有 Backend 验收、BCS E2E、
覆盖率阈值和独立产物检查通过；实际门禁与产物校验均退出 0。Backend 63/63 通过，
BCS HTTP 166/166、CLI 66/66，变更行覆盖率 61.22%（322/526，要求 >60%）。
总体行/方法覆盖率为 49.37%/43.95%，分别满足 41%/36% 门槛。

`run_failure_story.py` 通过真实 HTTP 和投递调度器，在队列关/开下分别验证节点失败、
HumanInput 超时、主动取消和 Session 删除；核对创建者查询权限、安全结果、完成节点、
私有 Send 回执、重复读取和取消幂等。这里的短超时属于独立测试定义，不修改游戏的
900 秒 HumanInput 期限。

验收助手在等待个人 Bot 就绪前登记设备释放，并跟踪休眠重启后的替换绑定。
expert_chat 每项测试等待本项新建 caller 的异步发布结束，再释放设备；失败也执行清理
并报告错误。覆盖率启动器清理专用隔离 BCS 数据库，避免历史投递阻塞下一轮消息。
没有降低阈值、缩小分母或增加跳过项。

从仓库根目录设置目标分支与 HEAD 的 merge-base 后执行：

```bash
export BCS_E2E_CHANGED_BASE_REF="<目标分支与 HEAD 的 merge-base>"
export BCS_E2E_CHANGED_LINE_MIN=60
bash singlebox/ci/singlebox_coverage.sh
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
```

清理及轮询回归 6 项、覆盖率启动器回归、Ruff、shell 语法、Git 空白检查均通过。
报告由入口生成到 `singlebox/.dependencies/coverage/singlebox/reports/`，原始运行资料
保持本机忽略。此次结果只更新 Singlebox 与相关测试结论，不代表重新执行真实模型
故障注入、完整 Cargo workspace、MySQL 或整仓架构检查。
