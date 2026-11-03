"""Bot-scoped read contract for Local Skill security observations."""

from datetime import datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.skills.router import router
from agentclaw.community.api.skill_query_service import SkillQueryServiceProtocol
from agentclaw.community.api.local_skill_upload_service import LocalSkillUploadServiceProtocol
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.local_skill_security_observation import (
    LocalSkillSecurityObservationRepositoryProtocol,
)
from agentclaw.community.di.local_skill_security_config import (
    LocalSkillSecurityObservationConfig,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    bind_bot_access_seam,
    user_scoped_client,
)


class _Query:
    def __init__(self, *, bot_id: str = "bot-1", owner_id: str = "actor", local=True):
        self.bot_id = bot_id
        self.owner_id = owner_id
        self.local = local

    def get_local_skill(self, *, skill_id: str, actor_id: str):
        return {
            "id": skill_id,
            "name": "weather",
            "bolt_id": self.bot_id,
            "user_id": self.owner_id,
            "git_path": "local://weather" if self.local else "git://weather",
        }


def _observation(observation_id: str, *, risk_status: str | None):
    return SimpleNamespace(
        observation_id=observation_id,
        upload_order=2 if observation_id == "new" else 1,
        provider_skill_id="tc:stable",
        modifier_id="actor",
        sha256="sha256:" + "a" * 64,
        check_id=f"check-{observation_id}",
        processing_status="COMPLETED" if risk_status else "PENDING",
        risk_status=risk_status,
        report={"riskStatus": risk_status} if risk_status else None,
        detail_url="https://example.invalid/report" if risk_status else None,
        error_code=None,
        error_message=None,
        scan_started_at=None,
        scan_completed_at=datetime(2026, 10, 12) if risk_status else None,
        created_at=datetime(2026, 10, 12),
    )


class _Repository:
    def __init__(self):
        self.current = _observation("new", risk_status=None)
        self.old = _observation("old", risk_status="PASS")
        self.history_rows = (self.current, self.old)
        self.latest_handoff = None
        self.incarnation_id = "incarnation-current"
        self.calls = []

    def get_observed_incarnation_id(self, **kwargs):
        self.calls.append(("formal", kwargs))
        return self.incarnation_id

    def get_identity_for_asset(self, **kwargs):
        self.calls.append(("identity", kwargs))
        return SimpleNamespace(provider_skill_id="tc:stable")

    def get_current_for_asset(self, **kwargs):
        self.calls.append(("current", kwargs))
        return self.current

    def get_latest_handoff_for_asset(self, **kwargs):
        self.calls.append(("handoff", kwargs))
        return self.latest_handoff

    def page_for_asset(self, **kwargs):
        self.calls.append(("page", kwargs))
        return len(self.history_rows), self.history_rows[: kwargs["page_size"]]


class _BotRepository:
    def get_by_id_and_owner(self, bot_id, owner_id):
        return {"entity_id": owner_id, "bot_id": bot_id, "owner_id": owner_id}

    def list_matches_by_legacy_locator(self, bot_id, locator):
        row = self.get_by_id_and_owner(bot_id, locator)
        return [] if row is None else [{"id": 1, "bot_id": bot_id, "entity_id": locator, **row}]


class _UploadService:
    def __init__(self, digest):
        self.digest = digest

    async def installed_package_digest(self, **_kwargs):
        return self.digest


def _client(
    query: _Query, repository: _Repository, *, digest="sha256:" + "a" * 64,
    observation_enabled=True,
):
    class Bindings(Module):
        def configure(self, binder):
            binder.bind(SkillQueryServiceProtocol, to=query)
            binder.bind(LocalSkillSecurityObservationRepositoryProtocol, to=repository)
            binder.bind(
                LocalSkillSecurityObservationConfig,
                to=LocalSkillSecurityObservationConfig(enabled=observation_enabled),
            )
            binder.bind(BotRepository, to=_BotRepository())
            binder.bind(LocalSkillUploadServiceProtocol, to=_UploadService(digest))
            bind_bot_access_seam(binder)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "actor"}
    attach_injector(app, Injector([Bindings()]))
    return user_scoped_client(app, "actor")


def test_disabled_observation_returns_unknown_without_querying_security_table():
    repository = _Repository()
    response = _client(
        _Query(), repository, observation_enabled=False
    ).get("/openapi/v1/bots/bot-1/skills/42/security-observations")
    assert response.status_code == 200
    assert response.json()["data"]["current_status"] == "UNKNOWN"
    assert response.json()["data"]["history"] == {"total": 0, "items": []}
    assert repository.calls == []


def test_current_attempt_stays_pending_while_older_pass_is_in_history():
    repository = _Repository()
    response = _client(_Query(), repository).get(
        "/openapi/v1/bots/bot-1/skills/42/security-observations?page_size=1"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["provider_skill_id"] == "tc:stable"
    assert data["current"]["observation_id"] == "new"
    assert data["current"]["risk_status"] is None
    assert data["current_status"] == "PENDING"
    assert data["history"]["total"] == 2
    assert [item["observation_id"] for item in data["history"]["items"]] == ["new"]
    assert (
        "page",
        {
            "env": "dev",
            "bot_id": "bot-1",
            "owner_id": "actor",
            "skill_id": 42,
            "incarnation_id": "incarnation-current",
            "page": 1,
            "page_size": 1,
        },
    ) in repository.calls


def test_latest_failed_handoff_hides_old_pass_but_retains_it_in_history():
    repository = _Repository()
    repository.current = repository.old
    repository.history_rows = (repository.old,)
    repository.latest_handoff = SimpleNamespace(
        observation_id="replacement-b",
        upload_order=2,
        sha256="sha256:" + "b" * 64,
        stage="SNAPSHOT_FAILED",
        snapshot_verified=False,
    )
    response = _client(_Query(), repository, digest="sha256:" + "b" * 64).get(
        "/openapi/v1/bots/bot-1/skills/42/security-observations"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["current"] is None
    assert data["current_status"] == "OBSERVATION_FAILED"
    assert data["current_failure"]["observation_id"] == "replacement-b"
    assert data["current_failure"]["stage"] == "SNAPSHOT_FAILED"
    assert data["history"]["items"][0]["risk_status"] == "PASS"


def test_unrecorded_replacement_is_unknown_when_installed_digest_changed():
    repository = _Repository()
    repository.current = repository.old
    repository.history_rows = (repository.old,)
    repository.latest_handoff = None
    response = _client(_Query(), repository, digest="sha256:" + "b" * 64).get(
        "/openapi/v1/bots/bot-1/skills/42/security-observations"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["current"] is None
    assert data["current_status"] == "UNKNOWN"
    assert data["current_failure"] is None
    assert data["history"]["items"][0]["risk_status"] == "PASS"


def test_wrong_bot_owner_or_nonlocal_asset_cannot_read_observations():
    for query in (
        _Query(bot_id="other-bot"),
        _Query(owner_id="other-owner"),
        _Query(local=False),
    ):
        repository = _Repository()
        response = _client(query, repository).get(
            "/openapi/v1/bots/bot-1/skills/42/security-observations"
        )
        assert response.status_code == 404
        assert repository.calls == []


def test_asset_without_incarnation_does_not_expose_reused_skill_id_history():
    repository = _Repository()
    repository.incarnation_id = None
    response = _client(_Query(), repository).get(
        "/openapi/v1/bots/bot-1/skills/42/security-observations"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["provider_skill_id"] is None
    assert data["current_status"] == "UNKNOWN"
    assert data["history"] == {"total": 0, "items": []}
    assert [method for method, _ in repository.calls] == ["formal"]
