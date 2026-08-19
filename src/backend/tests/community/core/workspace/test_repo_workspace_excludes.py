"""Unit tests for repo-derived workspace rsync excludes.

Covers ``parse_repo_workspace_excludes`` / ``_repo_dirname_from_url`` in
``agentclaw.community.core.workspace.engines``: code repositories declared in a
bot's ``template_config`` (``backend_repo`` / ``frontend_repo`` / ``lib_repo``,
plus template-factory aliases) must produce ``workspace/<repo>`` excludes so the
build rsync does not bake cloned repo working copies into the artifact.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.workspace.engines.aicoding import (
    _repo_dirname_from_url,
    _repo_workspace_excludes_for_bot as parse_repo_workspace_excludes,
)


@pytest.mark.unit
class TestRepoDirnameFromUrl:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://code.alipay.com/ASF-Service-Platform/aixcore", "aixcore"),
            ("https://code.alipay.com/ASF-Service-Platform/aixcore.git", "aixcore"),
            ("https://code.alipay.com/ASF-Service-Platform/aixcore/", "aixcore"),
            ("https://code.alipay.com/ASF/aixcore.git?branch=dev", "aixcore"),
            ("https://code.alipay.com/aixcore", "aixcore"),
            ("git@code.alipay.com:ASF-Service-Platform/aixcore.git", "aixcore"),
            ("git@code.alipay.com:ASF/shared-lib.git/", "shared-lib"),
            ("ssh://git@code.alipay.com/ASF/openclaw.git", "openclaw"),
            ("", ""),
            (None, ""),
            (123, ""),
        ],
    )
    def test_dirname(self, url, expected):
        assert _repo_dirname_from_url(url) == expected


@pytest.mark.unit
class TestParseRepoWorkspaceExcludes:
    def test_legacy_repo_keys_produce_workspace_excludes(self):
        bot = {
            "template_config": {
                "backend_repo": [
                    {"repo_url": "https://code.alipay.com/ASF-Service-Platform/aixcore"}
                ],
                "frontend_repo": [
                    {"repo_url": "https://code.alipay.com/ASF/web-frontend.git"}
                ],
                "lib_repo": [
                    {"repo_url": "git@code.alipay.com:ASF/shared-lib.git/"}
                ],
            }
        }
        assert parse_repo_workspace_excludes(bot) == [
            "workspace/aixcore",
            "workspace/web-frontend",
            "workspace/shared-lib",
        ]

    def test_dup_repo_across_keys_is_deduped(self):
        bot = {
            "template_config": {
                "backend_repo": [
                    {"repo_url": "https://code.alipay.com/ASF/aixcore"}
                ],
                "lib_repo": [
                    {"repo_url": "https://code.alipay.com/ASF/aixcore.git"}
                ],
            }
        }
        assert parse_repo_workspace_excludes(bot) == ["workspace/aixcore"]

    def test_ac_bots_ext_template_config_is_not_read(self):
        """bot["ext"] (ac_bots.ext) must NOT be used as a repo source — only
        bot["template_config"] (ac_templates.ext) is consulted."""
        bot = {
            "ext": {
                # ac_bots.ext may carry a stale template_config snapshot; ignore it.
                "template_config": {
                    "frontend_repo": [
                        {"repo_url": "https://code.alipay.com/ASF/should-be-ignored.git"}
                    ],
                }
            }
        }
        assert parse_repo_workspace_excludes(bot) == []

    def test_ac_bots_ext_ignored_when_template_config_present(self):
        """Even when both exist, only ac_templates.ext (bot["template_config"]) wins."""
        bot = {
            "template_config": {
                "backend_repo": [
                    {"repo_url": "https://code.alipay.com/ASF/top.git"}
                ],
            },
            "ext": {
                "template_config": {
                    "backend_repo": [
                        {"repo_url": "https://code.alipay.com/ASF/should-be-ignored.git"}
                    ],
                }
            },
        }
        assert parse_repo_workspace_excludes(bot) == ["workspace/top"]
    def test_template_factory_alias_keys_supported(self):
        bot = {
            "template_config": {
                "template_key": "app-factory",
                "repos": [
                    {"url": "https://code.alipay.com/ASF/aixcore.git"},
                    "https://code.alipay.com/ASF/extra.git",
                ],
            }
        }
        assert parse_repo_workspace_excludes(bot) == [
            "workspace/aixcore",
            "workspace/extra",
        ]

    @pytest.mark.parametrize(
        "bot",
        [
            None,
            {},
            {"ext": None},
            {"ext": {}},
            {"template_config": {}},
            {"template_config": {"backend_repo": []}},
            {"template_config": {"backend_repo": [{"repo_url": ""}]}},
        ],
    )
    def test_no_repos_returns_empty(self, bot):
        assert parse_repo_workspace_excludes(bot) == []



@pytest.mark.unit
class TestAixcoreEndToEnd:
    """用户给出的真实样本：
    backend_repo=[{"repo_url":"https://code.alipay.com/ASF-Service-Platform/aixcore"}]
    必须提取出 workspace/aixcore。"""

    SAMPLE = {
        "backend_repo": [
            {"repo_url": "https://code.alipay.com/ASF-Service-Platform/aixcore"}
        ]
    }

    def test_extract_repo_url_to_aixcore_dirname(self):
        # repo_url 形如 [{"repo_url": "..."}] -> URL -> 仓库名 aixcore
        from agentclaw.community.core.bot_management.utils import _extract_code_repo_urls
        urls = _extract_code_repo_urls(self.SAMPLE)
        assert urls == ["https://code.alipay.com/ASF-Service-Platform/aixcore"]
        assert _repo_dirname_from_url(urls[0]) == "aixcore"

    def test_bot_template_config_produces_aixcore_exclude(self):
        # bot["template_config"] == ac_templates.ext，端到端产出 workspace/aixcore
        exclude = parse_repo_workspace_excludes({"template_config": self.SAMPLE})
        assert exclude == ["workspace/aixcore"]

    def test_build_plan_includes_aixcore_exclude(self):
        from agentclaw.community.core.workspace.engines.aicoding import (
            AICodingSandboxProvider,
        )
        from agentclaw.community.di import config as cfg

        provider = AICodingSandboxProvider(workspace=cfg.WorkspaceConfig())
        plan = provider.get_build_plan(bot={"template_config": self.SAMPLE})
        assert "workspace/aixcore" in plan.rsync_excludes

    def test_ac_bots_ext_does_not_produce_exclude(self):
        # 反例：仓库声明放在 bot["ext"]（ac_bots.ext）不应被读取
        exclude = parse_repo_workspace_excludes({"ext": {"template_config": self.SAMPLE}})
        assert exclude == []


@pytest.mark.unit
class TestRepoExcludesFailSafe:
    """新增的 repo 排除逻辑出错时，绝不影响 get_build_plan 主链路。"""

    def test_build_plan_survives_repo_impl_error(self, monkeypatch):
        from agentclaw.community.core.workspace.engines import aicoding as mod
        from agentclaw.community.core.workspace.engines.aicoding import (
            AICodingSandboxProvider,
        )
        from agentclaw.community.di import config as cfg

        # 强制让 repo 排除逻辑抛错，模拟数据异常/导入失败等极端情况
        def _boom(_bot):
            raise RuntimeError("simulated repo-exclude failure")

        monkeypatch.setattr(mod, "_repo_workspace_excludes_for_bot", _boom)

        provider = AICodingSandboxProvider(workspace=cfg.WorkspaceConfig())
        bot = {"template_config": {"backend_repo": [
            {"repo_url": "https://code.alipay.com/ASF/aixcore.git"}
        ]}}

        # 不应抛错；仍返回有效 build plan
        plan = provider.get_build_plan(bot=bot)
        assert plan.engine_type == "aicoding"
        assert plan.rsync_excludes  # 默认 excludes 保留
        # repo 排除未生效（降级）
        assert "workspace/aixcore" not in plan.rsync_excludes

    def test_build_plan_survives_without_bot_when_impl_raises(self, monkeypatch):
        from agentclaw.community.core.workspace.engines import aicoding as mod
        from agentclaw.community.core.workspace.engines.aicoding import (
            AICodingSandboxProvider,
        )
        from agentclaw.community.di import config as cfg

        monkeypatch.setattr(mod, "_repo_workspace_excludes_for_bot",
                            lambda _bot: (_ for _ in ()).throw(ValueError("boom")))

        provider = AICodingSandboxProvider(workspace=cfg.WorkspaceConfig())
        # 即使无 bot 调用，rep 错误也不应冒泡
        plan = provider.get_build_plan()
        assert plan.source_root_name == ".aicoding"
