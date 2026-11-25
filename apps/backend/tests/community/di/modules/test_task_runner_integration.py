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


# ── openapi_bot api_key env selection (api_key_secret_prod vs api_key_secret) ──
def _openapi_cfg(**kw):
    return module.OpenApiBotConfig(
        base_url=kw.get("base_url", "https://baas.example"),
        base_url_pre=kw.get("base_url_pre", ""),
        api_key_secret=kw.get("api_key_secret", "pre-key"),
        api_key_secret_prod=kw.get("api_key_secret_prod", "prod-key"),
        api_key_prefix=kw.get("api_key_prefix", ""),
    )


def test_env_api_key_selects_prod_key_in_prod_env(monkeypatch):
    monkeypatch.setattr(module, "get_current_env", lambda: "prod")
    assert module._env_api_key(_openapi_cfg()) == "prod-key"


def test_env_api_key_falls_back_to_pre_key_outside_prod(monkeypatch):
    for env in ("pre", "dev"):
        monkeypatch.setattr(module, "get_current_env", lambda env=env: env)
        assert module._env_api_key(_openapi_cfg()) == "pre-key"


def test_openapi_bot_provider_reads_api_key_secret_prod_from_block(monkeypatch):
    block = {
        "base_url": "https://baas.example",
        "api_key_secret": "pre-key",
        "api_key_secret_prod": "prod-key",
        "api_key_prefix": "pref",
    }
    monkeypatch.setattr(module, "_block", lambda name: block if name == "openapi_bot" else {})
    cfg = module.TaskRunnerIntegrationModule().openapi_bot()
    assert cfg.api_key_secret == "pre-key"
    assert cfg.api_key_secret_prod == "prod-key"
    assert cfg.api_key_prefix == "pref"


def test_openapi_bot_port_fail_closed_when_prod_key_missing_in_prod(monkeypatch):
    """prod env: 只有非 prod key 时,所选 prod key 空 → port None(fail-closed)。"""
    monkeypatch.setattr(module, "get_current_env", lambda: "prod")
    cfg = _openapi_cfg(api_key_secret="pre-key", api_key_secret_prod="", base_url="https://baas.example")
    assert module.TaskRunnerIntegrationModule().openapi_bot_port(cfg=cfg) is None
