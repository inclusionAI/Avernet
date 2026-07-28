"""End-to-end tests verifying engine_type is threaded through SkillSetService
to get_default_mcp_server_codes, so different engines load their own default lists.

Covers the critical path:
    collect_bot_active_mcps(engine_type='aicoding')
        -> get_set_mcp_servers(engine_type='aicoding')
        -> get_default_mcp_server_codes('aicoding')
        -> returns aicoding list (10 servers)

    collect_bot_active_mcps(engine_type='openclaw')
        -> get_set_mcp_server_codes('openclaw')
        -> returns openclaw list

Same coverage for collect_bot_mcps.
"""
from unittest.mock import MagicMock

import pytest


BCS_MCP_SERVER_CODE = "mcp.ant.agentclawscs.bcs_mcp"


class TestMcpDefaultsPerEngineEndToEnd:
    """Verify engine_type results in correct default MCP list end-to-end."""

    @pytest.fixture
    def mock_skill_set_repo(self):
        repo = MagicMock()
        # Default skill set with no custom MCPs configured in DB
        repo.get_all_active_skill_sets.return_value = [
            {"id": 1, "name": "default", "is_default": True, "bolt_id": "b1", "is_active": True},
        ]
        repo.list_all.return_value = [
            {"id": 1, "name": "default", "is_default": True, "bolt_id": "b1", "is_active": True},
        ]
        repo.get_mcp_servers_in_set.return_value = []  # No custom MCPs in DB
        repo.get_excluded_mcps.return_value = []
        repo.get_all_excluded_mcps.return_value = []
        repo.get_default.return_value = {"id": 1, "name": "default", "is_default": True}
        return repo

    @pytest.fixture
    def mock_skill_repo(self):
        return MagicMock()

    def _make_service(self, engine_type, mock_skill_set_repo, mock_skill_repo, tmp_path):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService

        service = SkillSetService(
            skill_repo=mock_skill_repo,
            skill_set_repo=mock_skill_set_repo,
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            skills_dir=tmp_path / "skills",
            repo_dir=tmp_path / "skills-repo",
            local_dir=tmp_path / "skills-local",
            engine_type=engine_type,
            bot_repo=MagicMock(),

            path_factory=MagicMock(),
        )
        return service

    # ------------------------------------------------------------------
    # collect_bot_active_mcps
    # ------------------------------------------------------------------

    def test_collect_bot_active_mcps_aicoding_returns_aicoding_defaults(
        self, mock_skill_set_repo, mock_skill_repo, tmp_path
    ):
        """aicoding engine must load its own 10-server default list."""
        service = self._make_service(
            "aicoding", mock_skill_set_repo, mock_skill_repo, tmp_path
        )
        mcps = service.collect_bot_active_mcps(
            entity_id="u1", bot_id="b1", user_id="u1", engine_type="aicoding"
        )
        codes = [m["server_code"] for m in mcps]

        # Must contain aicoding-specific servers
        assert "mcp.ant.arkai.assistantmcpserver" in codes
        assert "mcp.ant.arkai.dimamcpserver" in codes
        assert BCS_MCP_SERVER_CODE in codes
        # Trimmed servers must no longer be present
        assert "mcp.ant.secaibase.secknowledgemcpserver" not in codes
        assert "mcp.ant.antcodemcp.code.mcpserver" not in codes
        # Must NOT contain openclaw-only servers
        assert "mcp.ant.antprocessai.anttaskmcp" not in codes
        assert "mcp.ant.homistudio.meetmcp" not in codes
        assert "hitl" in codes
        # Count: 7 aicoding servers incl. AixCodingMemoryMCP + BCS MCP + hitl
        assert len(codes) == 10

    def test_collect_bot_active_mcps_openclaw_returns_openclaw_defaults(
        self, mock_skill_set_repo, mock_skill_repo, tmp_path
    ):
        """openclaw engine must load its own default list."""
        service = self._make_service(
            "openclaw", mock_skill_set_repo, mock_skill_repo, tmp_path
        )
        mcps = service.collect_bot_active_mcps(
            entity_id="u1", bot_id="b1", user_id="u1", engine_type="openclaw"
        )
        codes = [m["server_code"] for m in mcps]

        # Must contain openclaw-specific servers
        assert "mcp.ant.antprocessai.anttaskmcp" in codes
        assert "mcp.ant.homistudio.meetmcp" in codes
        assert "mcp.ant.brwithub.worksummaryserver" in codes
        assert BCS_MCP_SERVER_CODE in codes
        assert "hitl" in codes
        # Must NOT contain aicoding-only servers
        assert "mcp.ant.secaibase.secknowledgemcpserver" not in codes
        assert len(codes) == 12

    def test_collect_bot_active_mcps_moltis_returns_empty_defaults(
        self, mock_skill_set_repo, mock_skill_repo, tmp_path
    ):
        """moltis engine has no default MCPs (empty list)."""
        service = self._make_service(
            "moltis", mock_skill_set_repo, mock_skill_repo, tmp_path
        )
        mcps = service.collect_bot_active_mcps(
            entity_id="u1", bot_id="b1", user_id="u1", engine_type="moltis"
        )
        # moltis default list is empty
        assert mcps == []

    def test_collect_bot_active_mcps_engine_type_overrides_service_default(
        self, mock_skill_set_repo, mock_skill_repo, tmp_path
    ):
        """When service is openclaw but call uses aicoding, must use aicoding list."""
        # Service initialized as openclaw
        service = self._make_service(
            "openclaw", mock_skill_set_repo, mock_skill_repo, tmp_path
        )
        # But call requests aicoding
        mcps = service.collect_bot_active_mcps(
            entity_id="u1", bot_id="b1", user_id="u1", engine_type="aicoding"
        )
        codes = [m["server_code"] for m in mcps]

        # Must return aicoding list, NOT openclaw
        assert "mcp.ant.arkai.assistantmcpserver" in codes
        assert "mcp.ant.antprocessai.anttaskmcp" not in codes
        assert BCS_MCP_SERVER_CODE in codes
        assert "hitl" in codes

    # ------------------------------------------------------------------
    # collect_bot_mcps
    # ------------------------------------------------------------------

    def test_collect_bot_mcps_aicoding_returns_aicoding_defaults(
        self, mock_skill_set_repo, mock_skill_repo, tmp_path
    ):
        """collect_bot_mcps with aicoding must load aicoding defaults."""
        service = self._make_service(
            "aicoding", mock_skill_set_repo, mock_skill_repo, tmp_path
        )
        mcps = service.collect_bot_mcps(
            entity_id="u1", bot_id="b1", user_id="u1", engine_type="aicoding"
        )
        codes = [m["server_code"] for m in mcps]

        assert "mcp.ant.arkai.assistantmcpserver" in codes
        assert "mcp.ant.arkai.dimamcpserver" in codes
        assert BCS_MCP_SERVER_CODE in codes
        assert "mcp.ant.secaibase.secknowledgemcpserver" not in codes
        assert "mcp.ant.antcodemcp.code.mcpserver" not in codes
        assert "mcp.ant.antprocessai.anttaskmcp" not in codes
        assert "hitl" in codes
        assert "mcp.ant.faas.aixjiter.AixCodingMemoryMCP" in codes
        assert len(codes) == 10

    def test_collect_bot_mcps_openclaw_returns_openclaw_defaults(
        self, mock_skill_set_repo, mock_skill_repo, tmp_path
    ):
        """collect_bot_mcps with openclaw must load openclaw defaults."""
        service = self._make_service(
            "openclaw", mock_skill_set_repo, mock_skill_repo, tmp_path
        )
        mcps = service.collect_bot_mcps(
            entity_id="u1", bot_id="b1", user_id="u1", engine_type="openclaw"
        )
        codes = [m["server_code"] for m in mcps]

        assert "mcp.ant.antprocessai.anttaskmcp" in codes
        assert "mcp.ant.brwithub.worksummaryserver" in codes
        assert BCS_MCP_SERVER_CODE in codes
        assert "hitl" in codes
        assert len(codes) == 12

    # ------------------------------------------------------------------
    # get_set_mcp_servers — direct engine_type parameter
    # ------------------------------------------------------------------

    def test_get_set_mcp_servers_aicoding_with_default_skill_set(
        self, mock_skill_set_repo, mock_skill_repo, tmp_path
    ):
        """get_set_mcp_servers with aicoding engine_type on default skill set returns aicoding defaults."""
        service = self._make_service(
            "aicoding", mock_skill_set_repo, mock_skill_repo, tmp_path
        )
        # get_set_mcp_servers needs skill_set_repo.get_skill_set to return a default skill set
        mock_skill_set_repo.get_by_id.return_value = {
            "id": 1, "name": "default", "is_default": True
        }

        mcps = service.get_set_mcp_servers(
            skill_set_id="1", user_id="u1", bot_id="b1", engine_type="aicoding"
        )
        codes = [m["server_code"] for m in mcps]

        assert "mcp.ant.arkai.assistantmcpserver" in codes
        assert "mcp.ant.secaibase.secknowledgemcpserver" not in codes
        assert BCS_MCP_SERVER_CODE in codes
        assert "hitl" in codes
        assert "mcp.ant.faas.aixjiter.AixCodingMemoryMCP" in codes
        assert len(codes) == 10

    def test_get_set_mcp_servers_openclaw_with_default_skill_set(
        self, mock_skill_set_repo, mock_skill_repo, tmp_path
    ):
        """get_set_mcp_servers with openclaw engine_type on default skill set returns openclaw defaults."""
        service = self._make_service(
            "openclaw", mock_skill_set_repo, mock_skill_repo, tmp_path
        )
        mock_skill_set_repo.get_by_id.return_value = {
            "id": 1, "name": "default", "is_default": True
        }

        mcps = service.get_set_mcp_servers(
            skill_set_id="1", user_id="u1", bot_id="b1", engine_type="openclaw"
        )
        codes = [m["server_code"] for m in mcps]

        assert "mcp.ant.antprocessai.anttaskmcp" in codes
        assert BCS_MCP_SERVER_CODE in codes
        assert "hitl" in codes
        assert len(codes) == 12


