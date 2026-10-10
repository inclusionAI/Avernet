"""G9 HTTP -> real merge/chat services -> durable stores, with local model doubles.

No live app, model, or group service is contacted. HTTP 200 alone is never the
success criterion. SQLite and optional disposable MySQL use the same scenarios.
"""

from contextlib import closing, contextmanager
from uuid import uuid4

import pytest

from src.domain.models.llm_response import LLMResponse
from src.domain.models.profile_fusion import GroupConversationSummary
from tests.fixtures.runtime_acceptance import acceptance_run
from tests.integration.test_isolated_runtime_acceptance import (
    TOKEN,
    isolated_app_factory,  # noqa: F401 - shared disposable database/app fixture
)


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
def g9_runtime(isolated_app_factory, monkeypatch, tmp_path):  # noqa: F811 - pytest fixture injection
    @contextmanager
    def open_runtime():
        from src.application.services.bot_fuse import fusion_expert_chat_service as chat
        from src.bootstrap.app_factory import _configure_fusion_dependencies
        from src.infra.config.feature_flags import FeatureFlags
        from src.interfaces.api.dependencies import fusion_dependencies as dependencies

        with isolated_app_factory() as (client, registry, _embedding):
            pool = getattr(registry.require("worker_registry_store"), "_pool", None)
            if pool is not None:
                from src.infra.public.stores.mysql_fused_profile_store import (
                    MySQLFusedProfileStore,
                )
                repository = MySQLFusedProfileStore(connection_pool=pool)
            else:
                from src.infra.adapters.sqlite_fused_profile_store import (
                    SQLiteFusedProfileStore,
                )
                repository = SQLiteFusedProfileStore(str(tmp_path / "fusion.sqlite"))
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
                with acceptance_run(client, TOKEN) as acceptance:
                    participants = []
                    for _ in range(2):
                        worker_id = acceptance.create_worker()
                        acceptance.put_profile(worker_id)
                        acceptance.request("PUT", f"/v1/workers/{worker_id}/profiles/release/activate")
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
