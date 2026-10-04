import sys
from pathlib import Path

BCS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BCS_ROOT))

from scripts.adapters_endpoint_coverage import build_template_matchers, match_hit, parse_hits, parse_router


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


def test_router_inventory_includes_all_mounted_draft_operations():
    endpoints = parse_router(str(BCS_ROOT / "crates/adapters/http/bcs-http/src/router.rs"))
    draft = [e for e in endpoints if e.path.startswith("/collaboration/drafts/")]
    base = "/collaboration/drafts/{id}"
    revision = base + "/revisions/{revision}"
    assert {e.key() for e in draft} == {
        ("PUT", base), ("GET", base), ("DELETE", base),
        ("POST", base + "/revisions"), ("GET", revision),
        ("POST", revision + "/validate"), ("GET", revision + "/reference"),
        ("DELETE", revision + "/share"),
    }
    assert len(draft) == 8
    matchers = build_template_matchers(endpoints)
    for endpoint in draft:
        concrete = endpoint.path.replace("{id}", "sample-id").replace("{revision}", "1")
        assert match_hit(endpoint.method, concrete, matchers) == endpoint.path


def test_router_inventory_ignores_unmounted_and_commented_modules(tmp_path):
    (tmp_path / "routes").mkdir()
    router = tmp_path / "router.rs"
    router.write_text('Router::new().merge(routes::mounted::router())\n'
                      '// .merge(routes::missing::router())\n')
    (tmp_path / "routes/mounted.rs").write_text('Router::new().route("/mounted", get(read))')
    (tmp_path / "routes/unused.rs").write_text('Router::new().route("/unused", get(read))')
    assert [e.key() for e in parse_router(str(router))] == [("GET", "/mounted")]
