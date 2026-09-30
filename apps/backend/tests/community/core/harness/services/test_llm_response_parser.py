"""Unit tests for LLM response parser — plain text diagnostic response."""
import pytest

from agentclaw.community.core.harness.models import Finding, Severity
from agentclaw.community.core.harness.services.llm_response_parser import parse_diagnostic_response

_DIAG_ID = "D-AGENTS-001"
_DIAG_NAME = "AGENTS.md 角色定义诊断"
_DIAG_SEVERITY = "warning"
_DIAG_FILE = "AGENTS.md"
_DIAG_TEMPLATES = [1111]


class TestParseDiagnosticResponse:
    """Test parse_diagnostic_response with various LLM response formats."""

    def test_normal_text_returns_finding(self):
        msg = "角色定义过于简短"
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            _DIAG_TEMPLATES, "bot1", msg,
        )
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == _DIAG_ID
        assert f.rule_name == _DIAG_NAME
        assert f.severity == Severity.WARNING
        assert f.file_type == _DIAG_FILE
        assert f.short_summary == msg
        assert f.message == msg

    def test_long_multiline_text(self):
        msg = """发现以下问题：
1. 角色定义缺失
2. 描述过于模糊

建议补充角色定义段落。"""
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            _DIAG_TEMPLATES, "bot1", msg,
        )
        assert len(findings) == 1
        f = findings[0]
        assert f.short_summary == "发现以下问题："
        assert f.message == "1. 角色定义缺失\n2. 描述过于模糊\n\n建议补充角色定义段落。"

    def test_no_issue_marker_returns_empty(self):
        for marker in ["无问题", "没有问题", "未发现问题", "no issues found", "pass", "OK"]:
            findings = parse_diagnostic_response(
                _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
                _DIAG_TEMPLATES, "bot1", marker,
            )
            assert findings == [], f"Expected empty for marker: {marker}"

    def test_llm_disabled_returns_llm01(self):
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            _DIAG_TEMPLATES, "bot1", "[llm disabled]",
        )
        assert len(findings) == 1
        assert findings[0].rule_id == "LLM01"
        assert findings[0].severity == Severity.INFO
        assert findings[0].file_type == _DIAG_FILE

    def test_empty_string_returns_llm01(self):
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            _DIAG_TEMPLATES, "bot1", "",
        )
        assert len(findings) == 1
        assert findings[0].rule_id == "LLM01"

    def test_whitespace_only_returns_llm01(self):
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            _DIAG_TEMPLATES, "bot1", "   \n  ",
        )
        assert len(findings) == 1
        assert findings[0].rule_id == "LLM01"

    def test_literal_double_quotes_returns_llm01(self):
        """LLM sometimes returns "" (two double-quote chars) as response."""
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            _DIAG_TEMPLATES, "bot1", '""',
        )
        assert len(findings) == 1
        assert findings[0].rule_id == "LLM01"

    def test_single_double_quote_returns_finding(self):
        """A lone double-quote char is treated as valid text, not empty."""
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            _DIAG_TEMPLATES, "bot1", '"',
        )
        assert len(findings) == 1
        assert findings[0].rule_id == _DIAG_ID

    def test_quoted_real_message_still_works(self):
        """Quoted real message should strip outer quotes and process normally."""
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            _DIAG_TEMPLATES, "bot1", '"角色定义过于简短"',
        )
        assert len(findings) == 1
        assert findings[0].rule_id == _DIAG_ID
        assert findings[0].message == "角色定义过于简短"

    def test_severity_critical(self):
        findings = parse_diagnostic_response(
            "D-SAFETY-001", "SAFETY.md 安全约束诊断", "critical", "SAFETY.md",
            [2222], "bot1", "安全约束为空，Bot 缺乏安全防护",
        )
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL

    def test_severity_info(self):
        findings = parse_diagnostic_response(
            "D-SOUL-001", "SOUL.md 人格描述诊断", "info", "SOUL.md",
            [3333], "bot1", "建议补充语气风格描述",
        )
        assert len(findings) == 1
        assert findings[0].severity == Severity.INFO

    def test_invalid_severity_defaults_to_info(self):
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, "bad-severity", _DIAG_FILE,
            _DIAG_TEMPLATES, "bot1", "some issue",
        )
        assert len(findings) == 1
        assert findings[0].severity == Severity.INFO

    def test_all_fields_from_diagnostic(self):
        """rule_id, rule_name, severity, file_type all come from diagnostic, not LLM."""
        findings = parse_diagnostic_response(
            "D-AGENTS-002", "AGENTS.md 结构完整性", "warning", "AGENTS.md",
            [1111], "bot1", "缺少关键段落",
        )
        f = findings[0]
        assert f.rule_id == "D-AGENTS-002"
        assert f.rule_name == "AGENTS.md 结构完整性"
        assert f.severity == Severity.WARNING
        assert f.file_type == "AGENTS.md"

    def test_suggested_template_ids_forwarded(self):
        """suggested_template_ids from diagnostic is forwarded to Finding (score > 0 and < 80)."""
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            [1111, 2222], "bot1", "some issue\n[SCORE:60]",
        )
        assert findings[0].score == 60
        assert findings[0].suggested_template_ids == [1111, 2222]

    def test_suggested_template_ids_default_empty(self):
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            None, "bot1", "some issue",
        )
        assert findings[0].suggested_template_ids == []

    def test_suggested_template_names_removed(self):
        """Finding no longer has suggested_template_names field (unified to ids)."""
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            _DIAG_TEMPLATES, "bot1", "some issue",
        )
        assert not hasattr(findings[0], "suggested_template_names")

    def test_pass_score_clears_template_ids(self):
        """When score >= 80 (pass), suggested_template_ids should be cleared."""
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            [1111, 2222], "bot1", "轻微建议\n[SCORE:90]",
        )
        assert len(findings) == 1
        assert findings[0].score == 90
        assert findings[0].suggested_template_ids == []

    def test_pass_score_81_clears_template_ids(self):
        """Score exactly 81 (just above threshold) also clears template ids."""
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            [1111], "bot1", "小问题\n[SCORE:81]",
        )
        assert findings[0].score == 81
        assert findings[0].suggested_template_ids == []

    def test_pass_score_80_clears_template_ids(self):
        """Score exactly 80 (at threshold) also clears template ids."""
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            [1111], "bot1", "小问题\n[SCORE:80]",
        )
        assert findings[0].score == 80
        assert findings[0].suggested_template_ids == []

    def test_warning_score_keeps_template_ids(self):
        """When score < 80 (warning/fail), suggested_template_ids are preserved."""
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            [1111, 2222], "bot1", "需要修复\n[SCORE:60]",
        )
        assert findings[0].score == 60
        assert findings[0].suggested_template_ids == [1111, 2222]

    def test_zero_score_clears_template_ids(self):
        """When no score tag (score=0, check failed), template ids should be cleared."""
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            [1111], "bot1", "角色定义缺失",
        )
        assert findings[0].score == 0
        assert findings[0].suggested_template_ids == []

    def test_result_no_score_is_error_critical(self):
        """No [SCORE] tag → score=0 → result is 'error' regardless of severity."""
        findings = parse_diagnostic_response(
            "D-SAFETY-001", "SAFETY.md 安全约束诊断", "critical", "SAFETY.md",
            [2222], "bot1", "安全约束为空",
        )
        assert findings[0].score == 0
        assert findings[0].result == "error"

    def test_result_no_score_is_error_warning(self):
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            _DIAG_TEMPLATES, "bot1", "some issue",
        )
        assert findings[0].score == 0
        assert findings[0].result == "error"

    def test_result_no_score_is_error_info(self):
        findings = parse_diagnostic_response(
            "D-SOUL-001", "SOUL.md 人格描述诊断", "info", "SOUL.md",
            [3333], "bot1", "建议补充语气风格描述",
        )
        assert findings[0].score == 0
        assert findings[0].result == "error"

    def test_result_llm_disabled_is_error(self):
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            _DIAG_TEMPLATES, "bot1", "[llm disabled]",
        )
        assert findings[0].result == "error"

    def test_result_score_overrides_severity_critical_pass(self):
        """D-TOOLS-001 scenario: severity=critical, score=88 → result should be 'pass'."""
        findings = parse_diagnostic_response(
            "D-TOOLS-001", "工具调用原则诊断", "critical", "TOOLS.md",
            [], "bot1", "缺失工具签名翻译规则\n[SCORE:88]",
        )
        assert findings[0].score == 88
        assert findings[0].result == "pass"
        assert findings[0].severity == Severity.CRITICAL

    def test_result_score_overrides_severity_warning_fail(self):
        """severity=warning, score=50 → result should be 'fail' (score-based)."""
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            _DIAG_TEMPLATES, "bot1", "问题很多\n[SCORE:50]",
        )
        assert findings[0].score == 50
        assert findings[0].result == "fail"

    def test_result_score_overrides_severity_info_warning(self):
        """severity=info, score=65 → result should be 'warning' (score-based)."""
        findings = parse_diagnostic_response(
            "D-SOUL-001", "SOUL.md 人格描述诊断", "info", "SOUL.md",
            [3333], "bot1", "建议补充详细\n[SCORE:65]",
        )
        assert findings[0].score == 65
        assert findings[0].result == "warning"

    def test_result_score_80_is_pass(self):
        """severity=critical, score=80 → result should be 'pass' (score >= 80)."""
        findings = parse_diagnostic_response(
            "D-SAFETY-001", "SAFETY.md 安全约束诊断", "critical", "SAFETY.md",
            [], "bot1", "轻微建议\n[SCORE:80]",
        )
        assert findings[0].score == 80
        assert findings[0].result == "pass"

    def test_result_no_score_is_error(self):
        """No [SCORE] tag → score=0 → result is 'error' (LLM failed, not severity-based)."""
        findings = parse_diagnostic_response(
            "D-SAFETY-001", "SAFETY.md 安全约束诊断", "critical", "SAFETY.md",
            [], "bot1", "安全约束为空",
        )
        assert findings[0].score == 0
        assert findings[0].result == "error"


