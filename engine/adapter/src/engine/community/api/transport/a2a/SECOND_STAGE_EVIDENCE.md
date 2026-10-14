# A2A 阶段 1a 第二关验收 — 真实链路证据（2026-10-14）

环境：OpenClaw gateway 2026.5.12（profile=ceo, :30001, auth=token）+ engine adapter
（openclaw, :20003）+ singlebox mock model server（:18080）。
四项全部按评审加强断言执行。

## F2 真引擎 delta/final 拼接 — PASS

- SSE 状态 200 text/event-stream；事件序 task → statusUpdate(WORKING) →
  artifactUpdate ×2 → statusUpdate(COMPLETED) → statusUpdate(final marker)
- final 全文（F,T 终稿替换帧）：`[from OpenAI-compatible Mock Model Server]: Hi,
  gateway-client, now time is …` —— 原文完整、无 `[object Object]`、无重复追加
- artifact seq 严格单调；终态 `metadata.final="true"` 在场
- 真实副作用发现：gateway 产出 `state=delta, hasText=False` 的空增量帧（无
  deltaText/delta 字段）——bridge 按 R12 分流规则**跳过该帧不误读累计快照**，
  真实引擎上验证了"缺增量字段宁可跳过"分支

## F3 真旁观注入不终止主 run — PASS

流程：主 run 流式进行中（0.4s 处）InjectContext 哨兵文案注入。

- 主 run 终态 = TASK_STATE_COMPLETED（不被 inject 打断）
- 终稿逐字检查**不含哨兵文本**（无旁观污染）
- transcript（/api/sessions/f3-ctx/messages）逐字含哨兵
- 流内 message 成员 = 0：gateway 的 live-inject-listener 机制（与
  ws_server :1746-1763 同款）在此配置下不向未注册监听者的 chat 流推播
  inject 帧；bridge 的 inject-帧隔离分支已由 R14 fake-stream 回归
  （test_engine_inject_frame_isolation）覆盖——两层共同闭环

## F4 AuthGate 真开关 — PASS

- 真 REST 面开/关：`POST /api/openclaw/zero-check {"enabled": true}` 实测翻转
- gate 开启后：**无 x-iam-token 的 SendStreamingMessage 与 InjectContext 均被拒**
  （-32000 auth_required）
- 零副作用断言（评审要求）：gate 拒绝后 kernel runs 数、幂等键数、
  context 订阅者数、inject 去重表全部零变化——无 run 注册、无派发、无广播
- 验收后关开关还原现场

## F6 引擎 transcript 逐字 — PASS

- InjectContext 哨兵文案 → 引擎真实 transcript（session messages）逐字一致：
  `content == "F6验收-旁观注入原文ABC-20261014"`（完整字符串，非对象污染）
- role=assistant（openclaw adapter 对 transcript-only assistant role 的
  既有修复行为）

## 验收边界

- claude_code 引擎路径未在本轮验收：本机无独立 claude CLI（gateway spawn 后
  "Claude Code process terminated by signal SIGKILL"——CLI 不在 PATH 且无
  CLAUDE_CODE_PATH）。claude 侧 delta/final 拼接由 R14 落仓回归
  （test_payload_delta_vs_snapshot_split + bridge 帧序测试）覆盖其 wire 契约，
  单机真 claude 引擎验收留待有 CLI 环境后补测。
- mock model 回复为单发（真模型会有多增量 delta），多 delta 流式拼接行为的
  wire 契约由落仓回归覆盖。