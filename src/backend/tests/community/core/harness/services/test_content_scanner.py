"""Unit tests for ContentScanner service (diagnostic-based, mocked dependencies)."""
import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from agentclaw.community.core.harness.diagnostics import register_all_diagnostics
from agentclaw.community.core.harness.diagnostics.base import Diagnostic, DiagnosticContext
from agentclaw.community.core.harness.models import (
    Finding,
    Layer,
    PatchTarget,
    PatchTemplate,
    Severity,
)
from agentclaw.community.core.harness.services.content_scanner import ContentScanner
from agentclaw.community.core.harness.services.llm import LLM


def _scanner(**kwargs):
    """Build a ContentScanner, defaulting the DI-required deps to mocks.

    ``patch_library``/``mcp_center``/``kb_config`` are required constructor deps
    (always supplied by the DI provider); tests that don't exercise them pass
    mocks so the honest required signature is satisfied.
    """
    kwargs.setdefault("patch_library", MagicMock())
    kwargs.setdefault("mcp_center", MagicMock())
    kwargs.setdefault("kb_config", MagicMock())
    return ContentScanner(**kwargs)


# ── Helpers ─────────────────────────────────────────────

class MockBotFileRef:
    def __init__(self, content: str, file_type: str):
        self.content = content
        self.file_type = file_type


def _make_bot_profile(file_contents: dict[str, str] | None = None):
    """Create a mock BotProfile that returns specified file contents."""
    profile = AsyncMock()
    contents = file_contents or {}

    async def list_files(entity_type, entity_id, bot_id):
        return list(contents.keys())

    async def read_file(entity_type, entity_id, bot_id, file_type, operator_id="", publish_id=None):
        content = contents.get(file_type, "")
        return MockBotFileRef(content=content, file_type=file_type)

    profile.list_files = list_files
    profile.read_file = read_file
    return profile


def _make_llm(responses: dict[str, str] | None = None):
    """Create a mock LLM that returns predefined responses per file type."""
    llm = AsyncMock(spec=LLM)
    resp_map = responses or {}

    async def chat(system, user, **kwargs):
        for ft, resp in resp_map.items():
            if ft in user:
                return resp
        return "[]"

    llm.chat = chat
    return llm


def _make_patch_library(template_names: list[str] | None = None):
    """Create a mock PatchLibrary."""
    lib = MagicMock()
    if template_names:
        templates = [
            PatchTemplate(
                name=name,
                layer=Layer.L1,
                target=PatchTarget(files=["AGENTS.md"]),
            )
            for name in template_names
        ]
        lib.list_applicable.return_value = templates
    else:
        lib.list_applicable.return_value = []
    return lib


class _StubDiag(Diagnostic):
    """Minimal concrete Diagnostic for testing."""

    def __init__(self, diag_id="D-STUB", name="Stub", severity="info", findings=None, *, raise_error=False):
        self.id = diag_id
        self.name = name
        self.severity = severity
        self._findings = findings or []
        self._raise_error = raise_error

    async def analyze(self, ctx: DiagnosticContext) -> list[Finding]:
        if self._raise_error:
            raise RuntimeError(f"Diagnostic {self.id} failed")
        # Ensure files are read (via context) to test caching
        if self.id.startswith("D-AGENTS"):
            await ctx.read_file("AGENTS.md")
        return self._findings


# ── Test Classes ────────────────────────────────────────