class TestBuildGroupedFindings:
    """Test _serialize_findings and _parse_db_findings helpers from router."""

    def test_serialize_findings_grouped_format(self):
        import json
        from agentclaw.community.adapters.http.harness.router import _serialize_findings
        findings = [
            Finding(rule_id="D-AGENTS-001", rule_name="角色定义", severity=Severity.WARNING,
                    file_type="AGENTS.md", message="角色定义过于简短", short_summary="角色定义过简",
                    score=70, suggested_template_ids=[1111]),
            Finding(rule_id="D-AGENTS-002", rule_name="结构完整性", severity=Severity.CRITICAL,
                    file_type="AGENTS.md", message="缺少关键段落", short_summary="段落缺失",
                    score=40, suggested_template_ids=[1111, 2222]),
            Finding(rule_id="D-SAFETY-001", rule_name="安全约束", severity=Severity.WARNING,
                    file_type="SAFETY.md", message="安全约束模糊", short_summary="约束模糊",
                    score=65, suggested_template_ids=[3333]),
        ]
        result = json.loads(_serialize_findings(findings))
        assert len(result) == 2
        agents = [g for g in result if g["check_item"] == "AGENTS.md"][0]
        safety = [g for g in result if g["check_item"] == "SAFETY.md"][0]
        assert len(agents["finding_details"]) == 2
        assert len(safety["finding_details"]) == 1
        assert agents["all_patch_id_list"] == []
        assert agents["finding_details"][0]["risk_level"] == "warning"
        assert agents["finding_details"][1]["risk_level"] == "critical"
        assert agents["finding_details"][0]["suggested_template_ids"] == [1111]
        assert agents["finding_details"][1]["suggested_template_ids"] == [1111, 2222]
        assert safety["finding_details"][0]["suggested_template_ids"] == [3333]
        assert agents["finding_details"][0]["patch_id_list"] == []
        assert agents["finding_details"][0]["rule_id"] == "D-AGENTS-001"
        assert agents["finding_details"][0]["result"] == "warning"  # score=70
        assert agents["finding_details"][1]["rule_id"] == "D-AGENTS-002"
        assert agents["finding_details"][1]["result"] == "fail"  # score=40
        assert safety["finding_details"][0]["result"] == "warning"  # score=65

    def test_parse_db_findings_grouped_format(self):
        from agentclaw.community.adapters.http.harness.router import _parse_db_findings
        grouped = [
            {
                "check_item": "AGENTS.md",
                "all_patch_id_list": [],
                "finding_details": [
                    {"name": "角色定义", "message": "过于简短", "risk_level": "warning",
                     "result": "warning", "suggested_template_ids": [1111], "patch_id_list": []},
                    {"name": "结构完整性", "message": "缺少段落", "risk_level": "critical",
                     "result": "fail", "suggested_template_ids": [], "patch_id_list": []},
                ],
            },
        ]
        items = _parse_db_findings(grouped)
        assert len(items) == 2
        assert items[0].name == "角色定义"
        assert items[0].detail == "过于简短"
        assert items[0].risk_level == "warning"
        assert items[0].result == "warning"
        assert items[1].name == "结构完整性"
        assert items[1].risk_level == "critical"
        assert items[1].result == "fail"

    def test_parse_db_findings_old_flat_format(self):
        from agentclaw.community.adapters.http.harness.router import _parse_db_findings
        flat = [
            {"rule_id": "D-AGENTS-001", "rule_name": "角色定义", "severity": "warning",
             "file_type": "AGENTS.md", "message": "过于简短"},
        ]
        items = _parse_db_findings(flat)
        assert len(items) == 1
        assert items[0].name == "角色定义"
        assert items[0].risk_level == "warning"

    def test_serialize_empty_findings(self):
        import json
        from agentclaw.community.adapters.http.harness.router import _serialize_findings
        result = json.loads(_serialize_findings([]))
        assert result == []

    def test_parse_db_findings_empty(self):
        from agentclaw.community.adapters.http.harness.router import _parse_db_findings
        assert _parse_db_findings([]) == []

    def test_result_detail_from_short_summaries(self):
        """Verify result_detail joins short_summary with semicolons."""
        findings = [
            Finding(rule_id="D-AGENTS-001", rule_name="角色定义", severity=Severity.WARNING,
                    file_type="AGENTS.md", message="角色定义过于简短", short_summary="角色定义过简"),
            Finding(rule_id="D-AGENTS-002", rule_name="结构完整性", severity=Severity.CRITICAL,
                    file_type="AGENTS.md", message="缺少关键段落", short_summary="段落缺失"),
        ]
        summaries = ";".join(f.short_summary for f in findings if f.short_summary)
        assert summaries == "角色定义过简;段落缺失"


