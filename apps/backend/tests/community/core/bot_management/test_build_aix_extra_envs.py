"""Unit tests for ``build_aix_extra_envs`` 的 model / runtime → RELAY_DEFAULT_* 注入。

覆盖：
- applicationCoding + 非空 model → 写 ``RELAY_DEFAULT_MODEL``（strip 后）。
- applicationCoding + 非空 runtime → 写 ``RELAY_DEFAULT_RUNTIME``（strip 后）。
- personalCoding + model / runtime → 写入（coding 模板同口径）。
- applicationCoding + 缺失 / 空 / 非字符串 model / runtime → 不写。
- model / runtime 与 BOT_TYPE / AIX_DEVFLOW_INFO / GIT_ADDRESSES 共存。
- template_config=None 时不写（只可能有 BOT_TYPE）。
"""
from __future__ import annotations

import json

from agentclaw.community.core.bot_management.utils import build_aix_extra_envs


class TestRelayDefaultModel:
    def test_application_coding_with_model(self):
        envs = build_aix_extra_envs({"model": "antchat/Ling-2.6-1T"}, template_type="applicationCoding")
        assert envs["RELAY_DEFAULT_MODEL"] == "antchat/Ling-2.6-1T"
        assert envs["BOT_TYPE"] == "application"

    def test_model_is_stripped(self):
        envs = build_aix_extra_envs({"model": "  m1  "}, template_type="applicationCoding")
        assert envs["RELAY_DEFAULT_MODEL"] == "m1"

    def test_personal_coding_injects_model(self):
        """personalCoding 与 applicationCoding 同口径写 RELAY_DEFAULT_MODEL。"""
        envs = build_aix_extra_envs({"model": "antchat/x"}, template_type="personalCoding")
        assert envs["RELAY_DEFAULT_MODEL"] == "antchat/x"
        assert envs["BOT_TYPE"] == "personal"

    def test_application_coding_without_model(self):
        envs = build_aix_extra_envs({"devflow_workflow": "d.yaml"}, template_type="applicationCoding")
        assert "RELAY_DEFAULT_MODEL" not in envs

    def test_empty_model_not_written(self):
        envs = build_aix_extra_envs({"model": "   "}, template_type="applicationCoding")
        assert "RELAY_DEFAULT_MODEL" not in envs

    def test_non_string_model_not_written(self):
        envs = build_aix_extra_envs({"model": 123}, template_type="applicationCoding")
        assert "RELAY_DEFAULT_MODEL" not in envs

    def test_model_coexists_with_devflow_and_git(self):
        cfg = {
            "model": "m1",
            "devflow_workflow": "devflow/app.yaml",
            "backend_repo": [{"repo_url": "git@x/y.git"}],
        }
        envs = build_aix_extra_envs(cfg, template_type="applicationCoding")
        assert envs["RELAY_DEFAULT_MODEL"] == "m1"
        assert envs["BOT_TYPE"] == "application"
        assert envs["AIX_DEVFLOW_INFO"] == "devflow/app.yaml"
        assert json.loads(envs["GIT_ADDRESSES"]) == ["git@x/y.git"]

    def test_none_template_config_no_model(self):
        envs = build_aix_extra_envs(None, template_type="applicationCoding")
        # template_config 为 None → 只可能 BOT_TYPE，绝不会有 RELAY_DEFAULT_MODEL
        assert envs == {"BOT_TYPE": "application"}