class TestContentScanner:
    """Test ContentScanner with diagnostic-based architecture."""

    @pytest.mark.asyncio
    async def test_scan_with_custom_diagnostics(self):
        profile = _make_bot_profile({"AGENTS.md": "some content"})
        llm = _make_llm()

        findings = [
            Finding(rule_id="D-AGENTS-001", rule_name="角色定义简短", severity=Severity.WARNING,
                    file_type="AGENTS.md", message="角色定义描述可以更具体"),
        ]
        diag = _StubDiag(findings=findings)
        scanner = _scanner(bot_profile=profile, llm=llm, diagnostics=[diag])
        report = await scanner.scan("staff", "user1", "bot1")

        assert report.status == "completed"
        assert len(report.findings) == 1
        assert report.findings[0].rule_id == "D-AGENTS-001"
        assert report.health_score == 90  # 100 - 10 (warning)

    @pytest.mark.asyncio
    async def test_scan_with_critical_finding(self):
        profile = _make_bot_profile({"SAFETY.md": "some content"})
        llm = _make_llm()

        findings = [
            Finding(rule_id="D-SAFETY-001", rule_name="安全约束缺失", severity=Severity.CRITICAL,
                    file_type="SAFETY.md", message="SAFETY.md 为空，缺少安全约束"),
        ]
        diag = _StubDiag(diag_id="D-SAFETY-001", severity="critical", findings=findings)
        scanner = _scanner(bot_profile=profile, llm=llm, diagnostics=[diag])
        report = await scanner.scan("staff", "user1", "bot1")

        assert report.status == "completed"
        assert any(f.severity == Severity.CRITICAL for f in report.findings)
        # score=0 findings → "error" bucket, not "fail"
        assert report.findings_summary.get("error", 0) >= 1
        assert report.health_score < 100

    @pytest.mark.asyncio
    async def test_scan_diagnostic_failure_produces_sys02(self):
        profile = _make_bot_profile({"SAFETY.md": "content"})
        llm = _make_llm()

        failing_diag = _StubDiag(diag_id="D-SAFETY-001", raise_error=True)
        scanner = _scanner(bot_profile=profile, llm=llm, diagnostics=[failing_diag])
        report = await scanner.scan("staff", "user1", "bot1")

        assert report.status == "completed"
        sys02 = [f for f in report.findings if f.rule_id == "SYS02"]
        assert len(sys02) == 1
        assert "D-SAFETY-001" in sys02[0].message

    @pytest.mark.asyncio
    async def test_scan_mixed_success_and_failure(self):
        profile = _make_bot_profile({})
        llm = _make_llm()

        good_findings = [
            Finding(rule_id="D-AGENTS-001", rule_name="test", severity=Severity.INFO,
                    file_type="AGENTS.md", message="info finding"),
        ]
        good_diag = _StubDiag(diag_id="D-AGENTS-001", findings=good_findings)
        bad_diag = _StubDiag(diag_id="D-SAFETY-001", raise_error=True)
        scanner = _scanner(bot_profile=profile, llm=llm, diagnostics=[good_diag, bad_diag])
        report = await scanner.scan("staff", "user1", "bot1")

        assert report.status == "completed"
        assert len(report.findings) == 2  # 1 finding + 1 SYS02

    @pytest.mark.asyncio
    async def test_scan_empty_findings_perfect_score(self):
        profile = _make_bot_profile({})
        llm = _make_llm()

        scanner = _scanner(
            bot_profile=profile, llm=llm,
            diagnostics=[_StubDiag(findings=[])],
        )
        report = await scanner.scan("staff", "user1", "bot1")

        assert report.status == "completed"
        assert len(report.findings) == 0
        assert report.health_score == 100

    @pytest.mark.asyncio
    async def test_scan_with_patch_library_validates_ids(self):
        """PatchLibrary validates existing suggested_template_ids but no fallback."""
        profile = _make_bot_profile({"AGENTS.md": "content"})
        llm = _make_llm()
        patch_lib = _make_patch_library(["安全约束初始化"])

        findings = [
            Finding(rule_id="D-AGENTS-001", rule_name="test", severity=Severity.WARNING,
                    file_type="AGENTS.md", message="test", suggested_template_ids=[1]),
        ]
        scanner = _scanner(
            bot_profile=profile, llm=llm, patch_library=patch_lib,
            diagnostics=[_StubDiag(findings=findings)],
        )
        report = await scanner.scan("staff", "user1", "bot1")

        assert report.status == "completed"
        # list_applicable is NOT called because there's no fallback
        assert not patch_lib.list_applicable.called
        # get_template_by_id IS called to validate the existing template id
        assert patch_lib.get_template_by_id.called

    @pytest.mark.asyncio
    async def test_scan_patch_library_exception_handled(self):
        profile = _make_bot_profile({})
        llm = _make_llm()
        patch_lib = MagicMock()
        patch_lib.list_applicable.side_effect = RuntimeError("DB error")

        findings = [
            Finding(rule_id="D-AGENTS-001", rule_name="test", severity=Severity.WARNING,
                    file_type="AGENTS.md", message="test"),
        ]
        scanner = _scanner(
            bot_profile=profile, llm=llm, patch_library=patch_lib,
            diagnostics=[_StubDiag(findings=findings)],
        )
        report = await scanner.scan("staff", "user1", "bot1")

        assert report.status == "completed"

    @pytest.mark.asyncio
    async def test_scan_default_diagnostics(self):
        profile = _make_bot_profile({"AGENTS.md": "content"})
        llm = _make_llm({"AGENTS.md": "[]"})

        scanner = _scanner(bot_profile=profile, llm=llm)
        # Should use all 6 registered diagnostics by default
        assert len(scanner._diagnostics) == 6

    @pytest.mark.asyncio
    async def test_scan_check_items_from_diagnostics(self):
        profile = _make_bot_profile({})
        llm = _make_llm()

        diags = [
            _StubDiag(diag_id="D-AGENTS-001", name="角色定义诊断", severity="warning"),
            _StubDiag(diag_id="D-SAFETY-001", name="安全约束诊断", severity="critical"),
        ]
        scanner = _scanner(bot_profile=profile, llm=llm, diagnostics=diags)
        report = await scanner.scan("staff", "user1", "bot1")

        rule_ids = {item["rule_id"] for item in report.check_items}
        assert "D-AGENTS-001" in rule_ids
        assert "D-SAFETY-001" in rule_ids
        assert len(report.check_items) == 2

    @pytest.mark.asyncio
    async def test_scan_parallel_diagnostic_execution(self):
        profile = _make_bot_profile({})
        llm = _make_llm()
        call_count = 0

        class CountingDiag(Diagnostic):
            id = "D-COUNT"
            name = "Counting"
            severity = "info"

            async def analyze(self, ctx):
                nonlocal call_count
                call_count += 1
                return []

        diags = [CountingDiag(), CountingDiag()]
        scanner = _scanner(bot_profile=profile, llm=llm, diagnostics=diags)
        report = await scanner.scan("staff", "user1", "bot1")

        assert report.status == "completed"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_scan_llm_disabled_produces_llm01(self):
        """When LLM returns '[llm disabled]', parse_findings produces LLM01."""
        profile = _make_bot_profile({"AGENTS.md": "content"})
        llm = _make_llm({"AGENTS.md": "[llm disabled]"})

        # Use real diagnostic that reads AGENTS.md
        from agentclaw.community.core.harness.diagnostics.agents.safety_rules import AgentsSafetyRulesDiagnostic
        scanner = _scanner(bot_profile=profile, llm=llm, diagnostics=[AgentsSafetyRulesDiagnostic()])
        report = await scanner.scan("staff", "user1", "bot1")

        assert report.status == "completed"
        assert any(f.rule_id == "LLM01" for f in report.findings)

    @pytest.mark.asyncio
    async def test_scan_duration_recorded(self):
        profile = _make_bot_profile({})
        llm = _make_llm()

        scanner = _scanner(
            bot_profile=profile, llm=llm,
            diagnostics=[_StubDiag(findings=[])],
        )
        report = await scanner.scan("staff", "user1", "bot1")

        assert report.duration_ms >= 0


