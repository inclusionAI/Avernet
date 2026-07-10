# Task 1 报告：通用 owner ID 批量配置读取器

## 实现内容

- 在 `CommonWhiteListService` 新增 `get_owner_ids(*, business_code, param_code, env) -> frozenset[str]`。
- 使用 `CommonConfigService.get_value`，固定传入 `default=None`、`only_enabled=True`。
- 配置缺失或禁用时返回空 `frozenset`；列表元素统一转字符串、去除首尾空白、丢弃空值和空白项，并自动去重。
- 非列表配置记录不包含原始值的错误日志，并抛出 `ValueError`。
- 配置读取异常记录上下文、原样重新抛出；未改变 `is_bot_feature_enabled` 行为。
- 新增规范化、空配置、非法类型、读取失败四类测试。

## RED 证据

命令（`src/backend`）：

```bash
DEPLOY_PROFILE=test uv run pytest tests/community/core/common_config/test_common_whitelist_service.py -v
```

关键输出：

```text
collected 14 items
7 failed, 7 passed, 14 warnings
AttributeError: 'CommonWhiteListService' object has no attribute 'get_owner_ids'
```

新增 7 个测试均按预期因接口尚不存在而失败，原有 7 个 bot 白名单测试通过。

## GREEN 与质量检查

命令（`src/backend`）：

```bash
DEPLOY_PROFILE=test uv run pytest tests/community/core/common_config/test_common_whitelist_service.py -v
uv run ruff check src/agentclaw/community/core/common_config/whitelist_service.py tests/community/core/common_config/test_common_whitelist_service.py
```

关键输出：

```text
14 passed, 14 warnings in 0.10s
All checks passed!
```

警告为仓库既有的 Pydantic 弃用警告，不由本次改动引入。

## 修改文件

- `src/backend/src/agentclaw/community/core/common_config/whitelist_service.py`
- `src/backend/tests/community/core/common_config/test_common_whitelist_service.py`
- `.superpowers/sdd/task-1-report.md`

## 自审结果

- `git diff --check` 通过。
- 日志只包含 `business_code`、`param_code`、`env` 和值类型，不包含原始列表或非法原始值。
- 读取异常使用 `logger.exception` 并重新抛出，满足安全停止语义。
- 仅修改 brief 指定的两个责任文件及本报告；未接入沉寂扫描。

## 顾虑

- 聚焦测试存在 14 条既有 Pydantic 弃用警告；未影响测试结果，也不属于本任务范围。
- 未运行全量测试；本任务 brief 要求的聚焦测试与 Ruff 均已通过。
