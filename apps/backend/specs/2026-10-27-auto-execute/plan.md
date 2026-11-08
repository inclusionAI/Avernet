# auto_execute 实施计划

参考 `./design.md`。逐步可独立验证。

## Step 1 — schemas 加 `main_session_id`

- 文件:`apps/backend/src/agentclaw/community/adapters/http/task/schemas.py`
- 在 `ExecutionConfigDTO` 上(紧邻 `target_bot_id`/`target_user_id` 之后)新增:
  ```python
  main_session_id: str | None = Field(
      None,
      description=(
          "目标 bot 会话 ID(仅 auto_execute 消费;与 benchmark_execute 通过 send_message "
          "返回拿到 session_id 不同,auto_execute 由调用方直接指定)"
      ),
  )
  ```
- 验证:`from agentclaw.community.adapters.http.task.schemas import ExecutionConfigDTO`,
  `ExecutionConfigDTO(task_type="dynamic", main_session_id="s-1").main_session_id == "s-1"`,
  且不传时为 `None`。

## Step 2 — 新增 `auto_routes.py`

- 文件:`apps/backend/src/agentclaw/community/adapters/http/task/auto_routes.py`(新建)
- 完整导入:`APIRouter`、`Envelope`、`envelope_errors`、`envelope`、`TaskInfoRequestDTO`、
  `TaskOpResultDTO`、`op_result_to_dto`、`task_info_request_from_dto`、`TaskServiceProtocol`、
  `compose_bot_identity`、`TaskOpResult`、`OpenApiBotPort`、`build_benchmark_task_launch_message`、
  `Injected`、`replace`、`Any`;
- `router = APIRouter(prefix="/auto_execute", tags=["task"])`;
- 端点 `@router.post("", response_model=Envelope[TaskOpResultDTO]) @envelope_errors
  async def auto_execute_task_internal(body, request,
      service: TaskServiceProtocol = Injected(...),
      bot_port: OpenApiBotPort = Injected(...))`;
- 实现按 design.md "执行流程" 10 步;`main_session_id` 校验失败、`bot_port is None` 等失败路径都走
  `return envelope(op_result_to_dto(TaskOpResult(...)), request)`;
- `metadata.bot_options.lifecycle_stage` 取
  `task_request.execution_config.get("lifecycle_stage", "draft")`,因为 step 3 已经 `setdefault`,
  等价于直接取 `task_request.execution_config["lifecycle_stage"]`。

## Step 3 — 注册到 router.py

- 文件:`apps/backend/src/agentclaw/community/adapters/http/task/router.py`
- 第 44 行附近(import 区):
  ```python
  from agentclaw.community.adapters.http.task.auto_routes import router as auto_api_router
  ```
- 第 91 行后(在 `benchmark_api_router` 之后、`task_read_api_router` 之前):
  ```python
  router.include_router(auto_api_router)
  ```
- 验证顺序:静态路由 `auto_execute` / `benchmark_execute` 必须在动态 `/{task_id}` 之前注册;
  否则 `auto_execute` 会被解析成 task_id。

## Step 4 — 测试 `test_auto_execute.py`

- 文件:`apps/backend/tests/community/adapters/http/task/test_auto_execute.py`(新建)
- 沿用 `test_benchmark_execute.py` 的 stub 模式;仅 `_StubService`(含 `execute`/`update_task_node_info`)
  和 `_StubBotPort`(含 `ensure_grant`/`send_message`,`BotSendResult(run_id="openapi-run-1",
  session_id="auto-session-1")`);不需要 `_StubBcsClient`/`_StubBotTokens`;
- 一次性构造 FastAPI app + attach_injector + 注入两个 stub(参照 benchmark 测试的 wiring);
- 用例覆盖 design.md "Validation" 段罗列的所有 7 类:happy(relay)、happy(非 relay)、
  error:main_session_id 缺失、error:relay 空 root_instruction、
  error:send_message 抛错、error:bot_port 未注入、透传 lifecycle_stage 覆盖 draft 默认;
- 断言点见 design.md "Validation"。

## Step 5 — 更新 coverage_baseline.txt

- 文件:`apps/backend/tests/community/framework/coverage_baseline.txt`
- 在 `benchmark_execute` 标记附近追加:
  ```
  POST /api/v1/collaboration/tasks/auto_execute: missing [happy, error]
  ```
- 当 happy + error 用例都补齐后,在 PR 描述里把本行标记为已覆盖或从 missing 列表移除。

## Step 6 — 运行产物

- 运行 `pytest apps/backend/tests/community/adapters/http/task/test_auto_execute.py -q`
  通过;
- 运行 `pytest apps/backend/tests/community/adapters/http/task/test_benchmark_execute.py -q`
  做回归 benchmark 没破坏;
- 全量工程结构 lint/type 检查(依据 `docs/arch/ci.enforce.md` 的 gate)不被减弱。

## Step 7 — Commit

- 以 `feat(backend): add auto_execute endpoint` 作为 title(`AGENTS.md` PR conventions);
- Description 段按 `Problem / Solution / Validation / Compatibility and risk / Spec` 顺序,
  内容可压缩引用本 `design.md` 与 `plan.md`;
- spec 文档随 PR 一起提交(包含本目录 `2026-10-27-auto-execute/design.md`、`plan.md`)。