class TestClaudeCodeDefaultMcpNames:
    """claude_code default MCPs declare name/description inline; the three merge
    branches surface them, falling back to the legacy mock name when absent.

    Icon is not shipped in community source, so we monkeypatch a declared icon
    onto one entry to exercise the icon-passthrough branch (lines 1662 / 1730).
    """

    @pytest.fixture
    def mock_skill_set_repo(self):
        repo = MagicMock()
        # No active/owned skill sets → collect_bot_*_mcps reaches its default-MCP
        # merge loop (where icon passthrough lives) instead of short-circuiting
        # via get_set_mcp_servers on a default set.
        repo.get_all_active_skill_sets.return_value = []
        repo.list_all.return_value = []
        repo.get_mcp_servers_in_set.return_value = []
        repo.get_excluded_mcps.return_value = []
        repo.get_all_excluded_mcps.return_value = []
        repo.get_by_id.return_value = {"id": 1, "name": "default", "is_default": True}
        repo.get_skill_set.return_value = {"id": 1, "name": "default", "is_default": True}
        repo.get_default.return_value = {"id": 1, "name": "default", "is_default": True}
        return repo

    def _make_service(self, engine_type, repo, tmp_path):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService

        return SkillSetService(
            skill_repo=MagicMock(),
            skill_set_repo=repo,
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            bot_repo=MagicMock(),
            path_factory=MagicMock(),
        )

    def test_named_default_mcp_shows_real_name_and_description(self, mock_skill_set_repo, tmp_path):
        """get_set_mcp_servers surfaces the declared name/description for claude_code."""
        service = self._make_service("claude_code", mock_skill_set_repo, tmp_path)
        mcps = service.get_set_mcp_servers(
            skill_set_id="1", user_id="u1", bot_id="b1", engine_type="claude_code"
        )
        by_code = {m["server_code"]: m for m in mcps}

        assert by_code["mcp.ant.antcodemcp.code.mcpserver"]["name"] == "AntCodeMCP"
        assert by_code["mcp.ant.antcodemcp.code.mcpserver"]["description"] == "AntCode提供的 MCP 服务"
        assert by_code["mcp.ant.faas.skylarkmcpserver.skylarkmcpserver"]["name"] == "语雀 MCP"

    def test_nameless_default_mcp_keeps_legacy_mock_name(self, mock_skill_set_repo, tmp_path):
        """hitl has no declared name → legacy code.split('.')[-1] mock fallback."""
        service = self._make_service("claude_code", mock_skill_set_repo, tmp_path)
        mcps = service.get_set_mcp_servers(
            skill_set_id="1", user_id="u1", bot_id="b1", engine_type="claude_code"
        )
        by_code = {m["server_code"]: m for m in mcps}

        assert by_code["hitl"]["name"] == "hitl"
        assert by_code["hitl"]["description"] == "默认 MCP"

    def test_collect_active_surfaces_real_name_and_icon_passthrough(
        self, mock_skill_set_repo, tmp_path, monkeypatch
    ):
        """collect_bot_active_mcps surfaces declared name; icon passthrough branch covered."""
        from agentclaw.community.core.mcp.services import _defaults

        original = _defaults.get_default_mcp_servers

        def patched(engine_type=None):
            servers = [dict(c) for c in original(engine_type)]
            for c in servers:
                if c["server_code"] == "mcp.ant.antcodemcp.code.mcpserver":
                    c["icon"] = "https://icon.example/antcode.png"
            return servers

        monkeypatch.setattr(_defaults, "get_default_mcp_servers", patched)
        # SkillSetService imported the name directly; patch its module reference too.
        import agentclaw.community.core.skill_center.services.skill_set_service as svc_mod
        monkeypatch.setattr(svc_mod, "get_default_mcp_servers", patched)

        service = self._make_service("claude_code", mock_skill_set_repo, tmp_path)
        mcps = service.collect_bot_active_mcps(
            entity_id="u1", bot_id="b1", user_id="u1", engine_type="claude_code"
        )
        by_code = {m["server_code"]: m for m in mcps}

        assert by_code["mcp.ant.antcodemcp.code.mcpserver"]["name"] == "AntCodeMCP"
        assert by_code["mcp.ant.antcodemcp.code.mcpserver"]["description"] == "AntCode提供的 MCP 服务"
        # icon passthrough branch (line 1662)
        assert by_code["mcp.ant.antcodemcp.code.mcpserver"]["icon"] == "https://icon.example/antcode.png"
        # nameless entry keeps legacy server_code fallback
        assert by_code["hitl"]["name"] == "hitl"
        assert by_code["hitl"]["description"] == "Default MCP"

    def test_collect_all_surfaces_real_name_and_icon_passthrough(
        self, mock_skill_set_repo, tmp_path, monkeypatch
    ):
        """collect_bot_mcps surfaces declared name; icon passthrough branch (line 1730)."""
        from agentclaw.community.core.mcp.services import _defaults

        original = _defaults.get_default_mcp_servers

        def patched(engine_type=None):
            servers = [dict(c) for c in original(engine_type)]
            for c in servers:
                if c["server_code"] == "mcp.ant.rgmcpserver.rgfastcheckmcpserver":
                    c["icon"] = "https://icon.example/xinghai.png"
            return servers

        monkeypatch.setattr(_defaults, "get_default_mcp_servers", patched)
        import agentclaw.community.core.skill_center.services.skill_set_service as svc_mod
        monkeypatch.setattr(svc_mod, "get_default_mcp_servers", patched)

        service = self._make_service("claude_code", mock_skill_set_repo, tmp_path)
        mcps = service.collect_bot_mcps(
            entity_id="u1", bot_id="b1", user_id="u1", engine_type="claude_code"
        )
        by_code = {m["server_code"]: m for m in mcps}

        assert by_code["mcp.ant.rgmcpserver.rgfastcheckmcpserver"]["name"] == "星海MCP服务"
        assert by_code["mcp.ant.rgmcpserver.rgfastcheckmcpserver"]["icon"] == "https://icon.example/xinghai.png"
        assert by_code["hitl"]["name"] == "hitl"
