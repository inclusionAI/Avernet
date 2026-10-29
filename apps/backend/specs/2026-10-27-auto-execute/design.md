# auto_execute 接口设计

## Problem

`benchmark_execute` 是评测链路里"由 API 创建评测会话 + Backend 自动执行任务"的内端点。它先发
评测 prompt 让 Bot 产出可回放的澄清卡片,再 `service.execute` 创建根节点,把副屏卡片和隐藏的 Relay
根指令作为第二条消息发回同一会话,使评测不开新会话也能自动执行。

实际场景里有一种"静默执行"诉求:目标会话已经存在(由前端/外部系统准备好),不需要再发任何 prompt,
也不需要走 BCS A2A 通道,只需要直接:

1. 触发 `service.execute` 创建任务根节点;
2. 取 Backend 生成的 `root_execution_instruction`;
3. 拼副屏卡片 + 隐藏 Relay 根指令;
4. 再向**入参指定的同一会话**发一次启动消息即可。

`/execute` 不发会话消息、`/benchmark_execute` 强制发第一棒 prompt 且要走 A2A 分支判定,都不满足这种
"已有会话 + 静默启动"的诉求。本项目缺少一个轻量、纯 OpenAPI、单会话自动启动的内端点。

## Solution

新增 `POST /api/v1/collaboration/tasks/auto_execute`,与 `benchmark_execute` 并列,挂在 `task` 路由
之前避免 `auto_execute` 被识别为 task_id。端点不复用第一棒 prompt 发送,缺少 A2A 分支,只走 OpenAPI
通道,其余与 `benchmark_execute` 的 openapi 分支语义对齐。

### 接口契约

- 路径:`POST /api/v1/collaboration/tasks/auto_execute`
- 入参:复用 `TaskInfoRequestDTO`。`ExecutionConfigDTO` 新增一个文档化字段(仅 `auto_execute`
  消费),其他端点的语义不变:
  ```
  main_session_id: str | None = Field(
      None,
      description=(
          "目标 bot 会话 ID(仅 auto_execute 消费;与 benchmark_execute 通过 send_message "
          "返回拿到 session_id 不同,auto_execute 由调用方直接指定)"
      ),
  )
  ```
  保留 `Optional`:该 DTO 与 `/execute`、`/benchmark_execute` 共享,设必填会破坏既有契约。端点入口
  做 `main_session_id` 非空校验,缺失时直接以失败 `Envelope` 返回,不抛 HTTPException。
- 出参:`Envelope[TaskOpResultDTO]`,与 `/execute`、`/benchmark_execute` 一致。

### 执行流程

`auto_execute_task_internal(body, request, service, bot_port) -> Envelope[TaskOpResultDTO]`:

1. `main_session_id = str(body.execution_config.main_session_id or "").strip()`;
   为空 → `TaskOpResult(success=False, error="auto_execute requires main_session_id",
   extend_props={"auto_session_id":"", "auto_launch_send":"error: missing session_id"})`,
   `return envelope(...)`;
2. `task_request = task_info_request_from_dto(body)`;
3. `execution_config = dict(task_request.execution_config)`,根据"lifecycle_stage 默认 draft
   且允许透传覆盖"的决策:
   ```
   execution_config["main_session_id"] = main_session_id
   execution_config.setdefault("lifecycle_stage", "online")
   task_request = replace(task_request, execution_config=execution_config)
   ```
4. `result = await service.execute(task_request)`;失败则 `result.extend_props` 合并
   `{"auto_session_id": main_session_id, "auto_launch_send":"skipped: execute failed"}`
   后返回,不进入 step 5-9;
5. `root_instruction = result.extend_props["root_execution_instruction"] or ""`,
   `result_mode = result.extend_props["orchestration_mode"] or ""`;
6. 若 `result_mode == "relay" 且 root_instruction.strip() == ""`,替换
   `result(success=False, error="relay task creation returned no root execution instruction")`,
   合并 `auto_session_id/auto_launch_send=error` 后返回(与 benchmark_routes.py:183 行一致);
