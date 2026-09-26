"""Gateway 透明转发接口契约测试。

基于 Gateway 透明转发接口文档（Rule #1 ~ #17）及 WebSocket 协议规范（WS-1/1a/2），
验证响应 JSON 字段结构与类型是否与文档一致。

测试方式:
    - 后端接口（Rule #1~6, #10, #13, #15, #16）:
        FastAPI TestClient + DI mock，直接调用 handler
    - 外部服务接口（Rule #7, #8, #17）:
        responses 库 mock HTTP，验证 BCS/引擎/MCP Center 响应格式
    - WebSocket 协议（WS-1, WS-1a, WS-2）:
        数据模型序列化/反序列化 + MockWebSocket 握手流程
    - Schema 快照测试:
        Pydantic 响应模型 → JSON Schema 快照对比，检测模型字段变更
    - Mock 数据契约验证:
        BCS/Engine mock 数据 → JSON Schema 合规检查，避免同义反复
    - Service-backed API 契约:
        真实 Service + SQLite Repo seed → 完整 API response 快照，检测 service 返回漂移

断言工具（conftest.py）:
    assert_has_fields(data, fields, label, *, strict=False)
        fields 为 dict[str, type|tuple]，检查字段存在性+类型。
        strict=True 时，同时检查是否存在未声明的额外字段。
        类型示例: {"bot_id": str, "count": int, "items": list, "data": (dict, list, type(None))}

    assert_response_schema(resp, required_top, data_fields, ..., strict=False)
        参数同 assert_has_fields，验证 API 响应顶层结构和 data 字段。

    assert_no_extra_fields(data, allowed_fields, label)
        断言 data 中不包含 allowed_fields 之外的未知字段。

    assert_success(resp, label)
        断言 success=True。

    assert_api_response_contract(resp, snapshot_name, *, update=False)
        校验完整 API response envelope + data 的严格 JSON Schema 快照。

Schema 工具（schema_utils.py）:
    validate_model_against_snapshot(model_cls, name, *, update=False)
        Pydantic 模型 Schema 快照对比。

    validate_mock_against_schema(mock_data, schema, label)
        Mock 数据 JSON Schema 合规验证。

    load_contract_schema(name)
        加载 schema_snapshots/bcs/ 下的 BCS/Engine 契约 Schema。

运行:
    cd src/backend

    # 全部契约测试
    uv run pytest tests/contracts/gateway/ -v

    # Service-backed API 契约测试（含更新快照）
    uv run pytest tests/contracts/gateway/test_service_backed_api_contracts.py -v
    uv run pytest tests/contracts/gateway/test_service_backed_api_contracts.py -v --snapshot-update

    # Schema 快照测试（含更新快照）
    uv run pytest tests/contracts/gateway/test_schema_conformance.py -v
    uv run pytest tests/contracts/gateway/test_schema_conformance.py -v --snapshot-update
"""
