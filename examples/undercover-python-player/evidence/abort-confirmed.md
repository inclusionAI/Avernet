# 真实 BCS `chat.abort` 与迟到结果抑制

2026-11-02，在独立分支 `codex/bcs-state-machine-cancel` 构建的本机 BCS（端口 21001、临时数据）上，使用实际 CPython `BcnClient` 执行单节点受控 state machine。Bot 的任务在 gate 上等待时，另一个临时 Bot 调用 `POST /state-machine-runs/{run_id}/cancel`。

| 检查项 | 观察 |
| --- | --- |
| 启动 | HTTP 202；玩家收到 `chat.send` |
| 取消 | HTTP 200；run 为 `aborted` |
| 精确下行 | 玩家收到 1 个 `chat.abort`；`run_id=smnode-sm-0e70c87f-bef4-4546-a083-d41ba1a5da46-gate-0`，`session_key=bcs_grp_1ac0a6cc6a1043aa828273fce610cf86:7f5f9988`，均与原任务一致 |
| 玩家响应 | 依次记录 `abort_requested`、`task_cancelled`、`aborted` |
| 放开 gate 后 | 没有 `final_sent` 或 `chat` final；BCS run 仍为 `aborted` |

机读数据见 [abort-confirmed.json](abort-confirmed.json)。这是修复后的独立 BCS 与实际玩家的协议级联调；三局人类混合牌桌本身没有触发取消。原部署缺口的修复前证据仍见 [abort-boundary.md](abort-boundary.md)。

测试 Bot 和服务端数据库均在临时目录，证据未包含 token、密钥、秘密词或原始群数据。