7. `launch_message = build_benchmark_task_launch_message(
        task_id=result.task_id,
        owner_user_id=task_request.owner_user_id,
        task_spec=body.task_spec.model_dump(),
        root_execution_instruction=root_instruction)`
   非 relay 模式下 `root_instruction` 为空,`launch_message` 退化为纯副屏卡片,不报错;
8. `bot_identity = compose_bot_identity(body.owner_bot_id, body.owner_user_id)`,
   `launch_sent = await bot_port.send_message(
       bot_id=bot_identity,
       message=launch_message,
       metadata={
           "benchmark": False,
           "auto": True,
           "session_id": main_session_id,
           "sender_options": {"from": task_request.owner_user_id},
           "bot_options": {"lifecycle_stage":
               task_request.execution_config.get("lifecycle_stage", "online")},
       })`;
   `bot_port is None` → 与 benchmark_routes.py:120 一致的失败 envelope;
   抛异常 → 替换 `result(success=False, error="auto task launch send failed: <exc>")`,
   `extend_props` 合并 `auto_launch_send=f"error: <exc>"` 后返回;
9. `await service.update_task_node_info(
       result.task_id, result.node_id or result.task_id,
       extend_props_patch={"run_id": launch_sent.run_id})`
   把第二棒真正的根执行 run_id 回写到根节点,方便 harness 探针/resume 指到精确的 engine run
   (与 benchmark_routes.py:236 行的 openapi 分支语义对齐);
10. `result.extend_props` 合并
    `{"auto_session_id": main_session_id, "auto_run_id": launch_sent.run_id, "auto_launch_send":"ok"}`,
    返回 `envelope(op_result_to_dto(result), request)`。

依赖最少:`TaskServiceProtocol` + `OpenApiBotPort`;不注入 `BcsClientPort`/`BcsBotTokenProvider`。

### 错误与可见性

- `OpenApiBotPort is None` → 服务降级,返回失败 envelope(与 benchmark openapi 分支一致);
- `service.execute` 返回 `success=False` → 不再发消息,合并 extend_props 直接回;
- `bot_port.send_message` 抛错 → 替换 `result.success=False` 但任务已建图,要把
  `auto_launch_send=error:...` 写回 `extend_props`;
- `update_task_node_info` 抛错 → 不阻塞返回成功:任务已落库并已发出 launch 消息,与 benchmark 一致
  (两者都没有 try/except 包裹 update_task_node_info,失败让框架 envelope_errors 映射 5xx)。

### 与 benchmark_execute 的差异

| 维度 | benchmark_execute | auto_execute |
| --- | --- | --- |
| 第一棒 prompt 发送 | 必发(`format_benchmark_prompt` → `bot_port.send_message`) | 不发 |
| 通道 | openapi / a2a 二选一(根据 target_bot_id+target_user_id 是否同时非空) | 仅 openapi |
| 会话来源 | 第一棒 send_message 返回的 session_id(a2a 是 `compose_benchmark_a2a_session_id`) | 入参 `main_session_id` |
| owner 是否改写 | a2a 分支把 owner bot/user 改写为目标 bot/user | 不改写 |
| `bot_token_provider` 依赖 | 是(a2a 分支) | 否 |
| metadata 标记 | `benchmark: True` | `benchmark: False, auto: True` |
| extend_props 键前缀 | `benchmark_*` | `auto_*` (除透传的 root_execution_instruction/orchestration_mode) |

## Validation

新建 `apps/backend/tests/community/adapters/http/task/test_auto_execute.py`,沿用
`test_benchmark_execute.py` 的 stub 模式但只装 `_StubService` + `_StubBotPort`:

