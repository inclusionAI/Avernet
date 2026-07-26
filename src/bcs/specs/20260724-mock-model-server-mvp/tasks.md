# Mock Model Server MVP 开发任务

状态：`[ ]` 待办 · `[~]` 进行中 · `[x]` 完成 · `[!]` 阻塞

## Task 1：sender-based 协议修订 — [x] 完成

目标：移除调用方 UUID 负担，使用真实 OpenClaw 自动注入的 sender metadata。

完成条件：

- [x] Spec 移除 request ID、UUID、ambiguous 和 invalid 契约。
- [x] 只读取最新一条 `role=user` 消息。
- [x] 支持字符串和数组 `type=text` content。
- [x] 从 `Sender (untrusted metadata)` JSON 提取 `label`。
- [x] 使用确认的 sender 固定回复句式。
- [x] 缺少或损坏 sender 返回 400 `missing_sender` 和 UTC 时间戳。
- [x] 协议单元测试和真实 loopback HTTP 测试通过。
- [x] Singlebox 回归和最终 diff 检查通过。

验证：

```bash
python3 scripts/modules/test_mock_model_server.py
bash scripts/test_singlebox_model_config.sh
bash -n scripts/modules/model_config.sh
bash -n scripts/singlebox.sh
git diff --check
```

## Task 2：Mock Server 与 Singlebox 生命周期 — [x] 完成

目标：提供本地 OpenAI-compatible Server，并只在 mock 模式下由需要模型配置的
Singlebox 目标管理。

完成条件：

- [x] 实现 `GET /health` 和 `POST /v1/chat/completions`。
- [x] `stream=true` 仍返回普通 JSON。
- [x] 生成指向 loopback 地址的 OpenClaw provider。
- [x] 支持默认端口和 `SINGLEBOX_MOCK_MODEL_PORT` 覆盖。
- [x] start、restart 和 stop 管理 Mock Server 进程。
- [x] manual 和 home 模式不管理 Mock Server。
- [x] 不使用未消费的 ready file。

## Task 3：OpenClaw sender metadata 映射 — [x] 完成

目标：把 BCS 已有的 sender display name 和 actor ID 交给 OpenClaw，让 Mock Server
可以原样回复 OpenClaw 生成的 sender label。

完成条件：

- [x] `chat.send` 使用 `actor_name` 和 `actor_id`。
- [x] `chat.inject` 使用同一套 sender 映射规则。
- [x] Human 和 Bot sender label 测试通过。
- [x] 不修改 BCS Service API、Plugin API 或消息协议。

## Task 4：完整 BCS 回复闭环 — [ ] 后续任务

目标：在 BCS E2E 中发送普通消息，验证真实 OpenClaw 经 Mock Server 返回 sender 问候。

该任务不属于本次 Mock Server 协议修订，不得用 HTTP 单测替代。