class TestRelayDefaultRuntime:
    def test_application_coding_with_runtime(self):
        envs = build_aix_extra_envs({"runtime": "python"}, template_type="applicationCoding")
        assert envs["RELAY_DEFAULT_RUNTIME"] == "python"
        assert envs["BOT_TYPE"] == "application"

    def test_runtime_is_stripped(self):
        envs = build_aix_extra_envs({"runtime": "  py  "}, template_type="applicationCoding")
        assert envs["RELAY_DEFAULT_RUNTIME"] == "py"

    def test_personal_coding_injects_runtime(self):
        """personalCoding 与 applicationCoding 同口径写 RELAY_DEFAULT_RUNTIME。"""
        envs = build_aix_extra_envs({"runtime": "py"}, template_type="personalCoding")
        assert envs["RELAY_DEFAULT_RUNTIME"] == "py"
        assert envs["BOT_TYPE"] == "personal"

    def test_application_coding_without_runtime(self):
        envs = build_aix_extra_envs({"devflow_workflow": "d.yaml"}, template_type="applicationCoding")
        assert "RELAY_DEFAULT_RUNTIME" not in envs

    def test_empty_runtime_not_written(self):
        envs = build_aix_extra_envs({"runtime": "   "}, template_type="applicationCoding")
        assert "RELAY_DEFAULT_RUNTIME" not in envs

    def test_non_string_runtime_not_written(self):
        envs = build_aix_extra_envs({"runtime": ["py"]}, template_type="applicationCoding")
        assert "RELAY_DEFAULT_RUNTIME" not in envs

    def test_runtime_coexists_with_model_and_others(self):
        cfg = {
            "model": "m1",
            "runtime": "py",
            "devflow_workflow": "devflow/app.yaml",
            "backend_repo": [{"repo_url": "git@x/y.git"}],
        }
        envs = build_aix_extra_envs(cfg, template_type="applicationCoding")
        assert envs["RELAY_DEFAULT_MODEL"] == "m1"
        assert envs["RELAY_DEFAULT_RUNTIME"] == "py"
        assert envs["BOT_TYPE"] == "application"
        assert envs["AIX_DEVFLOW_INFO"] == "devflow/app.yaml"
        assert json.loads(envs["GIT_ADDRESSES"]) == ["git@x/y.git"]

    def test_none_template_config_no_runtime(self):
        envs = build_aix_extra_envs(None, template_type="applicationCoding")
        assert envs == {"BOT_TYPE": "application"}


