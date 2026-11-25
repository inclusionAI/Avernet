"""The public consumer must remain deployment independent and KB scoped."""

from pathlib import Path


def test_no_enterprise_dependencies_in_public_management():
    source = Path(__file__).resolve().parents[3] / "src/agentclaw/community"
    roots = [
        source / "adapters/knowledge_platform",
        source / "adapters/http/knowledge_management",
        source / "core/knowledge_management",
    ]
    forbidden = ("agentclaw.corp", "IAM_TOKEN", ".alipay.com", "MTEGID")
    for root in roots:
        for path in root.rglob("*.py"):
            assert not any(value in path.read_text() for value in forbidden), path


def test_unscoped_bot_directory_stays_removed():
    from agentclaw.community.adapters.http.knowledge_management.router import router

    assert all(
        route.path != "/api/knowledge/spaces/{space_id}/bots" for route in router.routes
    )
