"""Unit tests for individual diagnostic classes."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from agentclaw.community.core.harness.diagnostics import register_all_diagnostics
from agentclaw.community.core.harness.diagnostics.base import DiagnosticContext
from agentclaw.community.core.harness.diagnostics.agents.safety_rules import AgentsSafetyRulesDiagnostic
from agentclaw.community.core.harness.diagnostics.agents.fail_first import AgentsFailFirstDiagnostic
from agentclaw.community.core.harness.diagnostics.agents.behavior_boundaries import AgentsBehaviorBoundariesDiagnostic
from agentclaw.community.core.harness.diagnostics.tools.declaration import ToolsDeclarationDiagnostic
from agentclaw.community.core.harness.diagnostics.tools.mcp_format import (
    ToolsMcpFormatDiagnostic,
    _BATCH_CHAR_BUDGET,
    _BATCH_MAX_MCPS,
    _compact_tool_for_prompt,
    _MAX_TOOLS_PER_MCP_IN_PROMPT,
    _pack_mcp_batches,
    _synthesize_batch_responses,
)
from agentclaw.community.core.harness.diagnostics.config.soul import SoulPersonaDiagnostic
from agentclaw.community.core.harness.models import Severity


class MockBotFileRef:
    def __init__(self, content: str, file_type: str):
        self.content = content
        self.file_type = file_type


class _MockMCPCenter:
    """Minimal MCPCenterPlugin test double: server_code -> detail dict."""

    def __init__(self, details: dict[str, dict]):
        self._details = details

    def get_mcp_detail(self, server_code: str):
        return self._details.get(server_code)


def _make_ctx(
    file_contents: dict[str, str] | None = None,
    activated_mcps: list[dict] | None = None,
) -> DiagnosticContext:
    """Create a DiagnosticContext with mocked LLM and BotProfile."""
    profile = AsyncMock()
    contents = file_contents or {}

    async def read_file(entity_type, entity_id, bot_id, file_type, operator_id="", publish_id=None):
        ref = MockBotFileRef(content=contents.get(file_type, ""), file_type=file_type)
        return ref

    profile.read_file = read_file
    # get_activated_mcps is a sync method
    profile.get_activated_mcps = MagicMock(
        return_value=activated_mcps if activated_mcps is not None else []
    )

    llm = AsyncMock()

    ctx = DiagnosticContext(
        llm=llm,
        bot_profile=profile,
        entity_type="staff",
        entity_id="user1",
        bot_id="bot1",
    )
    return ctx


class TestRegisterAllDiagnostics:
    """Test the diagnostic registry."""

    def test_returns_all_diagnostics(self):
        diags = register_all_diagnostics()
        assert len(diags) == 6
        ids = {d.id for d in diags}
        assert ids == {
            "D-AGENTS-001", "D-AGENTS-002", "D-AGENTS-003",
            "D-TOOLS-001", "D-TOOLS-002",
            "D-SOUL-001",
        }

    def test_each_diagnostic_is_concrete(self):
        for d in register_all_diagnostics():
            assert type(d).__abstractmethods__ == frozenset(), f"{d.id} is still abstract"
            assert d.id != "", f"{type(d).__name__}.id is empty"
            assert d.name != "", f"{type(d).__name__}.name is empty"
            assert d.severity in ("critical", "warning", "info")
            assert d.system_prompt != "", f"{type(d).__name__}.system_prompt is empty"


class TestAgentsSafetyRulesDiagnostic:
    """Test D-AGENTS-001: Safety Rules diagnostic."""

    @pytest.mark.asyncio
    async def test_empty_file_returns_no_findings(self):
        diag = AgentsSafetyRulesDiagnostic()
        ctx = _make_ctx({"AGENTS.md": ""})
        findings = await diag.analyze(ctx)
        assert findings == []

    @pytest.mark.asyncio
    async def test_llm_returns_issue_text(self):
        diag = AgentsSafetyRulesDiagnostic()
        ctx = _make_ctx({"AGENTS.md": "## Safety Rules\n\n注意安全"})
        ctx.llm.chat = AsyncMock(return_value="安全规则缺失\n缺少SR01-SR10核心安全规则，仅有模糊提示")
        findings = await diag.analyze(ctx)
        assert len(findings) == 1
        assert findings[0].rule_id == "D-AGENTS-001"
        assert findings[0].rule_name == "Safety Rules 诊断"
        assert findings[0].severity == Severity.CRITICAL
        assert findings[0].file_type == "AGENTS.md"
        assert "缺少" in findings[0].message

    @pytest.mark.asyncio
    async def test_llm_returns_no_issue(self):
        diag = AgentsSafetyRulesDiagnostic()
        ctx = _make_ctx({"AGENTS.md": "## Safety Rules\n\nSR01-SR10 all present"})
        ctx.llm.chat = AsyncMock(return_value="无问题")
        findings = await diag.analyze(ctx)
        assert findings == []

    @pytest.mark.asyncio
    async def test_llm_disabled_marker(self):
        diag = AgentsSafetyRulesDiagnostic()
        ctx = _make_ctx({"AGENTS.md": "内容"})
        ctx.llm.chat = AsyncMock(return_value="[llm disabled]")
        findings = await diag.analyze(ctx)
        assert len(findings) == 1
        assert findings[0].rule_id == "LLM01"


class TestAgentsFailFirstDiagnostic:
    """Test D-AGENTS-002: Failure-first principle diagnostic."""

    @pytest.mark.asyncio
    async def test_empty_file_returns_no_findings(self):
        diag = AgentsFailFirstDiagnostic()
        ctx = _make_ctx({"AGENTS.md": ""})
        findings = await diag.analyze(ctx)
        assert findings == []

    @pytest.mark.asyncio
    async def test_llm_returns_issue_text(self):
        diag = AgentsFailFirstDiagnostic()
        ctx = _make_ctx({"AGENTS.md": "## Configuration\n\nSome config"})
        ctx.llm.chat = AsyncMock(return_value="失败原则缺失\n缺少失败优先原则段落，Bot可能在工具失败时阻塞重试")
        findings = await diag.analyze(ctx)
        assert len(findings) == 1
        assert findings[0].rule_id == "D-AGENTS-002"
        assert findings[0].rule_name == "失败优先原则诊断"
        assert findings[0].severity == Severity.CRITICAL
        assert findings[0].file_type == "AGENTS.md"

    @pytest.mark.asyncio
    async def test_llm_returns_no_issue(self):
        diag = AgentsFailFirstDiagnostic()
        ctx = _make_ctx({"AGENTS.md": "## 失败优先原则\n\n完整覆盖"})
        ctx.llm.chat = AsyncMock(return_value="无问题")
        findings = await diag.analyze(ctx)
        assert findings == []


class TestAgentsBehaviorBoundariesDiagnostic:
    """Test D-AGENTS-003: Behavior boundaries diagnostic."""

    @pytest.mark.asyncio
    async def test_empty_file_returns_no_findings(self):
        diag = AgentsBehaviorBoundariesDiagnostic()
        ctx = _make_ctx({"AGENTS.md": ""})
        findings = await diag.analyze(ctx)
        assert findings == []

    @pytest.mark.asyncio
    async def test_llm_returns_issue_text(self):
        diag = AgentsBehaviorBoundariesDiagnostic()
        ctx = _make_ctx({"AGENTS.md": "## Behavior\n\nSome rules"})
        ctx.llm.chat = AsyncMock(return_value="行为边界缺失\n缺少Stop before you leap和Check before you start约束")
        findings = await diag.analyze(ctx)
        assert len(findings) == 1
        assert findings[0].rule_id == "D-AGENTS-003"
        assert findings[0].rule_name == "行为边界诊断"
        assert findings[0].severity == Severity.CRITICAL
        assert findings[0].file_type == "AGENTS.md"

    @pytest.mark.asyncio
    async def test_llm_returns_no_issue(self):
        diag = AgentsBehaviorBoundariesDiagnostic()
        ctx = _make_ctx({"AGENTS.md": "## 行为边界\n\n完整覆盖"})
        ctx.llm.chat = AsyncMock(return_value="无问题")
        findings = await diag.analyze(ctx)
        assert findings == []


class TestToolsDeclarationDiagnostic:
    """Test D-TOOLS-001: TOOLS.md calling conventions diagnostic."""

    @pytest.mark.asyncio
    async def test_empty_tools_returns_no_findings(self):
        diag = ToolsDeclarationDiagnostic()
        ctx = _make_ctx({"TOOLS.md": ""})
        findings = await diag.analyze(ctx)
        assert findings == []

    @pytest.mark.asyncio
    async def test_llm_returns_issue_text(self):
        diag = ToolsDeclarationDiagnostic()
        ctx = _make_ctx({"TOOLS.md": "# Tools\n\nSome tools listed"})
        ctx.llm.chat = AsyncMock(return_value="调用规范缺失\n缺少核心调用纪律和参数传递格式说明")
        findings = await diag.analyze(ctx)
        assert len(findings) == 1
        assert findings[0].rule_id == "D-TOOLS-001"
        assert findings[0].rule_name == "工具调用原则诊断"
        assert findings[0].severity == Severity.CRITICAL
        assert findings[0].file_type == "TOOLS.md"

    @pytest.mark.asyncio
    async def test_llm_returns_no_issue(self):
        diag = ToolsDeclarationDiagnostic()
        ctx = _make_ctx({"TOOLS.md": "# Tools\n\n完整的调用规范和参数格式"})
        ctx.llm.chat = AsyncMock(return_value="无问题")
        findings = await diag.analyze(ctx)
        assert findings == []

    @pytest.mark.asyncio
    async def test_prompt_does_not_include_mcp_list(self):
        """D-TOOLS-001 should not include MCP list in prompt (that's D-TOOLS-002's job)."""
        diag = ToolsDeclarationDiagnostic()
        mcps = [{"server_code": "skylarkmcpserver", "name": "skylark"}]
        ctx = _make_ctx({"TOOLS.md": "# Tools\n\n- some tools"}, activated_mcps=mcps)

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "无问题"

        ctx.llm.chat = capture_chat
        await diag.analyze(ctx)

        assert "TOOLS.md 调用规范诊断" in captured_user
        assert "已激活的 MCP 列表" not in captured_user
        assert "skylarkmcpserver" not in captured_user


class TestToolsMcpFormatDiagnostic:
    """Test D-TOOLS-002: MCP calling standards diagnostic."""

    @pytest.mark.asyncio
    async def test_empty_tools_returns_no_findings(self):
        diag = ToolsMcpFormatDiagnostic()
        ctx = _make_ctx({"TOOLS.md": ""})
        findings = await diag.analyze(ctx)
        assert findings == []

    @pytest.mark.asyncio
    async def test_includes_enriched_mcp_details_in_prompt(self, monkeypatch):
        diag = ToolsMcpFormatDiagnostic()
        mcps = [
            {"server_code": "skylarkmcpserver", "name": "skylark"},
            {"server_code": "pydataphin", "name": "dataphin"},
        ]
        ctx = _make_ctx({"TOOLS.md": "# Tools\n\n- some tools"}, activated_mcps=mcps)

        # Mock get_mcp_center_plugin to return enriched details
        mock_center = MagicMock()
        mock_center.get_mcp_detail.side_effect = lambda code: {
            "skylarkmcpserver": {
                "name": "语雀MCP",
                "description": "语雀知识库文档管理",
                "tools": [{"name": "skylark_doc_create"}, {"name": "skylark_doc_detail"}],
            },
            "pydataphin": {
                "name": "DataPhin MCP",
                "description": "数据资产管理",
                "tools": [{"name": "query_asset"}],
            },
        }.get(code)
        ctx.mcp_center = mock_center

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "无问题"

        ctx.llm.chat = capture_chat
        await diag.analyze(ctx)

        assert "TOOLS.md MCP 调用规范诊断" in captured_user
        assert "语雀MCP" in captured_user
        assert "语雀知识库文档管理" in captured_user
        # Unreachable MCPs (no verified_guide, no inputSchema): tools list is
        # stripped; only server_code/name/description are sent.
        assert "skylark_doc_create" not in captured_user
        assert "DataPhin MCP" in captured_user
        assert "query_asset" not in captured_user
        # Section header should indicate the unreachable (no-schema) bucket.
        assert "无 schema MCP" in captured_user

    @pytest.mark.asyncio
    async def test_verified_guide_attached_for_known_mcp(self, monkeypatch):
        """MCPs in _VERIFIED_MCP_GUIDES should have verified_guide content in prompt."""
        diag = ToolsMcpFormatDiagnostic()
        mcps = [{"server_code": "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver", "name": "skylark"}]
        ctx = _make_ctx({"TOOLS.md": "# Tools\n\n- some tools"}, activated_mcps=mcps)

        mock_center = MagicMock()
        mock_center.get_mcp_detail.return_value = {
            "name": "语雀MCP",
            "description": "语雀知识库文档管理",
            "tools": [{"name": "skylark_doc_create"}],
        }
        ctx.mcp_center = mock_center

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "无问题"

        ctx.llm.chat = capture_chat
        await diag.analyze(ctx)

        assert "verified_guide" in captured_user
        assert "skylark_search" in captured_user  # Content from the verified guide file

    @pytest.mark.asyncio
    async def test_no_verified_guide_for_unknown_mcp(self, monkeypatch):
        """MCPs NOT in _VERIFIED_MCP_GUIDES (and without inputSchema) land in the
        unreachable bucket: rendered as a mapping-table-only text block carrying
        the 平台补全中 note, with no verified_guide content."""
        diag = ToolsMcpFormatDiagnostic()
        mcps = [{"server_code": "some-unknown-mcp", "name": "Unknown"}]
        ctx = _make_ctx({"TOOLS.md": "# Tools\n\n- some tools"}, activated_mcps=mcps)

        mock_center = MagicMock()
        mock_center.get_mcp_detail.return_value = {
            "name": "Unknown MCP",
            "description": "Some service",
            "tools": [{"name": "do_stuff"}],
        }
        ctx.mcp_center = mock_center

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "无问题"

        ctx.llm.chat = capture_chat
        await diag.analyze(ctx)

        # No verified_guide anywhere (the MCP is unknown), and it lands in the
        # unreachable (no-schema) bucket as a text block, not a JSON dump.
        assert "verified_guide:" not in captured_user
        assert "无 schema MCP" in captured_user
        assert "some-unknown-mcp" in captured_user
        assert "平台补全中" in captured_user

    @pytest.mark.asyncio
    async def test_no_activated_mcps_still_works(self):
        diag = ToolsMcpFormatDiagnostic()
        ctx = _make_ctx({"TOOLS.md": "# Tools\n\n- some tools"}, activated_mcps=[])

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "无问题"

        ctx.llm.chat = capture_chat
        findings = await diag.analyze(ctx)
        assert findings == []
        # Should not include MCP list section when empty
        assert "已激活的 MCP 列表" not in (captured_user or "")

    @pytest.mark.asyncio
    async def test_get_activated_mcps_failure_ignored(self):
        diag = ToolsMcpFormatDiagnostic()
        ctx = _make_ctx({"TOOLS.md": "# Tools"})
        ctx.bot_profile.get_activated_mcps = MagicMock(side_effect=RuntimeError("MCP service down"))
        ctx.llm.chat = AsyncMock(return_value="无问题")
        findings = await diag.analyze(ctx)
        assert findings == []

    @pytest.mark.asyncio
    async def test_llm_returns_issue_text(self, monkeypatch):
        diag = ToolsMcpFormatDiagnostic()
        mcps = [{"server_code": "skylarkmcpserver", "name": "skylark"}]
        ctx = _make_ctx({"TOOLS.md": "# Tools\n\n- some tool"}, activated_mcps=mcps)

        mock_center = MagicMock()
        mock_center.get_mcp_detail.return_value = {
            "name": "语雀MCP",
            "description": "语雀知识库文档管理",
            "tools": [{"name": "skylark_doc_create"}],
        }
        ctx.mcp_center = mock_center

        ctx.llm.chat = AsyncMock(return_value="MCP规范缺失\n缺少语雀MCP的调用规范和场景映射 [SCORE:40]")
        findings = await diag.analyze(ctx)
        assert len(findings) == 1
        assert findings[0].rule_id == "D-TOOLS-002"
        assert findings[0].rule_name == "各项 MCP 调用规范诊断"
        # No verified_guide and no inputSchema ⇒ advisory (platform must author):
        # template dropped (no TODO placeholder patch), bumped to pass/info so
        # health_score isn't stuck, message annotated with the recovery note.
        assert findings[0].suggested_template_ids == []
        assert findings[0].severity == Severity.INFO
        assert findings[0].score >= 80
        assert findings[0].result == "pass"
        assert "平台补全中" in findings[0].message
        assert findings[0].file_type == "TOOLS.md"

    @pytest.mark.asyncio
    async def test_mcp_detail_failure_falls_back_gracefully(self, monkeypatch):
        """When get_mcp_detail fails, enriched data falls back to original mcp dict fields."""
        diag = ToolsMcpFormatDiagnostic()
        mcps = [{"server_code": "failing-mcp", "name": "Fallback Name"}]
        ctx = _make_ctx({"TOOLS.md": "# Tools"}, activated_mcps=mcps)

        mock_center = MagicMock()
        mock_center.get_mcp_detail.side_effect = RuntimeError("MCP Center down")
        ctx.mcp_center = mock_center

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "无问题"

        ctx.llm.chat = capture_chat
        await diag.analyze(ctx)

        # Fallback name should still appear in the prompt
        assert "Fallback Name" in captured_user
        assert "failing-mcp" in captured_user

    @pytest.mark.asyncio
    async def test_mcp_without_server_code_skipped(self, monkeypatch):
        """MCPs without server_code are skipped (empty server_code filtered out)."""
        diag = ToolsMcpFormatDiagnostic()
        mcps = [
            {"name": "no-server-code-mcp"},
            {"mcp_server_name": "another-mcp"},
        ]
        ctx = _make_ctx({"TOOLS.md": "# Tools"}, activated_mcps=mcps)

        mock_center = MagicMock()
        ctx.mcp_center = mock_center

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "无问题"

        ctx.llm.chat = capture_chat
        await diag.analyze(ctx)

        # Should not include MCP list since no server_code entries pass the filter
        assert "已激活的 MCP 列表" not in (captured_user or "")
        # get_mcp_detail should never be called
        mock_center.get_mcp_detail.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_mcp_center_plugin_failure_ignored(self):
        """When mcp_center is unavailable, the diagnostic falls back to basic names."""
        diag = ToolsMcpFormatDiagnostic()
        mcps = [{"server_code": "some-mcp", "name": "Some MCP"}]
        ctx = _make_ctx({"TOOLS.md": "# Tools"}, activated_mcps=mcps)

        ctx.mcp_center = None  # impl null-guards -> fallback to basic names

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "无问题"

        ctx.llm.chat = capture_chat
        # Should not raise — falls back to basic MCP names
        findings = await diag.analyze(ctx)
        assert findings == []
        # Fallback should include the MCP name even without enrichment
        assert "Some MCP" in captured_user

    @pytest.mark.asyncio
    async def test_kb_context_included_in_prompt(self, monkeypatch):
        """When KB returns results, they should appear in the LLM prompt."""
        diag = ToolsMcpFormatDiagnostic()
        ctx = _make_ctx({"TOOLS.md": "## Tools\n\nSome tools content"})

        monkeypatch.setattr(
            "agentclaw.community.core.harness.diagnostics.tools.mcp_format._query_knowledge_base",
            lambda question, kb_config=None: [
                {"q": "CCT", "a": "query:CCT\nanswer:大安全风险仿真与事件分析平台", "rerankScore": 0.99},
                {"q": "Skylark", "a": "query:Skylark\nanswer:语雀文档知识库服务", "rerankScore": 0.95},
            ],
        )

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "无问题"

        ctx.llm.chat = capture_chat

        # Provide MCP with description so KB query is triggered
        monkeypatch.setattr(
            ctx.bot_profile,
            "get_activated_mcps",
            lambda **kw: [{"server_code": "mcp.test.cct", "name": "CCT MCP", "description": "CCT事件分析"}],
        )
        ctx.mcp_center = _MockMCPCenter({"mcp.test.cct": {"name": "CCT MCP", "description": "CCT事件分析", "tools": []}})

        findings = await diag.analyze(ctx)
        assert findings == []
        assert "--- 内网知识库参考（MCP 相关术语与服务说明） ---" in captured_user
        assert "**CCT**: 大安全风险仿真与事件分析平台" in captured_user
        assert "**Skylark**: 语雀文档知识库服务" in captured_user

    @pytest.mark.asyncio
    async def test_kb_context_dedup_by_q(self, monkeypatch):
        """KB results with the same 'q' should be deduplicated."""
        diag = ToolsMcpFormatDiagnostic()
        ctx = _make_ctx({"TOOLS.md": "## Tools\n\nSome tools content"})

        call_count = 0

        def mock_kb(question, kb_config=None):
            nonlocal call_count
            call_count += 1
            # Both MCPs return a result with q="CCT"
            return [{"q": "CCT", "a": "query:CCT\nanswer:大安全CCT平台", "rerankScore": 0.99}]

        monkeypatch.setattr(
            "agentclaw.community.core.harness.diagnostics.tools.mcp_format._query_knowledge_base",
            mock_kb,
        )

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "无问题"

        ctx.llm.chat = capture_chat

        monkeypatch.setattr(
            ctx.bot_profile,
            "get_activated_mcps",
            lambda **kw: [
                {"server_code": "mcp.test.cct", "name": "CCT", "description": "CCT事件分析"},
                {"server_code": "mcp.test.cct2", "name": "CCT2", "description": "CCT风控查询"},
            ],
        )
        ctx.mcp_center = _MockMCPCenter({
            "mcp.test.cct": {"name": "CCT", "description": "CCT事件分析", "tools": []},
            "mcp.test.cct2": {"name": "CCT2", "description": "CCT风控查询", "tools": []},
        })

        findings = await diag.analyze(ctx)
        assert findings == []
        # Same q="CCT" deduplicated — bold term appears only once
        assert captured_user.count("**CCT**") == 1

    @pytest.mark.asyncio
    async def test_kb_context_failure_ignored(self, monkeypatch):
        """When KB query fails, diagnosis should still work without KB context."""
        diag = ToolsMcpFormatDiagnostic()
        ctx = _make_ctx({"TOOLS.md": "## Tools\n\nSome tools content"})

        monkeypatch.setattr(
            "agentclaw.community.core.harness.diagnostics.tools.mcp_format._query_knowledge_base",
            lambda question, kb_config=None: (_ for _ in ()).throw(RuntimeError("KB unavailable")),
        )

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "无问题"

        ctx.llm.chat = capture_chat

        monkeypatch.setattr(
            ctx.bot_profile,
            "get_activated_mcps",
            lambda **kw: [{"server_code": "mcp.test.x", "name": "X MCP", "description": "Test MCP"}],
        )
        ctx.mcp_center = _MockMCPCenter({"mcp.test.x": {"name": "X MCP", "description": "Test MCP", "tools": []}})

        findings = await diag.analyze(ctx)
        assert findings == []
        # KB section should NOT be present
        assert "--- 内网知识库参考" not in captured_user

    @pytest.mark.asyncio
    async def test_kb_context_empty_mcp_list(self, monkeypatch):
        """When no MCPs are activated, KB should not be queried and no KB section in prompt."""
        diag = ToolsMcpFormatDiagnostic()
        ctx = _make_ctx({"TOOLS.md": "## Tools\n\nSome tools content"})

        kb_called = False

        def mock_kb(question, kb_config=None):
            nonlocal kb_called
            kb_called = True
            return []

        monkeypatch.setattr(
            "agentclaw.community.core.harness.diagnostics.tools.mcp_format._query_knowledge_base",
            mock_kb,
        )

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "无问题"

        ctx.llm.chat = capture_chat

        monkeypatch.setattr(
            ctx.bot_profile,
            "get_activated_mcps",
            lambda **kw: [],
        )

        findings = await diag.analyze(ctx)
        assert findings == []
        assert not kb_called
        assert "--- 内网知识库参考" not in captured_user

    @pytest.mark.asyncio
    async def test_kb_context_low_rerank_score_filtered(self, monkeypatch):
        """KB results with rerankScore below threshold should be excluded."""
        diag = ToolsMcpFormatDiagnostic()
        ctx = _make_ctx({"TOOLS.md": "## Tools\n\nSome tools content"})

        monkeypatch.setattr(
            "agentclaw.community.core.harness.diagnostics.tools.mcp_format._query_knowledge_base",
            lambda question, kb_config=None: [
                {"q": "CCT", "a": "query:CCT\nanswer:大安全CCT平台", "rerankScore": 0.9},
                {"q": "Unrelated", "a": "query:Unrelated\nanswer:无关条目", "rerankScore": 0.05},
            ],
        )

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "无问题"

        ctx.llm.chat = capture_chat

        monkeypatch.setattr(
            ctx.bot_profile,
            "get_activated_mcps",
            lambda **kw: [{"server_code": "mcp.test.cct", "name": "CCT MCP", "description": "CCT事件分析"}],
        )
        ctx.mcp_center = _MockMCPCenter({"mcp.test.cct": {"name": "CCT MCP", "description": "CCT事件分析", "tools": []}})

        findings = await diag.analyze(ctx)
        assert findings == []
        assert "**CCT**: 大安全CCT平台" in captured_user
        assert "Unrelated" not in captured_user

    @pytest.mark.asyncio
    async def test_input_schema_keeps_template_and_forwards_schema(self):
        """A tool exposing inputSchema is schema-derivable: keep the auto-fix
        template (so Phase 3 emits a real, schema-transcribed call-spec patch
        instead of a TODO) and forward a *compact* param table (flattened from
        inputSchema — not the full nested JSON, which would bloat the prompt
        past antchat's ~90s gateway window) to the diagnostic LLM.
        The finding is NOT bumped (it is recoverable)."""
        diag = ToolsMcpFormatDiagnostic()
        mcps = [{"server_code": "mcp.ant.arkai.dimamcpserver", "name": "Dima"}]
        ctx = _make_ctx({"TOOLS.md": "# Tools\n\n- some"}, activated_mcps=mcps)
        mock_center = MagicMock()
        mock_center.get_mcp_detail.return_value = {
            "name": "Dima MCP",
            "description": "Dima assistant",
            "tools": [{
                "name": "generateWorkSummary",
                "description": "Generate a work summary",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "startDate": {"type": "string", "description": "yyyyMMdd"},
                        "endDate": {"type": "string", "description": "yyyyMMdd"},
                    },
                    "required": ["startDate", "endDate"],
                },
            }],
        }
        ctx.mcp_center = mock_center

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "MCP规范不全\n建议据 schema 补全 [SCORE:55]"

        ctx.llm.chat = capture_chat
        findings = await diag.analyze(ctx)

        assert len(findings) == 1
        # Recoverable ⇒ template kept, finding NOT bumped to pass.
        assert findings[0].suggested_template_ids == [2]
        assert findings[0].severity == Severity.WARNING
        assert findings[0].score == 55
        assert findings[0].result != "pass"
        # inputSchema is rendered as a compact one-line tool signature for
        # transcription; the full nested JSON is NOT forwarded (that's what used
        # to bloat the prompt past antchat's ~90s gateway window). The one-liner
        # keeps param name/type/required (star) so the LLM can transcribe it.
        assert "inputSchema" not in captured_user
        assert "generateWorkSummary(startDate:string*, endDate:string*)" in captured_user
        assert "可据参数表转录" in captured_user

    @pytest.mark.asyncio
    async def test_all_unreachable_clears_template_and_bumps(self):
        """MCPs with neither verified_guide nor inputSchema are advisory
        (platform must author specs): drop the template and bump to pass so
        health_score isn't stuck low; message carries the 平台补全中 note."""
        diag = ToolsMcpFormatDiagnostic()
        mcps = [{"server_code": "mcp.ant.arkai.dimamcpserver", "name": "Dima"}]
        ctx = _make_ctx({"TOOLS.md": "# Tools"}, activated_mcps=mcps)
        mock_center = MagicMock()
        mock_center.get_mcp_detail.return_value = {
            "name": "Dima MCP",
            "description": "Dima assistant",
            "tools": [{"name": "generateWorkSummary", "description": "..."}],  # no inputSchema
        }
        ctx.mcp_center = mock_center
        ctx.llm.chat = AsyncMock(return_value="MCP规范缺失\n建议后续补充调用规范 [SCORE:45]")
        findings = await diag.analyze(ctx)

        assert len(findings) == 1
        assert findings[0].suggested_template_ids == []
        assert findings[0].severity == Severity.INFO
        assert findings[0].score >= 80
        assert findings[0].result == "pass"
        assert "平台补全中" in findings[0].message

    @pytest.mark.asyncio
    async def test_mixed_verified_and_unreachable_keeps_template(self):
        """A verified MCP makes the run recoverable: keep the template (real
        patch for the verified spec) even though unreachable MCPs are also
        present; the finding keeps its honest score (not bumped)."""
        diag = ToolsMcpFormatDiagnostic()
        verified_code = "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver"
        mcps = [
            {"server_code": verified_code, "name": "skylark"},
            {"server_code": "mcp.ant.arkai.dimamcpserver", "name": "Dima"},
        ]
        ctx = _make_ctx({"TOOLS.md": "# Tools"}, activated_mcps=mcps)
        mock_center = MagicMock()

        def detail(code):
            if code == verified_code:
                return {"name": "语雀MCP", "description": "语雀", "tools": [{"name": "skylark_search"}]}
            return {"name": "Dima MCP", "description": "Dima", "tools": [{"name": "generateWorkSummary"}]}

        mock_center.get_mcp_detail.side_effect = detail
        ctx.mcp_center = mock_center
        ctx.llm.chat = AsyncMock(return_value="MCP规范不全\n部分MCP缺规范 [SCORE:60]")
        findings = await diag.analyze(ctx)

        assert len(findings) == 1
        # Recoverable (verified_guide present) ⇒ template kept, score honest.
        assert findings[0].suggested_template_ids == [2]
        assert findings[0].score == 60
        assert findings[0].severity == Severity.WARNING

    def test_compact_tool_flattens_input_schema(self):
        """_compact_tool_for_prompt flattens a nested inputSchema into a single
        prompt line (name + param:type*=required sig + one-line desc) and drops
        the nested JSON — the slimmed payload that goes into the diagnostic
        prompt."""
        tool = {
            "name": "create_doc",
            "description": "创建语雀文档\n第二行不该进入 prompt",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "文档标题"},
                    "tags": {"type": "array", "description": "标签列表"},
                    "private": {"type": "boolean", "description": "是否私有"},
                },
                "required": ["title"],
            },
        }
        compact = _compact_tool_for_prompt(tool)
        assert compact == "create_doc(title:string*, tags:array, private:boolean) — 创建语雀文档"

    def test_compact_tool_without_schema_returns_empty_params(self):
        """A tool with no inputSchema renders as `name — desc` (no param sig) so
        the LLM can still list it in the mapping table."""
        compact = _compact_tool_for_prompt({"name": "bare_tool", "description": "no schema"})
        assert compact == "bare_tool — no schema"

    def test_compact_tool_skips_malformed_param_defs(self):
        """A schema property whose value is not a dict is skipped (the
        `not isinstance(pdef, dict)` guard) rather than crashing the prompt
        build; well-formed siblings still come through."""
        tool = {
            "name": "weird",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "good": {"type": "string", "description": "ok"},
                    "bad": "not-a-dict",
                    "also_bad": None,
                },
                "required": ["good"],
            },
        }
        compact = _compact_tool_for_prompt(tool)
        assert compact == "weird(good:string*)"

    def test_compact_mcp_tools_non_list_returns_empty(self):
        """A non-list `tools` value (None, a string, a dict) hits the early
        return and yields no tools and no omitted count."""
        from agentclaw.community.core.harness.diagnostics.tools.mcp_format import (
            _compact_mcp_tools_for_prompt,
        )
        assert _compact_mcp_tools_for_prompt({"tools": None}) == ([], 0)
        assert _compact_mcp_tools_for_prompt({"tools": "not-a-list"}) == ([], 0)
        assert _compact_mcp_tools_for_prompt({}) == ([], 0)

    @pytest.mark.asyncio
    async def test_verified_guide_tools_also_capped_with_omitted(self):
        """The verified bucket also caps tools and surfaces the `…另N个未列出`
        note (the verified-guide path shares _compact_mcp_tools_for_prompt);
        this covers the `if omitted` branch of the verified block."""
        diag = ToolsMcpFormatDiagnostic()
        code = "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver"
        mcps = [{"server_code": code, "name": "skylark"}]
        ctx = _make_ctx({"TOOLS.md": "# Tools\n\n- some"}, activated_mcps=mcps)
        total = _MAX_TOOLS_PER_MCP_IN_PROMPT + 3
        mock_center = MagicMock()
        mock_center.get_mcp_detail.return_value = {
            "name": "语雀MCP",
            "description": "语雀",
            "tools": [
                {"name": f"t{i}", "description": "d", "inputSchema": {"type": "object"}}
                for i in range(total)
            ],
        }
        ctx.mcp_center = mock_center

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "无问题"

        ctx.llm.chat = capture_chat
        await diag.analyze(ctx)

        assert "另3个未列出" in captured_user
        assert "t0" in captured_user
        assert f"t{total - 1}" not in captured_user

    @pytest.mark.asyncio
    async def test_tools_capped_per_mcp_with_omitted(self):
        """An MCP exposing more tools than the per-MCP cap only forwards the
        first N (compacted one-liners); the remainder is summarised as
        ``…另N个未列出`` so a 27-tool MCP doesn't dominate the prompt and blow
        the gateway window."""
        diag = ToolsMcpFormatDiagnostic()
        mcps = [{"server_code": "big", "name": "big"}]
        ctx = _make_ctx({"TOOLS.md": "# Tools\n\n- some"}, activated_mcps=mcps)
        total = _MAX_TOOLS_PER_MCP_IN_PROMPT + 5
        tools = [{
            "name": f"tool_{i}",
            "description": f"desc {i}",
            "inputSchema": {
                "type": "object",
                "properties": {"p": {"type": "string", "description": "p"}},
                "required": ["p"],
            },
        } for i in range(total)]
        mock_center = MagicMock()
        mock_center.get_mcp_detail.return_value = {
            "name": "big", "description": "big mcp", "tools": tools,
        }
        ctx.mcp_center = mock_center

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "无问题"

        ctx.llm.chat = capture_chat
        await diag.analyze(ctx)

        assert "另5个未列出" in captured_user
        # First capped tool and the boundary tool are present …
        assert "tool_0" in captured_user
        assert f"tool_{_MAX_TOOLS_PER_MCP_IN_PROMPT - 1}" in captured_user
        # … the truncated tail is not.
        assert f"tool_{_MAX_TOOLS_PER_MCP_IN_PROMPT + 4}" not in captured_user