- **happy (relay)**:`execution_config.main_session_id` + service.execute 返 relay mode →
  断言
  - `service.requests[0].execution_config["main_session_id"] == <预期 session_id>`,
    且 `lifecycle_stage == "online"`(无透传时);
  - `bot_port.send_calls[0].metadata["session_id"] == <预期 session_id>`,
    `sender_options.from == task_request.owner_user_id`,
    `bot_options.lifecycle_stage == "online"`,
    `metadata["benchmark"] is False`,`metadata["auto"] is True`;
  - `launch_message` 含 `<!--` 隐藏指令 wrapper(因为 root_instruction 非空);
  - `service.node_updates[0]["extend_props_patch"] == {"run_id": "openapi-run-1"}`;
  - response `extend_props` 含 `auto_session_id/auto_run_id="openapi-run-1"/auto_launch_send="ok"`;
- **happy (非 relay)**:`orchestration_mode != "relay"`,`root_instruction` 空 → 同样返回
  成功,`launch_message` 不含 `<!--` 包装,元数据 lifecycle_stage / metadata 仍满足约束,
  不报错;
- **error: main_session_id 缺失**:body `execution_config.main_session_id = None / ""`
  → 返回 `success=False`, `error` 含 `requires main_session_id`,
  `extend_props.auto_launch_send` 含 `error: missing session_id`,
  一次 `bot_port.send_calls == []`,`service.execute` 不被调用;
- **error: relay 模式 root_instruction 空**:`orchestration_mode == "relay"` + 空指令 →
  失败 envelope,`error` 含 "relay task creation returned no root execution instruction",
  `bot_port.send_calls == []`;
- **error: bot_port.send_message 抛 RuntimeError**:`_StubBotPort.fail_calls = {1}` →
  失败 envelope,`error` 含 "auto task launch send failed",`extend_props.auto_launch_send`
  含 `error: openapi send failed`;
- **error: bot_port 未注入**:`bot_port = None` → 失败 envelope,
  `error` 含 "bot port is unavailable";
- **透传覆盖**:body `execution_config.lifecycle_stage = "stable"` → service.requests[0]
  中 `lifecycle_stage == "stable"`,且 metadata.bot_options.lifecycle_stage == "stable"
  (验证 `setdefault` 不覆盖透传)。

`coverage_baseline.txt`:补对齐 benchmark_execute 的标注法:
```
POST /api/v1/collaboration/tasks/auto_execute: missing [happy, error]
```

## Compatibility and risk

- 新增端点 + 新增 DTO 透传字段,`ExecutionConfigDTO` 用了 `extra="allow"` 之外的显式 Optional
  新增,不破坏 `/execute`、`/benchmark_execute` 的入参契约;
- 新增字段默认 `None`,`task_info_request_from_dto` 已通过 `model_dump(exclude_none=True)`
  透传,旧端点行为不变;
- `build_benchmark_task_launch_message` 仍由 auto_routes 直接复用,不重命名,benchmark 及其测试
  不受影响;
- 路由顺序:必须 `router.include_router(auto_api_router)` 在 `task_read_api_router` /
  `artifact_api_router` 之前(与 benchmark_api_router 同梯队),否则 `/{task_id}` 动态路由会吃掉
  `auto_execute` 这个静态 path;
- 无引入硬编码 URL/token,与架构约束一致。

## Spec

落地清单:
1. 新增 `apps/backend/src/agentclaw/community/adapters/http/task/auto_routes.py`;
2. 改 `apps/backend/src/agentclaw/community/adapters/http/task/router.py:44/91`:
   import + `router.include_router(auto_api_router)`(放在 benchmark_api_router 之后、
   task_read_api_router 之前);
3. 改 `apps/backend/src/agentclaw/community/adapters/http/task/schemas.py:103-129`:
   `ExecutionConfigDTO` 新增 `main_session_id` 字段及描述;
4. 新增 `apps/backend/tests/community/adapters/http/task/test_auto_execute.py`;
5. 更新 `apps/backend/tests/community/framework/coverage_baseline.txt`:
   追加 `POST /api/v1/collaboration/tasks/auto_execute: missing [happy, error]`;
6. 提交时遵循 PR conventions:`feat(backend): add auto_execute endpoint`。
