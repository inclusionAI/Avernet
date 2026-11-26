from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "verify_singlebox_coverage_artifacts.py"
SPEC = importlib.util.spec_from_file_location("coverage_artifact_verifier", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        bcs_line_min=40.0,
        bcs_method_min=36.0,
        bcs_router_min=100.0,
        bcs_cli_min=100.0,
    )


def _write_bcs_reports(reports_dir: Path, endpoint_overall: str) -> None:
    bcs_dir = reports_dir / "bcs"
    bcs_dir.mkdir(parents=True)
    (bcs_dir / "oauth_e2e.json").write_text(json.dumps({
        "status": "passed",
        "scenarios": ["mixed-sqlite", "mixed-memory", "compatibility-and-chain-selection", "pending-capacity", "startup-validation"],
        "checks": [{"path": "/auth/url", "status": 200}],
        "provider_calls": {"authorize": 1, "exchange": 1, "userinfo": 1},
    }))
    (bcs_dir / "summary.json").write_text(
        '{"data":[{"totals":{"lines":{"covered":45,"count":100},'
        '"functions":{"covered":40,"count":100}}}]}\n',
        encoding="utf-8",
    )
    (bcs_dir / "endpoint_coverage.xml").write_text(
        f"<endpointCoverage>{endpoint_overall}</endpointCoverage>\n",
        encoding="utf-8",
    )
    (bcs_dir / "cli_command_coverage.txt").write_text(
        "bcs-cli leaf command coverage: 8 / 8 (100.0%)\n",
        encoding="utf-8",
    )


def test_router_gate_recomputes_ratio_instead_of_trusting_percent(tmp_path: Path):
    _write_bcs_reports(
        tmp_path,
        '<overall covered="0" total="0" uncovered="0" percent="100.0"/>',
    )
    errors: list[str] = []

    metrics = VERIFIER.validate_bcs_artifacts(tmp_path, _args(), errors)

    assert metrics[2] == 0.0
    assert "BCS Router API coverage has no endpoint denominator" in errors
    assert "BCS Router API coverage 0.00% < 100.00%" in errors


def test_router_gate_uses_covered_and_total_fields(tmp_path: Path):
    _write_bcs_reports(
        tmp_path,
        '<overall covered="3" total="4" uncovered="1" percent="100.0"/>',
    )
    errors: list[str] = []

    metrics = VERIFIER.validate_bcs_artifacts(tmp_path, _args(), errors)

    assert metrics[2] == 75.0
    assert "BCS Router API coverage 75.00% < 100.00%" in errors


def test_malformed_llvm_metric_is_reported_without_crashing(tmp_path: Path):
    _write_bcs_reports(
        tmp_path,
        '<overall covered="4" total="4" uncovered="0" percent="100.0"/>',
    )
    (tmp_path / "bcs/summary.json").write_text(
        '{"data":[{"totals":{"lines":[],"functions":40}}]}\n',
        encoding="utf-8",
    )
    errors: list[str] = []

    metrics = VERIFIER.validate_bcs_artifacts(tmp_path, _args(), errors)

    assert metrics[:2] == (0.0, 0.0)
    assert "BCS lines metric must be an object" in errors
    assert "BCS functions metric must be an object" in errors


def test_oauth_artifact_requires_live_scenarios_and_provider_calls(tmp_path: Path):
    _write_bcs_reports(tmp_path, '<overall covered="4" total="4"/>')
    errors = []
    VERIFIER.validate_oauth_e2e(tmp_path, errors)
    assert not errors
    path = tmp_path / "bcs/oauth_e2e.json"
    valid = json.loads(path.read_text())
    for field, value in [("status", "failed"), ("scenarios", []), ("checks", []),
                         ("provider_calls", {}), ("provider_calls", {"authorize": 1, "exchange": 0, "userinfo": 1})]:
        path.write_text(json.dumps({**valid, field: value}))
        errors = []
        VERIFIER.validate_oauth_e2e(tmp_path, errors)
        assert errors, field
    path.unlink()
    errors = []
    VERIFIER.validate_oauth_e2e(tmp_path, errors)
    assert any("missing required JSON" in error for error in errors)


def test_default_bcs_thresholds_are_inclusive(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("sys.argv", [str(SCRIPT)])
    args = VERIFIER.parse_args()
    assert (args.bcs_line_min, args.bcs_method_min) == (54.0, 48.0)
    assert (args.bcs_router_min, args.bcs_cli_min) == (100.0, 100.0)
    _write_bcs_reports(
        tmp_path,
        '<overall covered="4" total="4" uncovered="0" percent="100.0"/>',
    )
    for lines, methods, expected_errors in [(5400, 4800, []), (5399, 4799, [
        "BCS line coverage 53.99% < 54.00%",
        "BCS method coverage 47.99% < 48.00%",
    ])]:
        (tmp_path / "bcs/summary.json").write_text(json.dumps({"data": [{"totals": {
            "lines": {"covered": lines, "count": 10000},
            "functions": {"covered": methods, "count": 10000},
        }}]}), encoding="utf-8")
        errors: list[str] = []
        VERIFIER.validate_bcs_artifacts(tmp_path, args, errors)
        assert errors == expected_errors
