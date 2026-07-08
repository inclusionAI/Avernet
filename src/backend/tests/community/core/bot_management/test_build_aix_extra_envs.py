"""Unit tests for ``build_aix_extra_envs`` 的 model / runtime → RELAY_DEFAULT_* 注入。

覆盖：
- applicationCoding + 非空 model → 写 ``RELAY_DEFAULT_MODEL``（strip 后）。
- applicationCoding + 非空 runtime → 写 ``RELAY_DEFAULT_RUNTIME``（strip 后）。
- personalCoding + model / runtime → **不写**（门控仅 applicationCoding）。
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

    def test_personal_coding_does_not_inject_model(self):
        """门控仅 applicationCoding：personalCoding 即使带 model 也不写 RELAY_DEFAULT_MODEL。"""
        envs = build_aix_extra_envs({"model": "antchat/x"}, template_type="personalCoding")
        assert "RELAY_DEFAULT_MODEL" not in envs
        assert envs == {"BOT_TYPE": "personal"}

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

    def test_personal_coding_does_not_inject_runtime(self):
        """门控仅 applicationCoding：personalCoding 即使带 runtime 也不写 RELAY_DEFAULT_RUNTIME。"""
        envs = build_aix_extra_envs({"runtime": "py"}, template_type="personalCoding")
        assert "RELAY_DEFAULT_RUNTIME" not in envs
        assert envs == {"BOT_TYPE": "personal"}

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