class TestScoring:
    """Test the scoring algorithm — per-file-type LLM score average."""

    def test_perfect_score_empty(self):
        assert ContentScanner._compute_score([]) == 100

    def test_llm_score_based_single_file(self):
        """Single file type with two findings: average their scores."""
        findings = [
            Finding(rule_id="D-AGENTS-001", rule_name="test", severity=Severity.WARNING,
                    file_type="AGENTS.md", message="test", score=80),
            Finding(rule_id="D-AGENTS-002", rule_name="test", severity=Severity.WARNING,
                    file_type="AGENTS.md", message="test", score=60),
        ]
        # AGENTS.md avg = (80+60)/2 = 70 → health_score = 70
        assert ContentScanner._compute_score(findings) == 70

    def test_llm_score_based_multiple_files(self):
        """Multiple file types: average of per-file averages."""
        findings = [
            Finding(rule_id="D-AGENTS-001", rule_name="test", severity=Severity.WARNING,
                    file_type="AGENTS.md", message="test", score=90),
            Finding(rule_id="D-AGENTS-002", rule_name="test", severity=Severity.WARNING,
                    file_type="AGENTS.md", message="test", score=70),
            Finding(rule_id="D-SOUL-001", rule_name="test", severity=Severity.INFO,
                    file_type="SOUL.md", message="test", score=50),
        ]
        # AGENTS.md avg = (90+70)/2 = 80, SOUL.md avg = 50
        # health_score = (80+50)/2 = 65
        assert ContentScanner._compute_score(findings) == 65

    def test_llm_score_ignores_zero_scores(self):
        """Findings with score=0 are excluded from file average."""
        findings = [
            Finding(rule_id="D-AGENTS-001", rule_name="test", severity=Severity.WARNING,
                    file_type="AGENTS.md", message="test", score=80),
            Finding(rule_id="D-AGENTS-002", rule_name="test", severity=Severity.WARNING,
                    file_type="AGENTS.md", message="test", score=0),
        ]
        # AGENTS.md: only score=80 counts, avg = 80 → health_score = 80
        assert ContentScanner._compute_score(findings) == 80

    def test_fallback_penalty_no_scores(self):
        """When no LLM scores, fall back to penalty-based scoring."""
        findings = [Finding(rule_id="D-SAFETY-001", rule_name="test", severity=Severity.CRITICAL,
                            file_type="SAFETY.md", message="test")]
        assert ContentScanner._compute_score(findings) == 80  # 100 - 20

    def test_fallback_penalty_mixed(self):
        findings = [
            Finding(rule_id="D-SAFETY-001", rule_name="test", severity=Severity.CRITICAL,
                    file_type="SAFETY.md", message="test"),
            Finding(rule_id="D-AGENTS-001", rule_name="test", severity=Severity.WARNING,
                    file_type="AGENTS.md", message="test"),
            Finding(rule_id="D-AGENTS-003", rule_name="test", severity=Severity.INFO,
                    file_type="AGENTS.md", message="test"),
        ]
        assert ContentScanner._compute_score(findings) == 68  # 100 - 20 - 10 - 2

    def test_floor_at_zero(self):
        findings = [Finding(rule_id=f"D-{i}", rule_name="test", severity=Severity.CRITICAL,
                            file_type="SAFETY.md", message="test") for i in range(10)]
        assert ContentScanner._compute_score(findings) == 0

    def test_mixed_score_and_no_score_files(self):
        """Files with scores use LLM scores; files without use penalty fallback."""
        findings = [
            Finding(rule_id="D-AGENTS-001", rule_name="test", severity=Severity.WARNING,
                    file_type="AGENTS.md", message="test", score=90),
            # SAFETY.md has no scores, but we only have LLM scores for AGENTS.md
            # Only file types with scores are included in the average
            Finding(rule_id="D-SAFETY-001", rule_name="test", severity=Severity.CRITICAL,
                    file_type="SAFETY.md", message="test", score=0),
        ]
        # AGENTS.md avg = 90, SAFETY.md has no nonzero scores so excluded
        # health_score = 90
        assert ContentScanner._compute_score(findings) == 90

    def test_all_scores_zero_uses_penalty(self):
        """When all scores are 0 (LLM disabled), fall back to penalty."""
        findings = [
            Finding(rule_id="D-SAFETY-001", rule_name="test", severity=Severity.CRITICAL,
                    file_type="SAFETY.md", message="test", score=0),
        ]
        assert ContentScanner._compute_score(findings) == 80  # 100 - 20


