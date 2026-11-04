"""G9 HTTP -> real merge/chat services -> durable stores, with local model doubles.

No live app, model, or group service is contacted. HTTP 200 alone is never the
success criterion. This suite exercises the composed TC runtime and SQLite.
"""

from contextlib import closing, contextmanager
from uuid import uuid4

import pytest
from app.domain.models.llm_response import LLMResponse
from app.domain.models.profile_fusion import GroupConversationSummary

from tests.contract.test_tc_catalog import runtime as tc_runtime  # noqa: F401
from tests.fixtures.runtime_acceptance import acceptance_run


class LocalModels:
    def __init__(self):
        self.provider = self
        self.fail_chat = False
        self.requests = []

    def generate(self, request, model=None):
        self.requests.append(request)
        structured = request.task_spec.need_structured_output
        if not structured and self.fail_chat:
            raise RuntimeError("controlled model outage")
        data = {
            "name": "Combined engineering team", "description": "Two expert profiles",
            "persona": "Database and Python engineering specialists",
            "memory": "Test migrations before release", "skills": ["python", "sql"],
        } if structured else None
        return LLMResponse(
            provider_id="acceptance-local", model_id="acceptance-model",
            raw_text="Use a tested migration and staged rollout.",
            structured_data=data, parse_success=structured, latency_ms=1,
            finish_reason="stop",
        )


class EmptyGroupContext:
    async def summarize(self, question, group_id):
        return GroupConversationSummary(
            original_question=question, rewritten_question=question,
            context_summary="", context_messages_count=0, key_messages=[], success=True,
        )


@pytest.fixture
def g9_runtime(tc_runtime, monkeypatch, tmp_path):  # noqa: F811 - pytest fixture injection
    @contextmanager
    def open_runtime():
        from app.adapters.http.dependencies import fusion_dependencies as dependencies
        from app.application.services.bot_fuse import fusion_expert_chat_service as chat
        from app.bootstrap.app_factory import _configure_fusion_dependencies
        from app.infra.config.feature_flags import FeatureFlags

        client, registry = tc_runtime
        from app.infra.adapters.sqlite_fused_profile_store import (
            SQLiteFusedProfileStore,
        )
        with closing(SQLiteFusedProfileStore(str(tmp_path / "fusion.sqlite"))) as repository:
            previous = registry.get("fused_profile_store")
            if previous is not None and hasattr(previous, "close"):
                previous.close()
            registry.register("fused_profile_store", repository)
            _configure_fusion_dependencies(client.app.state.context)
            models = LocalModels()
            monkeypatch.setattr(dependencies, "_get_llm_gateway_service", lambda: models)
            monkeypatch.setattr(dependencies, "_get_group_context_service", EmptyGroupContext)
            monkeypatch.setattr(chat, "get_server_ip", lambda: "127.0.0.1")
            monkeypatch.setenv("ENABLE_REAL_LLM", "true")
            monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
            FeatureFlags.reset()
            try:
                with acceptance_run(client, "test-token") as acceptance:
                    participants = []
                    for _ in range(2):
                        worker_id = f"acceptance_{uuid4().hex}:owner"
                        acceptance.worker_ids.append(worker_id)
                        acceptance.request("POST", f"/v1/workers/{worker_id}/sync", json={
                            "name": "Acceptance specialist", "description": "Database and Python testing",
                            "availability": "public", "runtime_state": "online",
                            "profile": {"profile_id": "release", "activate": False},
                        })
                        acceptance.request("PUT", f"/v1/workers/{worker_id}/config", json={"fusion_enable": True})
                        participants.append(worker_id)
                    path = f"/api/v1/groups/acceptance_{uuid4().hex}/fuse"
                    body = {
                        "question": "How should we release a database migration?",
                        "participants": participants, "driver_bot_id": participants[0],
                        "fusion_mode": "bot_profile_fuse", "options": {"timeout_ms": 10000},
                    }
                    yield acceptance, path, body, repository, models
            finally:
                service = dependencies._group_fusion_service
                if service is not None:
                    service.shutdown()

    return open_runtime


def assert_g9_success(result, participants):
    assert result["metadata"]["route_impl"] == "real_llm_fusion"
    assert result["fusion_mode"] == "bot_profile_fuse"
    assert result["errors"] == []
    assert result["recommendation"]["summary"] == "Use a tested migration and staged rollout."
    profile = result["extend_result"]["fused_profile"]
    assert profile["name"] == "Combined engineering team"
    assert set(profile["source_participants"]) == set(participants)
    assert profile["fusion_strategy"] == "llm_fusion"


def fresh_reader(repository):
    """Read committed data with a new store, bypassing service caches."""
    pool = getattr(repository, "_pool", None)
    if pool is not None:
        return type(repository)(connection_pool=pool)
    return type(repository)(repository._db_path)