class TestExtractScore:
    """Test _extract_score from LLM response text."""

    def test_score_extracted_from_bracket(self):
        from agentclaw.community.core.harness.services.llm_response_parser import _extract_score
        score, cleaned = _extract_score("角色定义缺失\n建议补充\n[SCORE:75]")
        assert score == 75
        assert "[SCORE:75]" not in cleaned
        assert "角色定义缺失" in cleaned

    def test_score_extracted_chinese_colon(self):
        from agentclaw.community.core.harness.services.llm_response_parser import _extract_score
        score, _ = _extract_score("问题发现\n[SCORE：60]")
        assert score == 60

    def test_score_clamped_to_100(self):
        from agentclaw.community.core.harness.services.llm_response_parser import _extract_score
        score, _ = _extract_score("完美\n[SCORE:150]")
        assert score == 100

    def test_score_clamped_to_0(self):
        from agentclaw.community.core.harness.services.llm_response_parser import _extract_score
        score, _ = _extract_score("严重缺失\n[SCORE:-10]")
        assert score == 0

    def test_no_score_returns_0(self):
        from agentclaw.community.core.harness.services.llm_response_parser import _extract_score
        score, cleaned = _extract_score("角色定义缺失\n建议补充")
        assert score == 0
        assert cleaned == "角色定义缺失\n建议补充"

    def test_parse_diagnostic_response_includes_score(self):
        msg = "角色定义缺失\n建议补充\n[SCORE:75]"
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            _DIAG_TEMPLATES, "bot1", msg,
        )
        assert len(findings) == 1
        assert findings[0].score == 75
        # Score marker should be stripped from message
        assert "[SCORE:75]" not in findings[0].message

    def test_parse_diagnostic_response_no_issue_no_score(self):
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            _DIAG_TEMPLATES, "bot1", "无问题",
        )
        assert findings == []

    def test_parse_diagnostic_response_llm_disabled_score_0(self):
        findings = parse_diagnostic_response(
            _DIAG_ID, _DIAG_NAME, _DIAG_SEVERITY, _DIAG_FILE,
            _DIAG_TEMPLATES, "bot1", "[llm disabled]",
        )
        assert findings[0].score == 0


