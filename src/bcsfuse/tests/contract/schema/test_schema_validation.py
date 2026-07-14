"""
Schema Validation Tests

验证 Pydantic 模型与 JSON Schema 的对齐。

M0: 验证 fixture 数据符合 Schema。
"""

import json
import pytest
from pathlib import Path


# Schema 目录
SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas"


@pytest.fixture
def schema_store():
    """加载所有 Schema 到 store"""
    from src.infra.schema_loader import SchemaLoader
    loader = SchemaLoader(SCHEMA_DIR)
    return loader.store


@pytest.fixture
def fixtures_dir():
    """Fixtures 目录"""
    return Path(__file__).resolve().parents[3] / "pytest_skeleton" / "tests" / "fixtures"


class TestSchemaLoader:
    """Schema 加载器测试"""

    def test_schema_loader_importable(self):
        """验证 SchemaLoader 可导入"""
        from src.infra.schema_loader import SchemaLoader
        assert SchemaLoader is not None

    def test_schema_loader_loads_schemas(self, schema_store):
        """验证 SchemaLoader 可加载所有 Schema"""
        assert "Worker.json" in schema_store
        assert "TaskSpec.json" in schema_store
        assert "PlanDraft.json" in schema_store
        assert "TeamSpec.json" in schema_store
        assert "Workspace.json" in schema_store
        assert "ExecutionPacket.json" in schema_store

    def test_schema_loader_resolves_refs(self, schema_store):
        """验证 SchemaLoader 可解析 $ref"""
        # Worker.json 引用 SkillRef.json 和 ResourceRef.json
        worker_schema = schema_store["Worker.json"]
        assert worker_schema["title"] == "Worker"


class TestWorkerSchemaValidation:
    """Worker Schema 校验测试"""

    def test_sample_worker_matches_schema(self, fixtures_dir, schema_store):
        """验证 sample_worker.json 符合 Worker Schema"""
        from src.infra.schema_loader import validate_with_store

        sample_path = fixtures_dir / "sample_worker.json"
        if not sample_path.exists():
            pytest.skip("sample_worker.json not found in fixtures")

        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        validate_with_store(sample, "Worker.json", schema_store)

    def test_worker_pydantic_model_is_valid(self):
        """验证 Worker Pydantic 模型生成的对象符合 Schema"""
        from src.infra.schema_loader import validate_with_store
        from src.domain.models.worker import Worker

        worker = Worker(
            id="wrk_test",
            type="bot",
            identity={"name": "Test", "handle": "@test"},
            responsibilities=["test"],
            capabilities=[{"name": "test", "level": "expert"}],
            constraints=[],
            skills=[],
            resources=[],
            state={"availability": "available", "trust_level": "trusted"}
        )

        # Pydantic 模型转 dict 进行 Schema 校验
        # 注意：使用 mode='json' 确保枚举值被序列化为字符串
        # 并只保留 Schema 定义的字段
        schema_fields = {
            "id", "type", "identity", "responsibilities", "domains",
            "capabilities", "constraints", "skills", "resources",
            "memory_refs", "state", "performance_stats"
        }
        # state 只保留 Schema 定义的字段
        state_schema_fields = {"availability", "trust_level", "current_load", "last_seen_at"}

        worker_dict = worker.model_dump(mode='json', exclude_none=True)
        # 只保留 Schema 定义的字段
        filtered_dict = {k: v for k, v in worker_dict.items() if k in schema_fields}
        # 过滤 state 中的内部字段
        if "state" in filtered_dict and isinstance(filtered_dict["state"], dict):
            filtered_dict["state"] = {
                k: v for k, v in filtered_dict["state"].items()
                if k in state_schema_fields
            }
        validate_with_store(filtered_dict, "Worker.json")


class TestTaskSpecSchemaValidation:
    """TaskSpec Schema 校验测试"""

    def test_sample_task_spec_matches_schema(self, fixtures_dir, schema_store):
        """验证 sample_task_spec.json 符合 TaskSpec Schema"""
        from src.infra.schema_loader import validate_with_store

        sample_path = fixtures_dir / "sample_task_spec.json"
        if not sample_path.exists():
            pytest.skip("sample_task_spec.json not found in fixtures")

        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        validate_with_store(sample, "TaskSpec.json", schema_store)


class TestPlanDraftSchemaValidation:
    """PlanDraft Schema 校验测试"""

    def test_sample_plan_draft_matches_schema(self, fixtures_dir, schema_store):
        """验证 sample_plan_draft.json 符合 PlanDraft Schema"""
        from src.infra.schema_loader import validate_with_store

        sample_path = fixtures_dir / "sample_plan_draft.json"
        if not sample_path.exists():
            pytest.skip("sample_plan_draft.json not found in fixtures")

        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        validate_with_store(sample, "PlanDraft.json", schema_store)