"""Index profile identity without replacing names or creating fake capabilities."""

import pytest

from src.domain.models.context_fragment import ContextFragment
from src.domain.models.worker_profile import WorkerProfile
from src.domain.services.profile_fragment_decomposer import ProfileFragmentDecomposer


@pytest.mark.parametrize("content", ["名称: Example Bot\nPython development", "名称: Example Bot\n" + "x" * 6000], ids=["short", "long"])
def test_worker_identifier_in_profile_and_full_fragments(content):
    profile = WorkerProfile(
        staff_id="test-bot:12345", profile_id="default", profile_type="bot", source_root="api",
        context_fragments=[ContextFragment(
            filename="profile", content=content, source_path="api://test-bot/default",
            metadata={"embedding_field": "profile"},
        )],
    )
    fragments = {f.fragment_type: f for f in ProfileFragmentDecomposer().decompose(profile)}
    for kind in ("profile", "full"):
        assert "worker_id: test-bot:12345" in fragments[kind].content
        assert "名称: Example Bot" in fragments[kind].content
    assert "capabilities" not in fragments
    assert profile.context_fragments[0].content == content


def test_reindexing_same_profile_is_stable():
    profile = WorkerProfile(staff_id="test-bot:12345", profile_id="default", profile_type="bot", source_root="api")
    decomposer = ProfileFragmentDecomposer()
    first = decomposer.decompose(profile)
    second = decomposer.decompose(profile)
    assert first == second
    assert "worker_id: test-bot:12345" in first[0].content
