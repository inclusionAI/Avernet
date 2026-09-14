import importlib.util
import json
import sys
from pathlib import Path


HANDLER = (
    Path(__file__).parents[1]
    / "clawevolve-workflow/scripts/handlers/clawevolve_optimize_run.py"
)
sys.path.insert(0, str(HANDLER.parent))
SPEC = importlib.util.spec_from_file_location("optimize_agent_archive", HANDLER)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_archive_openclaw_agent_session_keeps_envelope_and_transcript(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text('{"role":"assistant","content":"done"}\n', encoding="utf-8")
    result = {
        "agentId": "clawevolve-tune-ev-1",
        "sessionId": "session-1",
        "agentJson": {
            "status": "ok",
            "result": {"meta": {"agentMeta": {"sessionFile": str(source)}}},
        },
    }

    archive = MODULE._archive_openclaw_agent_session(result, tmp_path / "archive")

    assert archive["transcript_archived"] is True
    assert Path(archive["transcript_jsonl"]).read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    envelope = json.loads(Path(archive["agent_result_json"]).read_text(encoding="utf-8"))
    assert envelope["status"] == "ok"
