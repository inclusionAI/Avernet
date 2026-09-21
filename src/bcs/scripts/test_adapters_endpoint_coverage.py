import sys
from pathlib import Path

BCS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BCS_ROOT))

from scripts.adapters_endpoint_coverage import parse_hits


def test_parse_hits_prefers_structured_access_log_when_debug_marker_interleaves(tmp_path):
    log = tmp_path / "bcs.log"
    log.write_text(
        "\x1b[2m[→BCS] 2026-09-20 09:46:14 INFO another_writer\n"
        "GET /providers/prv_example\x1b[0m\n"
        "2026-09-20 09:46:14 INFO bcs_http_access: http.request.started "
        "request_id=req-1 route=/providers/{provider_id} method=GET\n",
        encoding="utf-8",
    )

    assert parse_hits(str(log)) == [("GET", "/providers/{provider_id}")]


def test_parse_hits_falls_back_to_legacy_debug_markers(tmp_path):
    log = tmp_path / "bcs.log"
    log.write_text(
        "\x1b[2m[→BCS] GET /providers/prv_example?include=bots\x1b[0m\n",
        encoding="utf-8",
    )

    assert parse_hits(str(log)) == [("GET", "/providers/prv_example")]