class TestSoulPersonaDiagnostic:
    """Test D-SOUL-001: SOUL.md persona diagnostic."""

    @pytest.mark.asyncio
    async def test_empty_soul_returns_no_findings(self):
        diag = SoulPersonaDiagnostic()
        ctx = _make_ctx({"SOUL.md": ""})
        findings = await diag.analyze(ctx)
        assert findings == []

    @pytest.mark.asyncio
    async def test_profile_included_in_prompt(self, monkeypatch):
        """When BCSFuse returns profiles, active profile contents should appear in the prompt."""
        diag = SoulPersonaDiagnostic()
        ctx = _make_ctx({"SOUL.md": "## Persona\n\nI am a helpful bot"})

        mock_profiles_resp = {
            "items": [
                {
                    "worker_id": "bot1:user1",
                    "profile_id": "default",
                    "display_name": "施易",
                    "contents": {
                        "profile": "特征研发助手，帮助特征平台的用户进行脚本研发",
                        "capabilities": ["代码开发", "问题排查", "技术调研"],
                        "short_profile": "特征研发助手，协助用户进行脚本研发与特征平台操作。",
                    },
                    "quality_score": 0.3,
                    "quality_issues": [
                        "[SUGGEST] SOUL.md内容不够丰富",
                        "[WARN] 未配置SKILL",
                    ],
                },
            ],
            "total": 1,
            "active_profile_id": "default",
        }
        monkeypatch.setattr(
            "agentclaw.community.core.harness.diagnostics.config.soul._fetch_profiles",
            lambda worker_id, base_url: mock_profiles_resp,
        )

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "无问题"

        ctx.llm.chat = capture_chat
        await diag.analyze(ctx)

        assert "SOUL.md 人格描述诊断" in captured_user
        assert "Bot 画像信息" in captured_user
        assert "施易" in captured_user
        assert "特征研发助手" in captured_user
        assert "代码开发" in captured_user

    @pytest.mark.asyncio
    async def test_profile_fetch_failure_ignored(self, monkeypatch):
        """When BCSFuse fails, the diagnostic should still work without profile."""
        diag = SoulPersonaDiagnostic()
        ctx = _make_ctx({"SOUL.md": "## Persona\n\nI am a helpful bot"})

        monkeypatch.setattr(
            "agentclaw.community.core.harness.diagnostics.config.soul._fetch_profiles",
            lambda worker_id, base_url: None,
        )

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "无问题"

        ctx.llm.chat = capture_chat
        findings = await diag.analyze(ctx)
        assert findings == []
        assert "SOUL.md 人格描述诊断" in captured_user
        # Profile data section should NOT be present when fetch fails
        assert "--- Bot 画像信息 ---" not in captured_user

    @pytest.mark.asyncio
    async def test_worker_id_format(self, monkeypatch):
        """worker_id should be formatted as bot_id:entity_id."""
        diag = SoulPersonaDiagnostic()
        ctx = _make_ctx({"SOUL.md": "## Persona\n\nBot"})

        captured_worker_id = None

        def mock_fetch(worker_id, base_url):
            nonlocal captured_worker_id
            captured_worker_id = worker_id
            return None

        monkeypatch.setattr(
            "agentclaw.community.core.harness.diagnostics.config.soul._fetch_profiles",
            mock_fetch,
        )

        ctx.llm.chat = AsyncMock(return_value="无问题")
        await diag.analyze(ctx)

        assert captured_worker_id == "bot1:user1"

    @pytest.mark.asyncio
    async def test_active_profile_selected(self, monkeypatch):
        """When multiple profiles exist, only the active one is used."""
        diag = SoulPersonaDiagnostic()
        ctx = _make_ctx({"SOUL.md": "## Persona\n\nBot"})

        mock_profiles_resp = {
            "items": [
                {
                    "worker_id": "bot1:user1",
                    "profile_id": "draft",
                    "display_name": "Draft Bot",
                    "contents": {
                        "profile": "This is a draft",
                        "capabilities": [],
                        "short_profile": "Draft",
                    },
                    "quality_score": 0.1,
                    "quality_issues": [],
                },
                {
                    "worker_id": "bot1:user1",
                    "profile_id": "default",
                    "display_name": "Active Bot",
                    "contents": {
                        "profile": "This is the active profile",
                        "capabilities": ["debug"],
                        "short_profile": "Active",
                    },
                    "quality_score": 0.8,
                    "quality_issues": [],
                },
            ],
            "total": 2,
            "active_profile_id": "default",
        }
        monkeypatch.setattr(
            "agentclaw.community.core.harness.diagnostics.config.soul._fetch_profiles",
            lambda worker_id, base_url: mock_profiles_resp,
        )

        captured_user = None

        async def capture_chat(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return "无问题"

        ctx.llm.chat = capture_chat
        await diag.analyze(ctx)

        # Active profile should be used, not the draft one
        assert "Active Bot" in captured_user
        assert "This is the active profile" in captured_user
        assert "Draft Bot" not in captured_user

    @pytest.mark.asyncio
    async def test_low_quality_profile_clears_template(self, monkeypatch):
        """B-mode: profile quality < 0.7 → suggestions are advisory (improve
        profile / supplement info), not a SOUL.md edit the patch LLM can fill.
        The auto-fix template must be dropped so Phase 3 doesn't emit
        TODO-placeholder patches. The finding still carries its score/message
        (counts toward health_score, surfaces in the report)."""
        diag = SoulPersonaDiagnostic()
        ctx = _make_ctx({"SOUL.md": "## Persona\n\n通用助手"})
        mock_profiles_resp = {
            "items": [{
                "profile_id": "default", "display_name": "Bot",
                "contents": {"profile": "研发助手", "capabilities": ["代码开发"]},
                "quality_score": 0.3, "quality_issues": [],
            }],
            "total": 1, "active_profile_id": "default",
        }
        monkeypatch.setattr(
            "agentclaw.community.core.harness.diagnostics.config.soul._fetch_profiles",
            lambda worker_id, base_url: mock_profiles_resp,
        )
        ctx.llm.chat = AsyncMock(
            return_value="通用模板问题\n缺少专属定制，建议完善画像\n[SCORE:50]"
        )
        findings = await diag.analyze(ctx)
        assert len(findings) == 1
        assert findings[0].score == 50
        assert findings[0].suggested_template_ids == []

    @pytest.mark.asyncio
    async def test_no_profile_clears_template(self, monkeypatch):
        """When BCSFuse is unavailable / returns nothing, the diagnostic can't
        generate a full SOUL.md rewrite — its suggestions are advisory, so the
        template is dropped (same as B-mode)."""
        diag = SoulPersonaDiagnostic()
        ctx = _make_ctx({"SOUL.md": "## Persona\n\n通用助手"})
        monkeypatch.setattr(
            "agentclaw.community.core.harness.diagnostics.config.soul._fetch_profiles",
            lambda worker_id, base_url: None,
        )
        ctx.llm.chat = AsyncMock(
            return_value="缺少专属定制\n建议补充 Bot 专属职责\n[SCORE:55]"
        )
        findings = await diag.analyze(ctx)
        assert len(findings) == 1
        assert findings[0].suggested_template_ids == []

    @pytest.mark.asyncio
    async def test_high_quality_profile_keeps_template(self, monkeypatch):
        """A-mode: profile quality >= 0.7 → the LLM produces a full suggested
        SOUL.md rewrite, a concrete file edit. Keep the auto-fix template so
        Phase 3 can generate a real patch."""
        diag = SoulPersonaDiagnostic()
        ctx = _make_ctx({"SOUL.md": "## Persona\n\n通用助手"})
        mock_profiles_resp = {
            "items": [{
                "profile_id": "default", "display_name": "Bot",
                "contents": {"profile": "研发助手", "capabilities": ["代码开发", "问题排查"]},
                "quality_score": 0.8, "quality_issues": [],
            }],
            "total": 1, "active_profile_id": "default",
        }
        monkeypatch.setattr(
            "agentclaw.community.core.harness.diagnostics.config.soul._fetch_profiles",
            lambda worker_id, base_url: mock_profiles_resp,
        )
        ctx.llm.chat = AsyncMock(
            return_value="缺少专属定制\n建议版 SOUL.md\n\n我是研发助手\n[SCORE:50]"
        )
        findings = await diag.analyze(ctx)
        assert len(findings) == 1
        assert findings[0].suggested_template_ids == [3]


class TestDiagnosticContextNoCache:
    """Test that DiagnosticContext always reads fresh content from BotProfile."""

    @pytest.mark.asyncio
    async def test_file_read_always_fresh(self):
        profile = AsyncMock()
        profile.read_file = AsyncMock(
            return_value=MockBotFileRef(content="content", file_type="AGENTS.md")
        )
        ctx = DiagnosticContext(
            llm=AsyncMock(), bot_profile=profile,
            entity_type="staff", entity_id="user1", bot_id="bot1",
        )
        result1 = await ctx.read_file("AGENTS.md")
        result2 = await ctx.read_file("AGENTS.md")
        assert result1 == "content"
        assert result2 == "content"
        # BotProfile.read_file should be called twice — no caching
        assert profile.read_file.call_count == 2

    @pytest.mark.asyncio
    async def test_file_read_failure_returns_empty(self):
        ctx = _make_ctx({})
        async def fail_read(*args, **kwargs):
            raise RuntimeError("read error")
        ctx.bot_profile.read_file = fail_read

        result = await ctx.read_file("NONEXISTENT.md")
        assert result == ""


class TestToolsMcpFormatBatching:
    """D-TOOLS-002 batched prompt mode: prompt size decoupled from MCP count."""

    def test_pack_splits_on_count(self):
        items = [("schema", f"block-{i}", {"server_code": f"mcp.{i}"}) for i in range(7)]
        batches = _pack_mcp_batches(items)
        assert [len(b) for b in batches] == [_BATCH_MAX_MCPS, 2]

    def test_pack_splits_on_chars(self):
        big = "x" * (_BATCH_CHAR_BUDGET - 100)
        items = [
            ("schema", big, {"server_code": "mcp.a"}),
            ("schema", big, {"server_code": "mcp.b"}),
        ]
        batches = _pack_mcp_batches(items)
        assert [[d["server_code"] for _, _, d in b] for b in batches] == [["mcp.a"], ["mcp.b"]]

    def test_pack_single_when_small(self):
        items = [("schema", "tiny", {"server_code": "mcp.a"})]
        assert len(_pack_mcp_batches(items)) == 1

    def test_pack_oversized_block_gets_own_batch(self):
        huge = "y" * (_BATCH_CHAR_BUDGET * 2)
        items = [
            ("schema", "a", {"server_code": "mcp.a"}),
            ("schema", huge, {"server_code": "mcp.b"}),
            ("schema", "c", {"server_code": "mcp.c"}),
        ]
        batches = _pack_mcp_batches(items)
        assert [[d["server_code"] for _, _, d in b] for b in batches] == [["mcp.a"], ["mcp.b"], ["mcp.c"]]

    def test_pack_large_mcp_alone(self):
        # A many-tool MCP (tools > _LARGE_MCP_TOOL_THRESHOLD) gets its own
        # batch so that batch's output is one spec draft, not several —
        # keeps the call inside the 90s window for big MCPs.
        big = {"server_code": "mcp.big", "tools": [{} for _ in range(25)]}
        small_a = {"server_code": "mcp.a", "tools": [{} for _ in range(3)]}
        small_b = {"server_code": "mcp.b", "tools": [{} for _ in range(3)]}
        items = [
            ("schema", "a", small_a),
            ("schema", "y", big),
            ("schema", "b", small_b),
        ]
        batches = _pack_mcp_batches(items)
        codes = [[d["server_code"] for _, _, d in b] for b in batches]
        assert ["mcp.big"] in codes  # big sits in its own batch
        big_batch = [b for b in batches if any(d["server_code"] == "mcp.big" for _, _, d in b)][0]
        assert len(big_batch) == 1

    def test_synthesize_all_no_issue(self):
        out = _synthesize_batch_responses([("无问题", ["mcp.a"]), ("无问题", ["mcp.b"])])
        assert out == "无问题"

    def test_synthesize_all_failed_returns_sentinel(self):
        out = _synthesize_batch_responses([(None, ["mcp.a"]), ("[llm disabled]", ["mcp.b"])])
        assert out == "[llm disabled]"

    def test_synthesize_aggregates_scores_and_summaries(self):
        out = _synthesize_batch_responses([
            ("映射表缺失\n缺 a 的映射\n[SCORE:70]", ["mcp.a"]),
            ("规范不全\n缺 b 的规范\n[SCORE:80]", ["mcp.b"]),
        ])
        assert out.startswith("映射表缺失；规范不全")
        assert "缺 a 的映射" in out and "缺 b 的规范" in out
        assert "[SCORE:50]" in out  # 100 - (30 + 20)

    def test_synthesize_partial_failure_noted(self):
        out = _synthesize_batch_responses([
            ("映射表缺失\n缺 a\n[SCORE:70]", ["mcp.a"]),
            (None, ["mcp.b"]),
        ])
        assert "未完成诊断" in out and "mcp.b" in out
        assert "[SCORE:70]" in out

    def test_synthesize_only_failures_is_check_failed_not_pass(self):
        out = _synthesize_batch_responses([
            ("无问题", ["mcp.a"]),
            (None, ["mcp.b"]),
        ])
        assert "部分 MCP 诊断失败" in out
        assert "[SCORE:0]" in out

    @pytest.mark.asyncio
    async def test_analyze_splits_into_batches_and_aggregates(self):
        diag = ToolsMcpFormatDiagnostic()
        mcps = [{"server_code": f"mcp.{c}", "name": c} for c in "abcdefg"]
        ctx = _make_ctx({"TOOLS.md": "# Tools\n\n- some tools"}, activated_mcps=mcps)
        ctx.mcp_center = _MockMCPCenter({
            f"mcp.{c}": {
                "name": c,
                "description": f"desc {c}",
                "tools": [{
                    "name": "t1",
                    "inputSchema": {"type": "object", "properties": {"p": {"type": "string"}}},
                }],
            }
            for c in "abcdefg"
        })

        captured: list[str] = []

        async def fake_chat(system, user, **kwargs):
            captured.append(user)
            if "mcp.a" in user:
                return "规范缺失\n缺 a 规范\n[SCORE:70]"
            return "无问题"

        ctx.llm.chat = fake_chat
        findings = await diag.analyze(ctx)

        # 7 MCPs / _BATCH_MAX_MCPS(5) -> 2 batches [a-e],[f-g]
        assert len(captured) == 2
        assert "第 1/2 批" in captured[0] and "第 2/2 批" in captured[1]
        assert "mcp.a" in captured[0] and "mcp.f" not in captured[0]
        assert "mcp.g" in captured[1] and "mcp.a" not in captured[1]
        assert len(findings) == 1
        assert findings[0].score == 70
        assert findings[0].short_summary == "规范缺失"

    @pytest.mark.asyncio
    async def test_analyze_all_batches_failed_yields_llm01(self):
        diag = ToolsMcpFormatDiagnostic()
        mcps = [{"server_code": f"mcp.{c}", "name": c} for c in "abcde"]
        ctx = _make_ctx({"TOOLS.md": "# Tools\n\n- some tools"}, activated_mcps=mcps)
        ctx.mcp_center = _MockMCPCenter({
            f"mcp.{c}": {"name": c, "description": f"desc {c}", "tools": []}
            for c in "abcde"
        })

        async def dead_chat(system, user, **kwargs):
            return "[llm disabled]"

        ctx.llm.chat = dead_chat
        findings = await diag.analyze(ctx)

        assert len(findings) == 1
        assert findings[0].rule_id == "LLM01"