def test_g9_http_result_is_persisted_and_repeat_reuses_profile(g9_runtime):
    with g9_runtime() as (acceptance, path, body, repository, models):
        first = acceptance.request("POST", path, json=body)
        assert_g9_success(first, body["participants"])
        with closing(fresh_reader(repository)) as reader:
            stored = reader.find_by_key(first["fusion_id"])
            assert stored is not None
            assert stored.fuse_detail["name"] == "Combined engineering team"
            turns = reader.get_conversation(first["fusion_id"])
            assert len(turns["turns"]) == turns["total_turns"] == 1
            assert turns["turns"][0]["question"] == body["question"]
        second = acceptance.request("POST", path, json=body)
        assert_g9_success(second, body["participants"])
        assert second["fusion_id"] == first["fusion_id"]
        assert repository.get_conversation(first["fusion_id"])["total_turns"] == 2
        assert sum(r.task_spec.need_structured_output for r in models.requests) == 1


def test_g9_current_description_reaches_model_and_update_preserves_history(g9_runtime):
    with g9_runtime() as (acceptance, path, body, repository, models):
        worker_id = body["participants"][0]
        acceptance.request("POST", f"/v1/workers/{worker_id}/sync", json={
            "name": "Acceptance specialist", "description": "Review transactional schema releases",
            "profile": {"skill_sets": [{"name": "sql", "description": "Check schema rollback safety"}]},
        })

        first = acceptance.request("POST", path, json=body)
        assert_g9_success(first, body["participants"])
        first_prompt = next(r.user_prompt for r in models.requests if r.task_spec.need_structured_output)
        assert "Review transactional schema releases" in first_prompt
        assert "Check schema rollback safety" in first_prompt

        acceptance.request("POST", f"/v1/workers/{worker_id}/sync", json={
            "name": "Acceptance specialist", "description": "Review online schema rollback and recovery",
            "profile": {},
        })
        second = acceptance.request("POST", path, json=body)
        assert_g9_success(second, body["participants"])
        assert second["fusion_id"] != first["fusion_id"]
        fusion_requests = [r for r in models.requests if r.task_spec.need_structured_output]
        assert len(fusion_requests) == 2
        assert "Review online schema rollback and recovery" in fusion_requests[-1].user_prompt
        with closing(fresh_reader(repository)) as reader:
            assert reader.find_by_key(first["fusion_id"]) is not None
            old_turns = reader.get_conversation(first["fusion_id"])
            assert old_turns["total_turns"] == 1
            assert old_turns["turns"][0]["question"] == body["question"]


def test_g9_disabled_participant_returns_explicit_error(g9_runtime):
    with g9_runtime() as (acceptance, path, body, _repository, models):
        acceptance.request("PUT", f"/v1/workers/{body['participants'][0]}/config", json={"fusion_enable": False})
        result = acceptance.request("POST", path, json=body)
        assert result["errors"]
        assert "fusion disabled" in str(result["errors"])
        assert result.get("recommendation") is None
        assert not models.requests


def test_g9_model_failure_is_not_accepted_as_success(g9_runtime):
    with g9_runtime() as (acceptance, path, body, repository, models):
        models.fail_chat = True
        result = acceptance.request("POST", path, json=body)
        assert result["errors"]
        assert result.get("recommendation") is None
        assert repository.get_conversation(result["fusion_id"])["turns"] == []
        assert len([r for r in models.requests if not r.task_spec.need_structured_output]) == 3


def test_g9_profile_persistence_failure_is_not_accepted_as_success(g9_runtime, monkeypatch):
    with g9_runtime() as (acceptance, path, body, repository, _models):
        def fail_save(*args, **kwargs):
            raise RuntimeError("controlled fusion storage outage")

        monkeypatch.setattr(repository, "save", fail_save)
        result = acceptance.request("POST", path, json=body)
        assert result["errors"]
        assert result.get("recommendation") is None
        assert repository.find_by_key(result["fusion_id"]) is None


def test_g9_conversation_write_failure_preserves_legacy_answer(g9_runtime, monkeypatch, caplog):
    with g9_runtime() as (acceptance, path, body, repository, _models):
        def fail_append(*args, **kwargs):
            raise RuntimeError("controlled conversation storage outage")

        with monkeypatch.context() as outage:
            outage.setattr(repository, "append_turn", fail_append)
            result = acceptance.request("POST", path, json=body)
        # OCB legacy behavior: conversation history is best-effort; the answer
        # remains available. Do not turn this migration into a policy change.
        assert_g9_success(result, body["participants"])
        assert any(
            record.levelname == "ERROR" and "controlled conversation storage outage" in record.message
            for record in caplog.records
        )
        assert repository.get_conversation(result["fusion_id"])["turns"] == []
        retried = acceptance.request("POST", path, json=body)
        assert_g9_success(retried, body["participants"])
        assert retried["fusion_id"] == result["fusion_id"]
        with closing(fresh_reader(repository)) as reader:
            assert reader.get_conversation(retried["fusion_id"])["total_turns"] == 1
