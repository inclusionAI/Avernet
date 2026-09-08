
import pytest

from engine.community.plugins.claude_code import _skills as claude_skills
from engine.community.plugins.openclaw import _skills as openclaw_skills


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "port_type", "engine"),
    [
        (openclaw_skills, openclaw_skills._SkillsPortMixin, "openclaw"),
        (claude_skills, claude_skills._SkillsPortMixin, "claude_code"),
    ],
)
async def test_concrete_ports_delegate_daily_apply_to_shared_logical_contract(
    monkeypatch,
    module,
    port_type,
    engine,
) -> None:
    calls = []

    async def apply(**kwargs):
        calls.append(kwargs)
        return {
            "status": "CONVERGED",
            "items": [],
            "issues": [],
            "evidence": {},
        }

    monkeypatch.setattr(module, "apply_logical_mapping_request", apply)
    port = port_type()

    result = await port.apply_pool_mappings(
        {
            "mappings": [],
            "retired_mappings": [],
            "source_layout": "legacy",
        }
    )

    assert result["status"] == "CONVERGED"
    assert calls[0]["engine"] == engine
    assert calls[0]["params"] == {
        "mappings": [],
        "retired_mappings": [],
        "source_layout": "legacy",
    }
