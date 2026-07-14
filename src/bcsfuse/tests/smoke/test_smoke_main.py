"""
M0 Smoke Test
验证项目基础结构可导入、Schema 可加载、分层骨架完整。
"""

import pytest


class TestProjectStructure:
    """项目结构测试"""

    def test_pyproject_exists(self):
        """验证 pyproject.toml 存在"""
        from pathlib import Path

        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        assert pyproject_path.exists(), f"pyproject.toml not found: {pyproject_path}"


class TestLayerImports:
    """分层导入测试"""

    def test_domain_layer_importable(self):
        """验证 domain 层可导入"""
        from src import domain
        assert domain is not None

    def test_domain_models_importable(self):
        """验证领域模型可导入"""
        from src.domain import models
        assert models is not None

    def test_domain_exceptions_importable(self):
        """验证领域异常可导入"""
        from src.domain import exceptions
        assert exceptions is not None

    def test_application_layer_importable(self):
        """验证 application 层可导入"""
        from src import application
        assert application is not None

    def test_infra_layer_importable(self):
        """验证 infra 层可导入"""
        from src import infra
        assert infra is not None

    def test_interfaces_layer_importable(self):
        """验证 interfaces 层可导入"""
        from src import interfaces
        assert interfaces is not None

    def test_adapters_layer_importable(self):
        """验证 adapters 层可导入"""
        from src import adapters
        assert adapters is not None

    def test_openclaw_adapter_importable(self):
        """验证 openclaw adapter 可导入"""
        from src.adapters import openclaw
        assert openclaw is not None


class TestSchemaLoading:
    """Schema 加载测试"""

    def test_schemas_directory_exists(self):
        """验证 schemas 目录存在"""
        from pathlib import Path

        schema_dir = Path(__file__).resolve().parents[1] / "schemas"
        assert schema_dir.exists(), f"Schema directory not found: {schema_dir}"

    def test_worker_schema_loadable(self):
        """验证 Worker.json 可加载"""
        from pathlib import Path
        import json

        schema_dir = Path(__file__).resolve().parents[1] / "schemas"
        worker_schema_path = schema_dir / "Worker.json"
        assert worker_schema_path.exists(), f"Worker.json not found: {worker_schema_path}"

        schema = json.loads(worker_schema_path.read_text(encoding="utf-8"))
        assert schema["title"] == "Worker"

    def test_all_required_schemas_exist(self):
        """验证所有必需的 Schema 文件存在"""
        from pathlib import Path

        schema_dir = Path(__file__).resolve().parents[1] / "schemas"
        required_schemas = [
            "Worker.json",
            "TaskSpec.json",
            "PlanDraft.json",
            "CandidateBundle.json",
            "TeamSpec.json",
            "Workspace.json",
            "ExecutionPacket.json",
            "SkillRef.json",
            "ResourceRef.json",
            "KnowledgeItem.json",
            "ContextPack.json",
            "ResourcePack.json",
            "SkillPack.json",
            "Guardrails.json",
            "OutputContract.json",
        ]

        for schema_name in required_schemas:
            schema_path = schema_dir / schema_name
            assert schema_path.exists(), f"Required schema not found: {schema_name}"


class TestCoreModelImports:
    """核心模型导入测试"""

    def test_worker_model_importable(self):
        """验证 Worker 模型可导入"""
        from src.domain.models import Worker
        assert Worker is not None

    def test_task_spec_model_importable(self):
        """验证 TaskSpec 模型可导入"""
        from src.domain.models import TaskSpec
        assert TaskSpec is not None

    def test_plan_draft_model_importable(self):
        """验证 PlanDraft 模型可导入"""
        from src.domain.models import PlanDraft
        assert PlanDraft is not None

    def test_candidate_bundle_model_importable(self):
        """验证 CandidateBundle 模型可导入"""
        from src.domain.models import CandidateBundle
        assert CandidateBundle is not None

    def test_team_spec_model_importable(self):
        """验证 TeamSpec 模型可导入"""
        from src.domain.models import TeamSpec
        assert TeamSpec is not None

    def test_workspace_model_importable(self):
        """验证 Workspace 模型可导入"""
        from src.domain.models import Workspace
        assert Workspace is not None

    def test_execution_packet_model_importable(self):
        """验证 ExecutionPacket 模型可导入"""
        from src.domain.models import ExecutionPacket
        assert ExecutionPacket is not None