class TestDevflowWorkflows:
    """``AIX_DEVFLOW_INFO``（兼容首项单值）+ ``AIX_DEVFLOW_INFO_LIST``（全量 JSON 数组）—— RFC 220 UQ-16a。"""

    def test_plural_list_emits_single_value_and_list(self):
        cfg = {"devflow_workflows": ["workflows/a/workflow.yaml", "workflows/b/workflow.yaml"]}
        envs = build_aix_extra_envs(cfg, template_type="applicationCoding")
        assert envs["AIX_DEVFLOW_INFO"] == "workflows/a/workflow.yaml"
        assert json.loads(envs["AIX_DEVFLOW_INFO_LIST"]) == [
            "workflows/a/workflow.yaml",
            "workflows/b/workflow.yaml",
        ]

    def test_plural_dict_items(self):
        """前端把每条选择持久化成 ``{"path": ...}``；要抽 path 而非整 dict 透传。"""
        cfg = {
            "devflow_workflows": [
                {"path": "workflows/a/workflow.yaml"},
                {"path": "workflows/b/workflow.yaml"},
            ]
        }
        envs = build_aix_extra_envs(cfg, template_type="applicationCoding")
        assert envs["AIX_DEVFLOW_INFO"] == "workflows/a/workflow.yaml"
        assert json.loads(envs["AIX_DEVFLOW_INFO_LIST"]) == [
            "workflows/a/workflow.yaml",
            "workflows/b/workflow.yaml",
        ]

    def test_plural_dedupes_preserving_order(self):
        cfg = {
            "devflow_workflows": [
                "workflows/a/workflow.yaml",
                "workflows/b/workflow.yaml",
                "  workflows/a/workflow.yaml  ",
            ]
        }
        envs = build_aix_extra_envs(cfg, template_type="applicationCoding")
        assert json.loads(envs["AIX_DEVFLOW_INFO_LIST"]) == [
            "workflows/a/workflow.yaml",
            "workflows/b/workflow.yaml",
        ]

    def test_plural_single_selection_still_emits_list(self):
        cfg = {"devflow_workflows": ["workflows/a/workflow.yaml"]}
        envs = build_aix_extra_envs(cfg, template_type="applicationCoding")
        assert envs["AIX_DEVFLOW_INFO"] == "workflows/a/workflow.yaml"
        assert json.loads(envs["AIX_DEVFLOW_INFO_LIST"]) == ["workflows/a/workflow.yaml"]

    def test_singular_string_falls_back(self):
        """无复数 key → 旧单数字符串仍可用；list 带这一条。"""
        cfg = {"devflow_workflow": "devflow/app.yaml"}
        envs = build_aix_extra_envs(cfg, template_type="applicationCoding")
        assert envs["AIX_DEVFLOW_INFO"] == "devflow/app.yaml"
        assert json.loads(envs["AIX_DEVFLOW_INFO_LIST"]) == ["devflow/app.yaml"]

    def test_singular_dict_extracts_path(self):
        cfg = {"devflow_workflow": {"path": "devflow/v2/app.yaml"}}
        envs = build_aix_extra_envs(cfg, template_type="applicationCoding")
        assert envs["AIX_DEVFLOW_INFO"] == "devflow/v2/app.yaml"
        assert json.loads(envs["AIX_DEVFLOW_INFO_LIST"]) == ["devflow/v2/app.yaml"]

    def test_plural_wins_over_singular(self):
        """前端双写（复数全量 + 单数首项）→ 取复数，单数忽略不重复。"""
        cfg = {
            "devflow_workflows": ["workflows/a/workflow.yaml", "workflows/b/workflow.yaml"],
            "devflow_workflow": "workflows/a/workflow.yaml",
        }
        envs = build_aix_extra_envs(cfg, template_type="applicationCoding")
        assert json.loads(envs["AIX_DEVFLOW_INFO_LIST"]) == [
            "workflows/a/workflow.yaml",
            "workflows/b/workflow.yaml",
        ]

    def test_empty_selection_emits_nothing(self):
        envs = build_aix_extra_envs({"devflow_workflows": []}, template_type="applicationCoding")
        assert "AIX_DEVFLOW_INFO" not in envs
        assert "AIX_DEVFLOW_INFO_LIST" not in envs

    def test_no_devflow_keys_emits_nothing(self):
        envs = build_aix_extra_envs({"model": "m1"}, template_type="applicationCoding")
        assert "AIX_DEVFLOW_INFO" not in envs
        assert "AIX_DEVFLOW_INFO_LIST" not in envs

    def test_personal_coding_multiselect(self):
        """personalCoding 与 applicationCoding 同口径享多选契约。"""
        cfg = {"devflow_workflows": ["workflows/a/workflow.yaml", "workflows/b/workflow.yaml"]}
        envs = build_aix_extra_envs(cfg, template_type="personalCoding")
        assert envs["BOT_TYPE"] == "personal"
        assert json.loads(envs["AIX_DEVFLOW_INFO_LIST"]) == [
            "workflows/a/workflow.yaml",
            "workflows/b/workflow.yaml",
        ]

    def test_multiselect_coexists_with_git_and_model(self):
        cfg = {
            "devflow_workflows": ["workflows/a/workflow.yaml", "workflows/b/workflow.yaml"],
            "backend_repo": [{"repo_url": "git@x/y.git"}],
            "model": "m1",
        }
        envs = build_aix_extra_envs(cfg, template_type="applicationCoding")
        assert envs["AIX_DEVFLOW_INFO"] == "workflows/a/workflow.yaml"
        assert json.loads(envs["AIX_DEVFLOW_INFO_LIST"]) == [
            "workflows/a/workflow.yaml",
            "workflows/b/workflow.yaml",
        ]
        assert json.loads(envs["GIT_ADDRESSES"]) == ["git@x/y.git"]
        assert envs["RELAY_DEFAULT_MODEL"] == "m1"
