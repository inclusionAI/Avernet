from contextlib import contextmanager
from typing import Any

from agentclaw.community.di.modules.infrastructure.community import task_runner_integration as module


def test_merchant_task_bot_bindings_malformed_json_degrades_to_empty(monkeypatch):
    monkeypatch.setattr(module, "_user_config", lambda: {"merchant_task_bot_bindings": "not-json"})

    config = module.TaskRunnerIntegrationModule().merchant_task_bot_bindings()

    assert config.bot_id_by_role == {}


def test_merchant_task_bot_bindings_structured_json_is_decoded(monkeypatch):
    monkeypatch.setattr(module, "_user_config", lambda: {"merchant_task_bot_bindings": '{"store_owner_bot_id": "bot-1"}'})

    config = module.TaskRunnerIntegrationModule().merchant_task_bot_bindings()

    assert config.bot_id_by_role == {"store_owner_bot_id": "bot-1"}


def test_merchant_task_bot_bindings_dict_is_passed_through(monkeypatch):
    """``merchant_task_bot_bindings`` 为 dict 时直通,不经 JSON 解析。"""
    monkeypatch.setattr(
        module, "_user_config", lambda: {"merchant_task_bot_bindings": {"store_owner_bot_id": "bot-1"}}
    )

    config = module.TaskRunnerIntegrationModule().merchant_task_bot_bindings()

    assert config.bot_id_by_role == {"store_owner_bot_id": "bot-1"}



class _ScalarResult:
    def __init__(self, scalar: Any) -> None:
        self._v = scalar
        self.params: dict | None = None

    def scalar(self) -> Any:
        return self._v


class _FakeSession:
    def __init__(self, scalar_or_exc: Any) -> None:
        self._scalar_or_exc = scalar_or_exc
        self.execute_calls: list[tuple] = []

    def execute(self, stmt, params):  # noqa: D401 — fake ORM session execute
        self.execute_calls.append((str(stmt), dict(params)))
        v = self._scalar_or_exc
        if isinstance(v, BaseException):
            raise v
        result = _ScalarResult(v)
        result.params = dict(params)
        return result


class _FakeDatabase:
    """``DatabasePlugin`` stub:``orm_session()`` yields the inner ``_FakeSession``."""

    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    @contextmanager
    def orm_session(self):
        yield self._session


def test_bcs_bot_token_provider_resolves_session_token_from_db(monkeypatch):
    """driver-bot bcs_uuid → 直读 bcs_bots.session_token(命中),带 env scope 与缓存。"""
    monkeypatch.setattr(module, "get_current_env", lambda: "prod")
    session = _FakeSession(scalar_or_exc="tok-driver-XX")
    db = _FakeDatabase(session)

    provider = module.TaskRunnerIntegrationModule().bcs_bot_token_provider(db=db)
    assert isinstance(provider, module.BcsBotTokenServiceProvider)
    assert provider.get_token("20260905_dr1:U1") == "tok-driver-XX"
    assert session.execute_calls and session.execute_calls[0][1] == {
        "uuid": "20260905_dr1:U1",
        "env": "prod",
    }
    # 命中缓存:第二次调用不再查库(execute_calls 不增)。
    assert provider.get_token("20260905_dr1:U1") == "tok-driver-XX"
    assert len(session.execute_calls) == 1


def test_bcs_bot_token_provider_env_scope_matches_get_current_env(monkeypatch):
    """env 来自 get_current_env(pre 与 bcs_client 同源),写入 SQL params.env。"""
    monkeypatch.setattr(module, "get_current_env", lambda: "pre")
    session = _FakeSession(scalar_or_exc="tok-pre")
    db = _FakeDatabase(session)

    provider = module.TaskRunnerIntegrationModule().bcs_bot_token_provider(db=db)
    assert provider.get_token("dr:U2") == "tok-pre"
    assert session.execute_calls[0][1] == {"uuid": "dr:U2", "env": "pre"}


def test_bcs_bot_token_provider_returns_none_when_row_missing(monkeypatch):
    """查无行(sessionToken scalar=None)→ 返 None,经未命中短缓存不抛。"""
    monkeypatch.setattr(module, "get_current_env", lambda: "prod")
    session = _FakeSession(scalar_or_exc=None)
    db = _FakeDatabase(session)

    provider = module.TaskRunnerIntegrationModule().bcs_bot_token_provider(db=db)
    assert provider.get_token("dr:U3") is None
    assert provider.get_token("dr:U3") is None  # 未命中也短缓存
    assert len(session.execute_calls) == 1


def test_bcs_bot_token_provider_degrades_to_none_on_db_error(monkeypatch):
    """查询抛错(库无 bcs_bots 表 / DB down)→ 吞异常返 None,建群降级 no-sub 分支不阻断。"""
    monkeypatch.setattr(module, "get_current_env", lambda: "prod")
    session = _FakeSession(scalar_or_exc=RuntimeError("no table bcs_bots"))
    db = _FakeDatabase(session)

    provider = module.TaskRunnerIntegrationModule().bcs_bot_token_provider(db=db)
    assert provider.get_token("dr:U4") is None
