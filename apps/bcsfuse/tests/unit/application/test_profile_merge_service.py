"""TC G9 uses current Card data while preserving durable fusion history."""

import logging
from contextlib import closing

import pytest

from app.application.services.bot_fuse.fused_profile_storage_service import (
    FusedProfileStorageService,
)
from app.application.services.bot_fuse.profile_merge_service import ProfileMergeService
from app.domain.models.llm_response import LLMResponse
from app.domain.models.profile_fusion import ConversationTurn, FusionContext
from app.domain.models.worker_profile_content import SkillSet, WorkerProfileContent
from app.infra.adapters.sqlite_fused_profile_store import SQLiteFusedProfileStore
from app.infra.adapters.sqlite_worker_profile_content_store import (
    SQLiteWorkerProfileContentStore,
)
from app.utils.fuse_util import generate_fusion_id, worker_profile_to_dict


class RecordingModel:
    def __init__(self):
        self.requests = []
        self.fail = False

    def generate(self, request):
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("controlled unavailable model")
        return LLMResponse(
            provider_id="local", model_id="local", raw_text="sensitive-generated-response",
            structured_data={"name": "Specialist team", "persona": "Combined release expertise"},
            parse_success=True, latency_ms=1, finish_reason="stop",
        )


@pytest.fixture
def runtime(tmp_path):
    with (
        closing(SQLiteFusedProfileStore(str(tmp_path / "fusion.sqlite"))) as repository,
        closing(SQLiteWorkerProfileContentStore(":memory:")) as profiles,
    ):
        model = RecordingModel()
        service = ProfileMergeService(profiles, model, FusedProfileStorageService(repository))
        yield service, model, repository


def current_profile():
    return WorkerProfileContent(
        worker_id="tc:database:owner", display_name="Database specialist",
        description="sensitive-current-description", contents={"profile": "sensitive-current-profile"},
        skill_sets=[SkillSet(name="sql", description="sensitive-skill-description")],
    )


def fuse(service, profiles, *, refresh=False):
    context = FusionContext(
        group_id="group", driver_bot_id=profiles[0].worker_id, profiles=profiles,
        profiles_dict=[worker_profile_to_dict(p) for p in profiles], refresh=refresh,
    )
    context.fusion_id = generate_fusion_id("bot_profile_fuse", context)
    return service.fuse_profiles(context)


def test_model_receives_current_description_profile_and_skills_without_md(runtime, caplog):
    service, model, _repository = runtime
    profile = current_profile()
    profile.soul_md = "ARCHIVED_SOUL"
    profile.contents.update({"identity.md": "ARCHIVED_IDENTITY", "memory.md": "ARCHIVED_MEMORY"})
    with caplog.at_level(logging.INFO):
        result = fuse(service, [profile])
    request = model.requests[0]
    assert "Database specialist" in request.user_prompt
    for value in (profile.description, profile.contents["profile"], profile.skill_sets[0].description):
        assert value in request.user_prompt
        assert value not in caplog.text
    assert "ARCHIVED_" not in request.user_prompt
    assert "sensitive-generated-response" not in caplog.text
    assert result.fused_profile.source_participants == ["tc:database:owner"]


@pytest.mark.parametrize("field", ["description", "profile", "skill_description"])
def test_current_input_change_creates_new_result_and_keeps_old_history(runtime, field):
    service, model, repository = runtime
    profile = current_profile()
    first = fuse(service, [profile])
    repository.append_turn(first.fusion_id, ConversationTurn(
        turn_index=1, question="original question", answer_content="original answer",
    ))
    if field == "profile":
        profile.contents["profile"] = "Changed generated expertise"
    elif field == "skill_description":
        profile.skill_sets[0].description = "Changed skill scope"
    else:
        profile.description = "Changed current description"
    changed = fuse(service, [profile])
    assert changed.fusion_id != first.fusion_id
    assert not changed.cache_hit
    assert len(model.requests) == 2
    assert repository.find_by_key(first.fusion_id) is not None
    assert repository.get_conversation(first.fusion_id)["turns"][0]["question"] == "original question"
    assert fuse(service, [profile]).cache_hit
    assert len(model.requests) == 2


def test_fallback_keeps_current_description_profile_and_ignores_archived_md(runtime):
    service, model, _repository = runtime
    model.fail = True
    profile = current_profile()
    profile.soul_md = "ARCHIVED_SOUL"
    result = fuse(service, [profile])
    prompt = result.fused_profile.to_system_prompt()
    assert profile.description in prompt
    assert profile.contents["profile"] in prompt
    assert profile.skill_sets[0].description in prompt
    assert "ARCHIVED_SOUL" not in prompt
    assert result.fused_profile.fusion_strategy == "fallback_exception"


def test_archived_md_does_not_affect_deduplication(runtime):
    service, _model, _repository = runtime
    first = current_profile()
    second = first.model_copy(update={"worker_id": "tc:another:owner"})
    baseline = fuse(service, [first, second])
    first.soul_md = second.soul_md = "The same archived soul"
    with_archive = fuse(service, [first, second], refresh=True)
    assert with_archive.fused_profile.dedup_count == baseline.fused_profile.dedup_count
    assert "archived soul" not in with_archive.fused_profile.model_dump_json()


def test_a_queued_sibling_summary_does_not_suppress_profile_model_call(runtime):
    from types import SimpleNamespace
    service, model, _repository = runtime
    # GroupFusionService submits profile and summary work to one bounded pool.
    # A waiting sibling is normal and does not establish model overload.
    service.set_llm_executor(SimpleNamespace(_work_queue=SimpleNamespace(qsize=lambda: 1)))
    fuse(service, [current_profile()], refresh=True)
    assert len(model.requests) == 1
