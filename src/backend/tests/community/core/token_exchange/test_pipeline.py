from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityStage,
    CallerIamTokenOutcome,
)
from agentclaw.community.core.token_exchange import (
    NoopTokenPluginPipeline,
    TokenExchangeOrchestrator,
    TokenPluginPipeline,
)
from agentclaw.community.core.token_exchange.protocols import (
    ExchangedToken,
    PreparedExchangeTokenRequest,
    TokenExchangeContext,
)
from agentclaw.community.plugin_api.auth import AuthRequestContext


class _Appender:
    def append_token(self, **kwargs: object) -> bool:
        self.kwargs = kwargs
        return True


class _Plugin:
    code = "test"

    def __init__(
        self,
        events: list[str],
        *,
        fail: bool = False,
        matches: bool = True,
    ) -> None:
        self.events = events
        self.fail = fail
        self.matches = matches

    def match(self, context: TokenExchangeContext) -> bool:
        del context
        self.events.append("match")
        return self.matches

    def prepare_exchange_token_request(self, context: TokenExchangeContext):
        del context
        self.events.append("prepare")
        if self.fail:
            raise RuntimeError("prepare failed")
        return PreparedExchangeTokenRequest(
            plugin_code=self.code,
            method="POST",
            path="https://example.test/token",
            headers={},
            body={},
            timeout_seconds=1,
            response_parser=MagicMock(),
            paas_device_id="device@template",
            outbound_header_name="x-test-token",
        )

    async def exchange_token(self, *, context, prepared):
        del context, prepared
        self.events.append("exchange")
        return ExchangedToken("token", None, "fingerprint", {})

    def append_to_outbound(self, *, context, prepared, token, appender):
        del context, prepared, token
        self.events.append("append")
        appender.append_token(
            paas_device_id="device@template",
            header_name="x-test-token",
            action="set",
            token="token",
            plugin_code=self.code,
        )


def _context() -> TokenExchangeContext:
    return TokenExchangeContext(
        bot_id="bot-1",
        bot_type="personal",
        owner_user_id="owner-1",
        user_list_entity_id="user-1",
        entity_id="user-1",
        stage="draft",
        env="pre",
        publish_id=None,
        binding_id=None,
        request_id="request-1",
    )


@pytest.mark.asyncio
async def test_pipeline_runs_four_phases_in_order() -> None:
    events: list[str] = []
    appender = _Appender()
    results = await TokenPluginPipeline(
        plugins=(_Plugin(events),), outbound_appender=appender
    ).run(_context())

    assert events == ["match", "prepare", "exchange", "append"]
    assert results[0].status == "succeeded"
    assert appender.kwargs["header_name"] == "x-test-token"


@pytest.mark.asyncio
async def test_one_plugin_failure_isolated() -> None:
    events: list[str] = []
    results = await TokenPluginPipeline(
        plugins=(_Plugin(events, fail=True), _Plugin(events)),
        outbound_appender=_Appender(),
    ).run(_context())

    assert [result.status for result in results] == ["failed", "succeeded"]
    assert events == ["match", "prepare", "match", "prepare", "exchange", "append"]


@pytest.mark.asyncio
async def test_pipeline_skips_plugin_when_match_is_false() -> None:
    events: list[str] = []

    results = await TokenPluginPipeline(
        plugins=(_Plugin(events, matches=False),),
        outbound_appender=_Appender(),
    ).run(_context())

    assert events == ["match"]
    assert results[0].status == "skipped"


@pytest.mark.asyncio
async def test_noop_pipeline_has_no_results() -> None:
    assert await NoopTokenPluginPipeline().run(_context()) == []


@pytest.mark.asyncio
async def test_caller_runs_before_plugins_and_keeps_result_on_plugin_failure() -> None:
    events: list[str] = []
    caller = MagicMock()
    caller.get_iam_token = AsyncMock(
        side_effect=lambda **kwargs: (
            events.append("caller"),
            CallerIamTokenOutcome(iam_token="original"),
        )[1]
    )
    plugin = _Plugin(events, fail=True)
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_entity.return_value = {
        "owner_id": "owner-1",
        "bot_type": "personal",
        "env": "pre",
    }
    orchestrator = TokenExchangeOrchestrator(
        caller_service=caller,
        token_pipeline=TokenPluginPipeline(
            plugins=(plugin,), outbound_appender=_Appender()
        ),
        bot_repository=bot_repo,
    )

    result = await orchestrator.get_iam_token(
        iam_token="iam",
        auth_request=AuthRequestContext(cookies={}, headers={}, query_params={}, base_url="http://test"),
        bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        entity_id="user-1",
        is_test_exchange=False,
    )

    assert result.iam_token == "original"
    assert events[0] == "caller"