class TestComputeFileResultAndScore:
    """Test _compute_file_result_and_score from router."""

    def test_empty_findings_pass_100(self):
        from agentclaw.community.adapters.http.harness.router import _compute_file_result_and_score
        result, score = _compute_file_result_and_score([])
        assert result == "pass"
        assert score == 100

    def test_score_based_result_pass(self):
        from agentclaw.community.adapters.http.harness.router import _compute_file_result_and_score
        findings = [
            {"result": "warning", "score": 90},
            {"result": "warning", "score": 85},
        ]
        result, score = _compute_file_result_and_score(findings)
        assert result == "pass"
        assert score == 87

    def test_score_based_result_warning(self):
        from agentclaw.community.adapters.http.harness.router import _compute_file_result_and_score
        findings = [
            {"result": "warning", "score": 70},
            {"result": "warning", "score": 55},
        ]
        result, score = _compute_file_result_and_score(findings)
        assert result == "warning"
        assert score == 62

    def test_score_based_result_fail(self):
        from agentclaw.community.adapters.http.harness.router import _compute_file_result_and_score
        findings = [
            {"result": "fail", "score": 40},
            {"result": "warning", "score": 50},
        ]
        result, score = _compute_file_result_and_score(findings)
        assert result == "fail"
        assert score == 45

    def test_fallback_result_from_severity(self):
        from agentclaw.community.adapters.http.harness.router import _compute_file_result_and_score
        findings = [
            {"severity": "critical", "result": "fail"},
            {"severity": "warning", "result": "warning"},
        ]
        result, score = _compute_file_result_and_score(findings)
        assert result == "fail"
        assert score is None

    def test_fallback_result_from_result_field(self):
        from agentclaw.community.adapters.http.harness.router import _compute_file_result_and_score
        findings = [
            {"result": "warning"},
        ]
        result, score = _compute_file_result_and_score(findings)
        assert result == "warning"
        assert score is None

    def test_fallback_pass_when_no_issues(self):
        from agentclaw.community.adapters.http.harness.router import _compute_file_result_and_score
        findings = [
            {"severity": "info", "result": "pass"},
        ]
        result, score = _compute_file_result_and_score(findings)
        assert result == "pass"
        assert score is None

    def test_score_zero_returns_error(self):
        from agentclaw.community.adapters.http.harness.router import _compute_file_result_and_score
        findings = [
            {"result": "warning", "score": 0},
        ]
        result, score = _compute_file_result_and_score(findings)
        assert result == "error"
        assert score == 0

    def test_score_zero_mixed_returns_error(self):
        """When all findings have score=0, file result is error."""
        from agentclaw.community.adapters.http.harness.router import _compute_file_result_and_score
        findings = [
            {"result": "warning", "score": 0},
            {"result": "fail", "score": 0},
        ]
        result, score = _compute_file_result_and_score(findings)
        assert result == "error"
        assert score == 0

    def test_score_zero_with_positive_mixed(self):
        """When file has both score=0 and positive scores, exclude zero and average positives only."""
        from agentclaw.community.adapters.http.harness.router import _compute_file_result_and_score
        findings = [
            {"result": "warning", "score": 0},
            {"result": "warning", "score": 90},
        ]
        result, score = _compute_file_result_and_score(findings)
        # positive_scores=[90], avg=90 → score_to_result(90)="pass"
        assert result == "pass"
        assert score == 90