class TestSummarize:
    """Test the findings summary — summarized by file-type-level result (pass/warning/fail/error)."""

    def test_summarize_empty(self):
        result = ContentScanner._summarize([])
        assert result == {"pass": 0, "warning": 0, "fail": 0, "error": 0}

    def test_summarize_mixed(self):
        # SAFETY.md: 1 critical, score=0 → file result = error (all scores 0)
        # AGENTS.md: 2 warning + 1 info, scores=0 → file result = error (all scores 0)
        findings = [
            Finding(rule_id="D-SAFETY-001", rule_name="test", severity=Severity.CRITICAL,
                    file_type="SAFETY.md", message="test"),
            Finding(rule_id="D-AGENTS-001", rule_name="test", severity=Severity.WARNING,
                    file_type="AGENTS.md", message="test"),
            Finding(rule_id="D-AGENTS-001b", rule_name="test", severity=Severity.WARNING,
                    file_type="AGENTS.md", message="test"),
            Finding(rule_id="D-AGENTS-003", rule_name="test", severity=Severity.INFO,
                    file_type="AGENTS.md", message="test"),
        ]
        result = ContentScanner._summarize(findings)
        assert result == {"pass": 0, "warning": 0, "fail": 0, "error": 2}

    def test_summarize_includes_sys_and_llm_findings(self):
        # SAFETY.md: critical, score=0 → error
        # AGENTS.md: info, score=0 → error
        # TOOLS.md: critical, score=0 → error
        findings = [
            Finding(rule_id="SYS01", rule_name="文件读取失败", severity=Severity.CRITICAL,
                    file_type="SAFETY.md", message="read error"),
            Finding(rule_id="LLM01", rule_name="LLM 服务未启用", severity=Severity.INFO,
                    file_type="AGENTS.md", message="disabled"),
            Finding(rule_id="SYS02", rule_name="LLM 诊断失败", severity=Severity.CRITICAL,
                    file_type="TOOLS.md", message="parse error"),
        ]
        result = ContentScanner._summarize(findings)
        assert result == {"pass": 0, "warning": 0, "fail": 0, "error": 3}

    def test_summarize_all_pass(self):
        # A file type with score=100 → pass
        findings = [
            Finding(rule_id="D-AGENTS-001", rule_name="test", severity=Severity.INFO,
                    file_type="AGENTS.md", message="ok", score=100),
        ]
        result = ContentScanner._summarize(findings)
        assert result == {"pass": 1, "warning": 0, "fail": 0, "error": 0}

    def test_summarize_score_based(self):
        # AGENTS.md: avg score 75 → warning
        # SAFETY.md: avg score 50 → fail
        findings = [
            Finding(rule_id="D-AGENTS-001", rule_name="test", severity=Severity.WARNING,
                    file_type="AGENTS.md", message="test", score=75),
            Finding(rule_id="D-SAFETY-001", rule_name="test", severity=Severity.CRITICAL,
                    file_type="SAFETY.md", message="test", score=50),
        ]
        result = ContentScanner._summarize(findings)
        assert result == {"pass": 0, "warning": 1, "fail": 1, "error": 0}

    def test_summarize_error_for_zero_score(self):
        # TOOLS.md: score=0 (LLM disabled) → error
        findings = [
            Finding(rule_id="LLM01", rule_name="LLM 服务未启用", severity=Severity.INFO,
                    file_type="TOOLS.md", message="LLM未启用", score=0),
        ]
        result = ContentScanner._summarize(findings)
        assert result == {"pass": 0, "warning": 0, "fail": 0, "error": 1}

    def test_summarize_mixed_with_error(self):
        # AGENTS.md: score=90 → pass
        # TOOLS.md: score=0 → error
        findings = [
            Finding(rule_id="D-AGENTS-001", rule_name="test", severity=Severity.INFO,
                    file_type="AGENTS.md", message="ok", score=90),
            Finding(rule_id="LLM01", rule_name="LLM 服务未启用", severity=Severity.INFO,
                    file_type="TOOLS.md", message="disabled", score=0),
        ]
        result = ContentScanner._summarize(findings)
        assert result == {"pass": 1, "warning": 0, "fail": 0, "error": 1}