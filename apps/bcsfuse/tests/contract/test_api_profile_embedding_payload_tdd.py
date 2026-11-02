"""API profile contents must remain indexable and keyword-searchable."""

from app.domain.models.worker_profile_content import WorkerProfileContent
from app.domain.services.profile_fragment_decomposer import ProfileFragmentDecomposer
from app.infra.worker_profiles.sources.api_profile_source import APIProfileSource


def _content() -> WorkerProfileContent:
    return WorkerProfileContent(
        worker_id="load20261102_0006",
        profile_id="default",
        soul_md=(
            "Agent load-0006 is a payment graph analyst. It specializes in database, "
            "high availability architecture, production incident response, and "
            "large-scale distributed workload optimization."
        ),
        agents_md="Use web search and database analysis tools.",
        tools_md="Search tools: code search, documentation search.",
    )


def test_api_core_fragments_declare_indexable_embedding_fields():
    source = APIProfileSource(content_store=object())
    fragments = source._extract_fragments(_content())

    by_file = {fragment.filename: fragment for fragment in fragments}
    assert by_file["SOUL.md"].metadata["embedding_field"] == "profile"
    assert by_file["AGENTS.md"].metadata["embedding_field"] == "capabilities"
    assert by_file["TOOLS.md"].metadata["embedding_field"] == "capabilities"


def test_api_profile_decomposition_keeps_real_rich_text():
    source = APIProfileSource(content_store=object())
    profile = source._convert_to_worker_profile(_content(), "full_dict")
    fragments = ProfileFragmentDecomposer().decompose(profile)

    by_type = {fragment.fragment_type: fragment for fragment in fragments}
    capability_text = " ".join(
        fragment.content for fragment in fragments if fragment.fragment_type == "capabilities"
    )
    assert "payment graph analyst" in by_type["profile"].content
    assert "high availability architecture" in by_type["full"].content
    assert "web search" in capability_text
    assert "database analysis tools" in capability_text


def test_empty_api_profile_keeps_identifier_only_fallback():
    source = APIProfileSource(content_store=object())
    profile = source._convert_to_worker_profile(
        WorkerProfileContent(worker_id="empty-bot", profile_id="default"),
        "full_dict",
    )
    fragments = ProfileFragmentDecomposer().decompose(profile)

    assert [fragment.fragment_type for fragment in fragments] == ["full"]
    assert fragments[0].content == "worker_id: empty-bot\nProfile: empty-bot:default"