@pytest.mark.asyncio
async def test_context_build_failure_does_not_change_caller_result() -> None:
    caller = MagicMock()
    caller.get_iam_token = AsyncMock(
        return_value=CallerIamTokenOutcome(iam_token="original")
    )
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_entity.side_effect = RuntimeError("db unavailable")
    orchestrator = TokenExchangeOrchestrator(
        caller_service=caller,
        token_pipeline=TokenPluginPipeline(plugins=(), outbound_appender=_Appender()),
        bot_repository=bot_repo,
    )

    result = await orchestrator.get_iam_token(
        iam_token="iam",
        auth_request=AuthRequestContext(cookies={}, headers={}, query_params={}, base_url="http://test"),
        bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        entity_id="user-1",
        is_test_exchange=False,
    )

    assert result.iam_token == "original"


@pytest.mark.asyncio
async def test_caller_exception_is_rethrown_after_independent_pipeline_run() -> None:
    caller = MagicMock()
    caller.get_iam_token = AsyncMock(side_effect=RuntimeError("caller failed"))
    pipeline = MagicMock()
    pipeline.run = AsyncMock(return_value=[])
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_entity.return_value = {
        "owner_id": "owner-1",
        "bot_type": "personal",
        "env": "pre",
    }
    orchestrator = TokenExchangeOrchestrator(
        caller_service=caller,
        token_pipeline=pipeline,
        bot_repository=bot_repo,
    )

    with pytest.raises(RuntimeError, match="caller failed"):
        await orchestrator.get_iam_token(
            iam_token="iam",
            auth_request=AuthRequestContext(
                cookies={}, headers={}, query_params={}, base_url="http://test"
            ),
            bot_id="bot-1",
            stage=CallerIdentityStage.DRAFT,
            publish_id=None,
            entity_id="user-1",
            is_test_exchange=False,
        )

    pipeline.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_unexpected_pipeline_exception_keeps_caller_result() -> None:
    caller = MagicMock()
    caller.get_iam_token = AsyncMock(
        return_value=CallerIamTokenOutcome(iam_token="original")
    )
    pipeline = MagicMock()
    pipeline.run = AsyncMock(side_effect=RuntimeError("pipeline failed"))
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_entity.return_value = {
        "owner_id": "owner-1",
        "bot_type": "personal",
        "env": "pre",
    }
    orchestrator = TokenExchangeOrchestrator(
        caller_service=caller,
        token_pipeline=pipeline,
        bot_repository=bot_repo,
    )

    result = await orchestrator.get_iam_token(
        iam_token="iam",
        auth_request=AuthRequestContext(
            cookies={}, headers={}, query_params={}, base_url="http://test"
        ),
        bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        entity_id="user-1",
        is_test_exchange=False,
    )

    assert result.iam_token == "original"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bot_id", "entity_id", "bot_record"),
    [
        (None, "user-1", None),
        ("bot-1", None, None),
        ("bot-1", "user-1", None),
        ("bot-1", "user-1", {"owner_id": "", "bot_type": "personal"}),
    ],
)
async def test_unavailable_context_skips_pipeline(
    bot_id: str | None,
    entity_id: str | None,
    bot_record: dict[str, str] | None,
) -> None:
    caller = MagicMock()
    caller.get_iam_token = AsyncMock(
        return_value=CallerIamTokenOutcome(iam_token="original")
    )
    pipeline = MagicMock()
    pipeline.run = AsyncMock(return_value=[])
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_entity.return_value = bot_record
    orchestrator = TokenExchangeOrchestrator(
        caller_service=caller,
        token_pipeline=pipeline,
        bot_repository=bot_repo,
    )

    result = await orchestrator.get_iam_token(
        iam_token="iam",
        auth_request=AuthRequestContext(
            cookies={}, headers={}, query_params={}, base_url="http://test"
        ),
        bot_id=bot_id,
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        entity_id=entity_id,
        is_test_exchange=False,
    )

    assert result.iam_token == "original"
    pipeline.run.assert_not_awaited()
