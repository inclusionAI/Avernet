"""Tests for adapters HTTP endpoint coverage tooling.

Covers the two self-check seams of scripts/adapters_endpoint_coverage.py:

1. hit extraction (`parse_hits`) from structured access logs and legacy
   debug markers (interleaved-concurrency-safe order);
2. endpoint discovery (`parse_router`) and COUNTING of routes registered
   OUTSIDE bcs-http's router.rs, added for the credential-gated team-manager
   sources slice (plan Task 13, carried to Task 20):
   `crates/adapters/http/bcs-api-http/src/v1/internal/routes/team_manager_sources.rs`
   mounted at `/api/v1/bots` via axum `.nest` — the normal PUT snapshot
   sync AND the internal POST/DELETE single-member repairs are all real
   endpoints and must all be discoverable and countable; no manual
   "covered" annotation exists anywhere.
"""

import sys
from pathlib import Path

BCS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BCS_ROOT))

from scripts.adapters_endpoint_coverage import (
    build_template_matchers,
    match_hit,
    parse_hits,
    parse_nested_router,
    parse_router,
)

TEAM_SLICE_ROUTER = (
    BCS_ROOT
    / "crates/adapters/http/bcs-api-http/src/v1/internal/routes/team_manager_sources.rs"
)


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


def test_team_manager_slice_routes_are_discovered():
    """The team slice's three real endpoints parse from the source router:
    the normal PUT snapshot sync plus the two internal single-member
    repairs (POST add-one, DELETE remove-one declared in one chained
    .route block)."""
    endpoints = parse_router(str(TEAM_SLICE_ROUTER))
    keys = sorted((e.method, e.path) for e in endpoints)
    assert keys == sorted(
        [
            ("PUT", "/{bot_id}/manager-sources/teams/{team_id}"),
            ("POST", "/{bot_id}/manager-sources/teams/{team_id}/members"),
            ("DELETE", "/{bot_id}/manager-sources/teams/{team_id}/members"),
        ]
    )
    # Handlers resolve to the real route functions (no synthetic entries).
    handlers = {e.handler for e in endpoints}
    assert "sync_team_manager_sources" in handlers
    assert "repair_add_team_member" in handlers
    assert "repair_remove_team_member" in handlers


def test_team_manager_slice_hits_count_all_three_methods_under_the_mount(tmp_path):
    """Access-log hits on the mounted slice map onto the discovered
    templates — the normal PUT sync and BOTH member repairs (POST/DELETE)
    count; nothing is covered by hand-annotation."""
    log = tmp_path / "bcs.log"
    log.write_text(
        "2026-10-09 10:00:00 INFO bcs_http_access: http.request.started "
        "request_id=req-1 route=/api/v1/bots/{bot_id}/manager-sources/teams/{team_id} "
        "method=PUT\n"
        "2026-10-09 10:00:01 INFO bcs_http_access: http.request.started "
        "request_id=req-2 route=/api/v1/bots/{bot_id}/manager-sources/teams/{team_id}/members "
        "method=POST\n"
        "2026-10-09 10:00:02 INFO bcs_http_access: http.request.started "
        "request_id=req-3 route=/api/v1/bots/{bot_id}/manager-sources/teams/{team_id}/members "
        "method=DELETE\n",
        encoding="utf-8",
    )
    endpoints = parse_nested_router(str(TEAM_SLICE_ROUTER), "/api/v1/bots")
    assert {(e.method, e.path) for e in endpoints} == {
        ("PUT", "/api/v1/bots/{bot_id}/manager-sources/teams/{team_id}"),
        ("POST", "/api/v1/bots/{bot_id}/manager-sources/teams/{team_id}/members"),
        ("DELETE", "/api/v1/bots/{bot_id}/manager-sources/teams/{team_id}/members"),
    }

    matchers = build_template_matchers(endpoints)
    covered = set()
    for method, raw_path in parse_hits(str(log)):
        template = match_hit(method, raw_path, matchers)
        assert template is not None, "unmatched team-slice hit %s %s" % (method, raw_path)
        covered.add((method, template))
    assert covered == {
        ("PUT", "/api/v1/bots/{bot_id}/manager-sources/teams/{team_id}"),
        ("POST", "/api/v1/bots/{bot_id}/manager-sources/teams/{team_id}/members"),
        ("DELETE", "/api/v1/bots/{bot_id}/manager-sources/teams/{team_id}/members"),
    }


def test_team_manager_slice_unnested_templates_do_not_shadow_router_rs(tmp_path):
    """Sanity: without the mount prefix the slice's raw templates are NOT
    the paths the access log reports, so silence in the matcher (not a
    wrong-covered entry) is what a mis-mounted run must produce."""
    endpoints = parse_router(str(TEAM_SLICE_ROUTER))
    matchers = build_template_matchers(endpoints)
    assert (
        match_hit("PUT", "/api/v1/bots/b1/manager-sources/teams/t2", matchers) is None
    )