# 真实 BCS state-machine cancel 边界证据（实际 CPython 客户端）

观测日期：2026-11-02；BCS：local standalone BCS 0.1.4-beta.3。

> 这不是一次成功的 `chat.abort`。这是对 Codex 指出的 `apps/bcs/crates/adapters/http/bcs-http/src/routes/collaboration_runs.rs:301` 路由的实测：`POST /state-machine-runs/{run_id}/cancel` 返回 aborted，但不向实际 CPython WS Bot 下发 `chat.abort`；迟到 final 在取消后仍发出。

## 路由与权限

- `POST /state-machine-runs/{run_id}/cancel`
- handler: `routes::collaboration_runs::cancel_state_machine_run`
- 权限查询：`GET /sessions/{sid}/state-machine-permission` -> allowed=`True`，reason=`allowed`，owner=`bot_43470f22`
- cancel HTTP status: `200`，run status=`aborted`，gate node=`running`
- run error: `controlled abort test`

## 实际 CPython 玩家

- seat1 / bot `bot_5d8e2188`，实际运行 `BcnClient`（GateBrain）。
- 收到 BCS `chat.send`：
  - request `smnode-sm-a2ee6e1c-eb0f-4f44-bc54-1db8022f9b0b-gate-0` node `gate`
- 发出的 V3 `chat` final：
  - run `smnode-sm-a2ee6e1c-eb0f-4f44-bc54-1db8022f9b0b-gate-0` state=`final` seq=1 content=`我投2号`

## 取消前后事件

### 取消后、释放 gate 前

| 时间 | 事件 | node |
| --- | --- | --- |
| 2026-11-02 01:40:08.171 | task_received | gate |

### 释放 gate 后（迟到 final）

| 时间 | 事件 | node | chars |
| --- | --- | --- | --- |
| 2026-11-02 01:40:08.171 | task_received | gate | None |
| 2026-11-02 01:40:11.181 | final_sent |  | 4 |
| 2026-11-02 01:40:11.181 | task_completed |  | 4 |

## 其他真实 API 探测

- Direct A2A: cancel returned state=cancelled; no chat.abort observed
- Group session: 404 bot_not_found; delivery cancel returned []
- IM bridge: `POST /channels/bindings` -> HTTP 400 `channel bridge is disabled`

## 结论

状态机 cancel 只标记 run aborted，未向 WS Bot 下发 chat.abort；IM channel bridge 在本机部署被禁用。可用 Bot API 路径不能触发真实 chat.abort。

迟到观察：取消后 WS client 仍收到 task_received，约 3 秒后发送 final_sent/task_completed；BCS run 保持 aborted，node 仍 running。

## 限制

- 这是取消路径边界证据，不是一次成功的 chat.abort。
- 未修改 BCS/主持人，未使用真实三局。

脱敏：不含 token、本机路径、真实对局数据。
