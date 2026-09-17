"""Tests for `scripts/gen_capability_matrix.py` — the capability-matrix generator.

Two things are worth guarding:

1. the static (ast) collector agrees with what the engines actually declare at
   runtime, so a dynamically-built declaration can never be silently missed;
2. the checked-in `docs/engine-capability-matrix.md` is regenerated whenever an
   engine's capabilities change — the drift gate the doc exists for.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# this file: .../src/engine/src/engine/community/tests/tools/<f>.py
# parents[5] == .../src/engine
ENGINE_DIR = Path(__file__).resolve().parents[5]
SCRIPT = ENGINE_DIR / "scripts" / "gen_capability_matrix.py"
MATRIX_DOC = ENGINE_DIR / "docs" / "engine-capability-matrix.md"


def _load_script():
    """Import the script by path — it lives outside the installed package."""
    spec = importlib.util.spec_from_file_location("gen_capability_matrix", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registering before exec so @dataclass can resolve the module namespace.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script()


@pytest.fixture(scope="module")
def static_matrix(script):
    return script.collect_static()


def test_script_exists():
    assert SCRIPT.is_file(), f"generator script missing: {SCRIPT}"


def test_static_vocabulary_matches_capability_enum(static_matrix):
    """Row vocabulary is the Capability enum, in declaration order."""
    from engine.community.core.engine.capability import Capability

    assert static_matrix.capabilities == [c.value for c in Capability]


def test_static_scan_finds_bundled_engines(static_matrix):
    names = {engine.name for engine in static_matrix.engines}
    assert {"openclaw", "claude_code"} <= names


def test_static_scan_reads_inline_declaration(static_matrix):
    """openclaw declares the literal inline in the class body."""
    openclaw = next(e for e in static_matrix.engines if e.name == "openclaw")
    assert openclaw.version == "1.0.0"
    assert "session.create" in openclaw.supported
    assert openclaw.status("mcp.start") == "limited"
    assert openclaw.note("mcp.start")
    assert openclaw.status("skills.execute") == "unsupported"


def test_static_scan_resolves_module_level_constant(static_matrix):
    """claude_code assigns `_CAPABILITIES` from a module-level constant."""
    claude_code = next(e for e in static_matrix.engines if e.name == "claude_code")
    assert "chat.stream" in claude_code.supported
    assert claude_code.status("session.create") == "limited"
    # Implicit string concatenation across lines must survive the parse.
    assert "sessionKey" in claude_code.note("session.create")


def test_static_matches_runtime_declarations(script):
    """The ast scan must agree with the declarations the registry serves."""
    try:
        runtime = script.collect_runtime()
    except ImportError as exc:  # engine deps not installed in this checkout
        pytest.skip(f"runtime collection unavailable: {exc}")

    static = {e.name: e for e in script.collect_static().engines}
    for engine in runtime.engines:
        parsed = static.get(engine.name)
        assert parsed is not None, f"static scan missed engine {engine.name!r}"
        assert parsed.supported == engine.supported
        assert parsed.limited == engine.limited
        assert parsed.fallback == engine.fallback


def test_render_markdown_has_table_legend_and_notes(script, static_matrix):
    rendered = script.render_markdown(static_matrix)
    assert "| Domain | Capability |" in rendered
    assert "**Legend:**" in rendered
    assert "## Notes" in rendered
    # Every capability gets exactly one row.
    for capability in static_matrix.capabilities:
        assert f"| `{capability}` |" in rendered


def test_render_markdown_can_omit_notes(script, static_matrix):
    assert "## Notes" not in script.render_markdown(static_matrix, notes=False)


def test_render_json_is_machine_readable(script, static_matrix):
    payload = json.loads(script.render_json(static_matrix))
    assert payload["capabilities"] == static_matrix.capabilities
    statuses = {
        status
        for row in payload["matrix"].values()
        for status in row.values()
    }
    assert statuses <= {"supported", "limited", "fallback", "unsupported"}
    assert payload["matrix"]["mcp.start"]["openclaw"] == "limited"


def test_render_csv_is_long_format(script, static_matrix):
    lines = script.render_csv(static_matrix).splitlines()
    assert lines[0] == "domain,capability,engine,status,note"
    expected = len(static_matrix.capabilities) * len(static_matrix.engines)
    assert len(lines) == expected + 1


def test_filter_engines_rejects_unknown_names(script, static_matrix):
    only = script.filter_engines(static_matrix, ["openclaw"])
    assert [e.name for e in only.engines] == ["openclaw"]
    with pytest.raises(SystemExit):
        script.filter_engines(static_matrix, ["no_such_engine"])


def test_checked_in_matrix_doc_is_up_to_date(script):
    """Drift gate: regenerate the doc when an engine's capabilities change.

    Fix with:
        python src/engine/scripts/gen_capability_matrix.py \\
            --source static -o src/engine/docs/engine-capability-matrix.md
    """
    assert MATRIX_DOC.is_file(), f"generated matrix doc missing: {MATRIX_DOC}"
    exit_code = script.main(["--source", "static", "--check", str(MATRIX_DOC)])
    assert exit_code == 0, "engine-capability-matrix.md is stale; regenerate it"
