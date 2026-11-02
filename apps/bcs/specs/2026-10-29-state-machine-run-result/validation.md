# StateMachine 恢复与 Singlebox 验收记录

## 当前结论（2026-11-02）

默认全部启用模块的 Singlebox 门禁和独立产物校验均通过，退出码均为 0。
本轮在 #3389 组合分支执行；相关门禁修复纳入 #3366，由 #3389 同步继承。
两边相应测试与运行脚本一致，不将组合分支实跑表述为两次独立全量运行。

| 检查 | 期待 | 实际结果 |
| --- | --- | --- |
| Backend 默认 12 模块验收 | 全部通过 | 63 通过、0 失败 |
| BCS HTTP / CLI | 100% / 100% | 166/166、66/66 |
| BCS 变更行覆盖率 | >60% | 61.22%，322/526 |
| BCS runtime line / method | >=41% / >=36% | 49.37% / 43.95% |
| devices router | >=44.44% | 88.89%，16/18 |
| expert_chat core / router | >=61.18% / 100% | 63.14% / 100%，11/11 路由 |
| 清理与轮询回归 | 全部通过 | 6 通过 |
| 覆盖率启动器回归 | 通过 | PASS |
| 独立产物校验 | 报告完整且满足门槛 | verified，退出 0 |

BCS E2E 脚本退出 0，原始摘要为 Passed 779、Failed 0、Total 780，保留原始计数，
不改写为 780 项通过。其余启用模块均满足现有阈值，pending 名单未变。
Ruff、shell 语法、Git 空白检查均通过；本轮新增或修改源文件最大 651 行。

## 本轮修复

- 真实 HTTP E2E 在队列关/开下分别验证节点失败、HumanInput 超时、主动取消和
  Session 删除；检查创建者查询权限、安全结果、完成节点、私有 Send 完成回执、
  重复读取及取消幂等。范围失效后拒绝查询且不继续投递。
- 个人 Bot 就绪前登记设备释放，失败也清理，并读取休眠重启后的替换绑定。
- expert_chat 只清理本项测试新建的 caller，等待异步发布结束后释放；失败也执行清理，
  并报告清理错误，避免共享栈累积运行环境。
- 覆盖率入口清理专用隔离 BCS 数据库，避免历史投递使下一轮消息被 queue_draining
  拒绝而漏执行 chat/callback。
- 未降低阈值、缩小分母或新增跳过。短超时属于独立 E2E 定义，不修改游戏的 900 秒期限。

## 复跑与范围

设置 BCS_E2E_CHANGED_BASE_REF 为目标分支与 HEAD 的 merge-base，并设置
BCS_E2E_CHANGED_LINE_MIN=60 后，从仓库根目录运行：

```bash
bash singlebox/ci/singlebox_coverage.sh
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
```

报告位于上述 reports 目录，包括 summary.json、summary.md、dashboard.html、验收
JUnit/日志与各组件覆盖率。本轮使用临时 OpenClaw 2026.6.1 和 mock 模型，原始资料、
数据库及工具安装保持本机忽略；本轮测试服务已按归属清理。
本次不代表重跑完整 Cargo workspace、全部事件/OpenAPI、整仓架构检查、MySQL、
高并发或真实模型故障注入，不声明整仓 CI 全绿。

## 最近一次真实恢复验收（2026-11-01）

五个真实 DSH 玩家与独立主持人，发言和真实玩家投票各五次，固定种子选择；
投票每次五个自然并行候选，排除 vote_start，不暂停其他玩家。
十次真实 kill 均在 60 秒内重开同会话、同轮同阶段：10/10，6.732–13.626 秒，
中位数 10.311 秒。结果、Send、failed 事件各一条，目标仅为创建者主持人，
Send 最终 completed；恢复窗口人类消息和节点完成数均为 0。
样本对照未检出本局玩家词和完整节点产物，不扩展为全面隐私证明。

独立三次同阶段故障中，前两次 9.826、7.739 秒恢复；第三次 RECOVERY_EXHAUSTED，
重试数 2、总 run 数 3，后续 97.291 秒没有第四个 run。第三次停止符合预期。
75.920 秒实时脱敏监视页视频已本地交付，尚无公开附件链接，未展示产品副屏。

逐次时间链与批次范围见
[真实恢复报告](https://github.com/inclusionAI/Avernet/pull/3389#issuecomment-6558741766)。
本次 mock E2E 与真实模型验收分别保留，不混算成功率。

## 历史证据

此前 Singlebox 的启动失败、覆盖率不足和 expert_chat 超时已经本轮全量实跑补验，
不再作为当前结论。Cargo、MySQL、整仓架构及协议检查保留各自批次范围，本轮
Singlebox 通过不覆盖其结果。失败原始资料保留，公开批次说明见
[历史完整测试报告](https://github.com/inclusionAI/Avernet/pull/3389#issuecomment-6557885443)。
