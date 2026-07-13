"""
Worker Domain Model Unit Tests

M0: 验证 Worker 模型可构造、字段合法、与 Schema 对齐。
"""

import pytest
from pydantic import ValidationError


class TestWorkerModelConstruction:
    """Worker 模型构造测试"""

    def test_worker_model_importable(self):
        """验证 Worker 模型可导入"""
        from src.domain.models.worker import Worker
        assert Worker is not None

    def test_worker_model_constructs_with_minimal_fields(self):
        """验证 Worker 可以用最小必填字段构造"""
        from src.domain.models.worker import Worker

        worker = Worker(
            id="wrk_test_001",
            type="bot",
            identity={
                "name": "Test Bot",
                "handle": "@test-bot"
            },
            responsibilities=["testing"],
            capabilities=[{"name": "test", "level": "intermediate"}],
            constraints=[],
            skills=[],
            resources=[],
            state={
                "availability": "available",
                "trust_level": "trusted"
            }
        )

        assert worker.id == "wrk_test_001"
        assert worker.type == "bot"
        assert worker.identity.name == "Test Bot"

    def test_worker_model_supports_human_type(self):
        """验证 Worker 支持 human 类型"""
        from src.domain.models.worker import Worker

        worker = Worker(
            id="wrk_human_001",
            type="human",
            identity={
                "name": "John Doe",
                "handle": "@john-doe"
            },
            responsibilities=["review"],
            capabilities=[{"name": "architecture", "level": "expert"}],
            constraints=[],
            skills=[],
            resources=[],
            state={
                "availability": "available",
                "trust_level": "trusted"
            }
        )

        assert worker.type == "human"

    def test_worker_model_supports_bot_type(self):
        """验证 Worker 支持 bot 类型"""
        from src.domain.models.worker import Worker

        worker = Worker(
            id="wrk_bot_001",
            type="bot",
            identity={
                "name": "Research Bot",
                "handle": "@research-bot"
            },
            responsibilities=["research"],
            capabilities=[{"name": "search", "level": "expert"}],
            constraints=[],
            skills=[],
            resources=[],
            state={
                "availability": "available",
                "trust_level": "trusted"
            }
        )

        assert worker.type == "bot"


class TestWorkerModelValidation:
    """Worker 模型校验测试"""

    def test_worker_id_must_match_pattern(self):
        """验证 worker_id 必须符合 wrk_ 前缀模式"""
        from src.domain.models.worker import Worker

        with pytest.raises(ValidationError):
            Worker(
                id="invalid_id",  # 缺少 wrk_ 前缀
                type="bot",
                identity={"name": "Test", "handle": "@test"},
                responsibilities=["test"],
                capabilities=[{"name": "test", "level": "expert"}],
                constraints=[],
                skills=[],
                resources=[],
                state={"availability": "available", "trust_level": "trusted"}
            )

    def test_worker_type_must_be_human_or_bot(self):
        """验证 worker_type 必须是 human 或 bot"""
        from src.domain.models.worker import Worker

        with pytest.raises(ValidationError):
            Worker(
                id="wrk_test",
                type="invalid_type",  # 非法类型
                identity={"name": "Test", "handle": "@test"},
                responsibilities=["test"],
                capabilities=[{"name": "test", "level": "expert"}],
                constraints=[],
                skills=[],
                resources=[],
                state={"availability": "available", "trust_level": "trusted"}
            )

    def test_worker_identity_name_required(self):
        """验证 identity.name 是必填字段"""
        from src.domain.models.worker import Worker

        with pytest.raises(ValidationError):
            Worker(
                id="wrk_test",
                type="bot",
                identity={"handle": "@test"},  # 缺少 name
                responsibilities=["test"],
                capabilities=[{"name": "test", "level": "expert"}],
                constraints=[],
                skills=[],
                resources=[],
                state={"availability": "available", "trust_level": "trusted"}
            )

    def test_worker_state_availability_must_be_valid(self):
        """验证 state.availability 必须是有效枚举值"""
        from src.domain.models.worker import Worker

        with pytest.raises(ValidationError):
            Worker(
                id="wrk_test",
                type="bot",
                identity={"name": "Test", "handle": "@test"},
                responsibilities=["test"],
                capabilities=[{"name": "test", "level": "expert"}],
                constraints=[],
                skills=[],
                resources=[],
                state={"availability": "invalid", "trust_level": "trusted"}
            )

    def test_worker_capability_level_must_be_valid(self):
        """验证 capability.level 必须是有效枚举值"""
        from src.domain.models.worker import Worker

        with pytest.raises(ValidationError):
            Worker(
                id="wrk_test",
                type="bot",
                identity={"name": "Test", "handle": "@test"},
                responsibilities=["test"],
                capabilities=[{"name": "test", "level": "invalid"}],
                constraints=[],
                skills=[],
                resources=[],
                state={"availability": "available", "trust_level": "trusted"}
            )


class TestWorkerModelSerialization:
    """Worker 模型序列化测试"""

    def test_worker_serializes_to_json(self):
        """验证 Worker 可以序列化为 JSON"""
        from src.domain.models.worker import Worker
        import json

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

        # 转换为 dict 再序列化（使用 mode='json' 确保 datetime 等类型可序列化）
        worker_dict = worker.model_dump(mode='json')
        json_str = json.dumps(worker_dict)

        assert "wrk_test" in json_str
        assert "bot" in json_str

    def test_worker_deserializes_from_dict(self):
        """验证 Worker 可以从 dict 反序列化"""
        from src.domain.models.worker import Worker

        data = {
            "id": "wrk_test",
            "type": "bot",
            "identity": {"name": "Test", "handle": "@test"},
            "responsibilities": ["test"],
            "capabilities": [{"name": "test", "level": "expert"}],
            "constraints": [],
            "skills": [],
            "resources": [],
            "state": {"availability": "available", "trust_level": "trusted"}
        }

        worker = Worker.model_validate(data)
        assert worker.id == "wrk_test"