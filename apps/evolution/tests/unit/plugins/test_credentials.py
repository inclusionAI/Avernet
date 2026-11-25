"""凭证解析：显式委派优先、缺失回落 owner、token 未下发时失败。"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from avernet_evolution.core.evolution_run.errors import EvolutionAppNotGranted, GenomeBaselineMissing
from avernet_evolution.plugins.credentials import Delegation, FileDelegations
from avernet_evolution.plugins.platform_http import (
    HttpGenome,
    PlatformCredential,
    PlatformHttpError,
)

TOKEN = "x" * 32


def _bot(bot_id="bot-1", owner="user-1", entity="user-1"):
    return SimpleNamespace(entity_id=entity, bot_id=bot_id, owner_id=owner)


def _source(tmp_path, delegations=()):
    token = tmp_path / "app-token"
    token.write_text(TOKEN)
    return FileDelegations(token, delegations)


@pytest.mark.asyncio
async def test_explicit_delegation_wins(tmp_path) -> None:
    source = _source(tmp_path, [Delegation("ent", "bot-1", "user-1", "reviewer-9")])
    credential = await source(_bot(entity="ent"))
    assert isinstance(credential, PlatformCredential)
    assert credential.acting_user_id == "reviewer-9"
    assert credential.headers["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.asyncio
async def test_missing_delegation_falls_back_to_owner(tmp_path, caplog) -> None:
    source = _source(tmp_path)
    with caplog.at_level(logging.WARNING):
        first = await source(_bot())
        second = await source(_bot())
    assert first.acting_user_id == "user-1"
    assert second.acting_user_id == "user-1"
    # 回落是正常路径，只提示一次，避免每个请求刷日志。
    assert sum("delegation.fallback" in record.getMessage() for record in caplog.records) == 1


@pytest.mark.asyncio
async def test_missing_token_is_a_domain_error(tmp_path) -> None:
    source = FileDelegations(tmp_path / "absent", [])
    with pytest.raises(PlatformHttpError):
        await source(_bot())


@pytest.mark.asyncio
async def test_short_token_is_treated_as_not_provisioned(tmp_path) -> None:
    token = tmp_path / "short"
    token.write_text("abc")
    with pytest.raises(PlatformHttpError):
        await FileDelegations(token, [])(_bot())


@pytest.mark.asyncio
async def test_active_404_is_reported_as_missing_baseline_only_if_visible() -> None:
    request = AsyncMock(side_effect=[PlatformHttpError("refused", status=404), {"drift": None}])
    bot = _bot()
    with pytest.raises(GenomeBaselineMissing):
        await HttpGenome(SimpleNamespace(request=request)).resolve_revision(bot, "active")
    request.assert_awaited_with(bot, "GET", "/genome/drift")


@pytest.mark.asyncio
async def test_drift_override_does_not_prove_visibility() -> None:
    request = AsyncMock(side_effect=PlatformHttpError("refused", status=404))
    override = AsyncMock(return_value=None)
    bot = _bot()
    with pytest.raises(EvolutionAppNotGranted, match="check the application grant"):
        await HttpGenome(SimpleNamespace(request=request), drift=override).resolve_revision(bot, "active")
    request.assert_awaited_with(bot, "GET", "/genome/drift")
    override.assert_not_awaited()


@pytest.mark.asyncio
async def test_nonempty_drift_does_not_mean_no_baseline() -> None:
    failure = PlatformHttpError("refused", status=404)
    request = AsyncMock(side_effect=[failure, {"drift": {"active": "rev1", "observed": "rev2"}}])
    with pytest.raises(PlatformHttpError) as caught:
        await HttpGenome(SimpleNamespace(request=request)).resolve_revision(_bot(), "active")
    assert caught.value is failure


@pytest.mark.asyncio
async def test_other_404_stays_a_generic_failure() -> None:
    class Bridge:
        async def request(self, bot, method, suffix, **kwargs):
            raise PlatformHttpError("platform request refused (404)", status=404)

    genome = HttpGenome(Bridge())
    with pytest.raises(PlatformHttpError):
        await genome.resolve_revision(_bot(), "sha256:missing")


@pytest.mark.asyncio
async def test_default_drift_404_is_inaccessible_before_resolving_active() -> None:
    request = AsyncMock(side_effect=PlatformHttpError("refused", status=404))
    with pytest.raises(EvolutionAppNotGranted):
        await HttpGenome(SimpleNamespace(request=request)).drift(_bot())
    assert request.await_count == 1
