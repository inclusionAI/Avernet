"""Unit tests for ActiveSessionInspector.

Covers the dual-axis contract mapping (engine ``query_status`` + ``verdict``
-> BaaS ``ActiveSessionVerdict``) and the safe-by-default failure convergence
to ``UNKNOWN`` (never ``CLEAR``) for every error path.

The inspector uses ``PaasServiceFacade.execute_command`` + ``curl`` exactly
like the sibling ``EngineHealthChecker`` / ``AdapterHealthChecker`` in the
same package; tests mock the facade to drive each branch.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.core.service.health_check.paas import (
    ActiveSessionInspector,
    ActiveSessionInspectResult,
    ActiveSessionVerdict,
)

ENDPOINT = "http://127.0.0.1:20003/api/engine/active-sessions"


def _command_result(exit_code: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """Build a minimal CommandResult-like mock returned by execute_command."""
    cr = MagicMock()
    cr.exit_code = exit_code
    cr.stdout = stdout
    cr.stderr = stderr
    return cr


class TestActiveSessionInspectorMapping:
    """§3.2 dual-axis contract mapping table."""

    @pytest.fixture
    def inspector(self) -> ActiveSessionInspector:
        return ActiveSessionInspector()

    @pytest.fixture
    def mock_facade(self) -> MagicMock:
        facade = MagicMock()
        facade.execute_command = AsyncMock()
        return facade

    @pytest.mark.asyncio
    async def test_ok_clear_maps_clear(
        self, inspector: ActiveSessionInspector, mock_facade: MagicMock
    ) -> None:
        mock_facade.execute_command.return_value = _command_result(
            stdout='{"query_status": "ok", "verdict": "clear"}'
        )
        result = await inspector.inspect(
            paas_device_id="dev--0@tpl-1", paas_facade=mock_facade
        )
        assert result.verdict is ActiveSessionVerdict.CLEAR
        assert result.query_status == "ok"
        assert result.timeout is False
        assert result.error is None
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_ok_active_maps_active(
        self, inspector: ActiveSessionInspector, mock_facade: MagicMock
    ) -> None:
        mock_facade.execute_command.return_value = _command_result(
            stdout='{"query_status": "ok", "verdict": "active"}'
        )
        result = await inspector.inspect(
            paas_device_id="dev--0@tpl-1", paas_facade=mock_facade
        )
        assert result.verdict is ActiveSessionVerdict.ACTIVE
        assert result.query_status == "ok"

    @pytest.mark.asyncio
    async def test_ok_unknown_verdict_maps_unknown(
        self, inspector: ActiveSessionInspector, mock_facade: MagicMock
    ) -> None:
        mock_facade.execute_command.return_value = _command_result(
            stdout='{"query_status": "ok", "verdict": "unknown"}'
        )
        result = await inspector.inspect(
            paas_device_id="dev--0@tpl-1", paas_facade=mock_facade
        )
        assert result.verdict is ActiveSessionVerdict.UNKNOWN

    @pytest.mark.asyncio
    async def test_ok_missing_verdict_maps_unknown(
        self, inspector: ActiveSessionInspector, mock_facade: MagicMock
    ) -> None:
        mock_facade.execute_command.return_value = _command_result(
            stdout='{"query_status": "ok"}'
        )
        result = await inspector.inspect(
            paas_device_id="dev--0@tpl-1", paas_facade=mock_facade
        )
        assert result.verdict is ActiveSessionVerdict.UNKNOWN

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["unsupported", "timeout", "error"])
    async def test_non_ok_status_maps_unknown_even_on_http200(
        self,
        inspector: ActiveSessionInspector,
        mock_facade: MagicMock,
        status: str,
    ) -> None:
        # HTTP 200 with non-ok query_status must collapse to UNKNOWN; we never
        # trust a non-ok transport verdict as a drain-all-clear signal.
        mock_facade.execute_command.return_value = _command_result(
            stdout=f'{{"query_status": "{status}", "verdict": "clear"}}'
        )
        result = await inspector.inspect(
            paas_device_id="dev--0@tpl-1", paas_facade=mock_facade
        )
        assert result.verdict is ActiveSessionVerdict.UNKNOWN
        assert result.query_status == status

    @pytest.mark.asyncio
    async def test_unknown_query_status_value_maps_unknown(
        self, inspector: ActiveSessionInspector, mock_facade: MagicMock
    ) -> None:
        mock_facade.execute_command.return_value = _command_result(
            stdout='{"query_status": "weird", "verdict": "clear"}'
        )
        result = await inspector.inspect(
            paas_device_id="dev--0@tpl-1", paas_facade=mock_facade
        )
        assert result.verdict is ActiveSessionVerdict.UNKNOWN

    @pytest.mark.asyncio
    async def test_missing_query_status_maps_unknown(
        self, inspector: ActiveSessionInspector, mock_facade: MagicMock
    ) -> None:
        mock_facade.execute_command.return_value = _command_result(
            stdout='{"verdict": "clear"}'
        )
        result = await inspector.inspect(
            paas_device_id="dev--0@tpl-1", paas_facade=mock_facade
        )
        assert result.verdict is ActiveSessionVerdict.UNKNOWN
        assert result.query_status is None


class TestActiveSessionInspectorFailures:
    """Every failure path collapses to UNKNOWN, never CLEAR."""

    @pytest.fixture
    def inspector(self) -> ActiveSessionInspector:
        return ActiveSessionInspector()

    @pytest.fixture
    def mock_facade(self) -> MagicMock:
        facade = MagicMock()
        facade.execute_command = AsyncMock()
        return facade

    @pytest.mark.asyncio
    async def test_curl_nonzero_exit_maps_unknown(
        self, inspector: ActiveSessionInspector, mock_facade: MagicMock
    ) -> None:
        mock_facade.execute_command.return_value = _command_result(
            exit_code=7, stderr="curl: failed to connect"
        )
        result = await inspector.inspect(
            paas_device_id="dev--0@tpl-1", paas_facade=mock_facade
        )
        assert result.verdict is ActiveSessionVerdict.UNKNOWN
        assert result.error is not None
        assert "exit_code=7" in result.error

    @pytest.mark.asyncio
    async def test_invalid_json_maps_unknown(
        self, inspector: ActiveSessionInspector, mock_facade: MagicMock
    ) -> None:
        mock_facade.execute_command.return_value = _command_result(
            stdout="<html>not json</html>"
        )
        result = await inspector.inspect(
            paas_device_id="dev--0@tpl-1", paas_facade=mock_facade
        )
        assert result.verdict is ActiveSessionVerdict.UNKNOWN
        assert "JSON" in (result.error or "")

    @pytest.mark.asyncio
    async def test_empty_stdout_maps_unknown(
        self, inspector: ActiveSessionInspector, mock_facade: MagicMock
    ) -> None:
        mock_facade.execute_command.return_value = _command_result(stdout="")
        result = await inspector.inspect(
            paas_device_id="dev--0@tpl-1", paas_facade=mock_facade
        )
        assert result.verdict is ActiveSessionVerdict.UNKNOWN

    @pytest.mark.asyncio
    async def test_non_object_json_maps_unknown(
        self, inspector: ActiveSessionInspector, mock_facade: MagicMock
    ) -> None:
        mock_facade.execute_command.return_value = _command_result(stdout="[1, 2, 3]")
        result = await inspector.inspect(
            paas_device_id="dev--0@tpl-1", paas_facade=mock_facade
        )
        assert result.verdict is ActiveSessionVerdict.UNKNOWN
        assert "JSON object" in (result.error or "")

    @pytest.mark.asyncio
    async def test_timeout_maps_unknown(
        self, inspector: ActiveSessionInspector, mock_facade: MagicMock
    ) -> None:
        mock_facade.execute_command.side_effect = TimeoutError()
        result = await inspector.inspect(
            paas_device_id="dev--0@tpl-1", paas_facade=mock_facade
        )
        assert result.verdict is ActiveSessionVerdict.UNKNOWN
        assert result.timeout is True
        assert "Timeout" in (result.error or "")

    @pytest.mark.asyncio
    async def test_generic_exception_maps_unknown(
        self, inspector: ActiveSessionInspector, mock_facade: MagicMock
    ) -> None:
        mock_facade.execute_command.side_effect = RuntimeError("rpc boom")
        result = await inspector.inspect(
            paas_device_id="dev--0@tpl-1", paas_facade=mock_facade
        )
        assert result.verdict is ActiveSessionVerdict.UNKNOWN
        assert "rpc boom" in (result.error or "")
        assert result.timeout is False


class TestActiveSessionInspectorPassthroughAndAudit:
    """Bot/device isolation and audit-field passthrough behavior."""

    @pytest.fixture
    def inspector(self) -> ActiveSessionInspector:
        return ActiveSessionInspector()

    @pytest.fixture
    def mock_facade(self) -> MagicMock:
        facade = MagicMock()
        facade.execute_command = AsyncMock()
        return facade

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "paas_device_id,bot_id,device_id,lifecycle_stage",
        [
            ("dev--0@tpl-1", 101, 11, "draft"),
            ("dev--1@tpl-2", 202, 22, "verify"),
            ("dev--2@tpl-3", 303, 33, "online"),
        ],
    )
    async def test_paas_device_id_passthrough_per_device_isolation(
        self,
        inspector: ActiveSessionInspector,
        mock_facade: MagicMock,
        paas_device_id: str,
        bot_id: int,
        device_id: int,
        lifecycle_stage: str,
    ) -> None:
        """Inspector forwards the caller-provided paas_device_id to the facade
        verbatim; no cross-device aggregation or aliasing happens here."""
        mock_facade.execute_command.return_value = _command_result(
            stdout='{"query_status": "ok", "verdict": "clear"}'
        )
        await inspector.inspect(
            paas_device_id=paas_device_id,
            paas_facade=mock_facade,
            bot_id=bot_id,
            device_id=device_id,
            lifecycle_stage=lifecycle_stage,
        )
        mock_facade.execute_command.assert_awaited_once()
        call_kwargs = mock_facade.execute_command.await_args.kwargs
        assert call_kwargs["paas_device_id"] == paas_device_id
        # curl command must target the active-sessions endpoint.
        assert ENDPOINT in call_kwargs["cmd"]

    @pytest.mark.asyncio
    async def test_duration_ms_non_negative(
        self, inspector: ActiveSessionInspector, mock_facade: MagicMock
    ) -> None:
        mock_facade.execute_command.return_value = _command_result(
            stdout='{"query_status": "ok", "verdict": "clear"}'
        )
        result = await inspector.inspect(
            paas_device_id="dev--0@tpl-1", paas_facade=mock_facade
        )
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_custom_timeout_forwarded(
        self, inspector: ActiveSessionInspector, mock_facade: MagicMock
    ) -> None:
        mock_facade.execute_command.return_value = _command_result(
            stdout='{"query_status": "ok", "verdict": "clear"}'
        )
        await inspector.inspect(
            paas_device_id="dev--0@tpl-1",
            paas_facade=mock_facade,
            timeout_seconds=25,
        )
        call_kwargs = mock_facade.execute_command.await_args.kwargs
        assert call_kwargs["timeout_seconds"] == 25

    @pytest.mark.asyncio
    async def test_constructor_default_timeout_used_when_none(
        self, mock_facade: MagicMock
    ) -> None:
        inspector = ActiveSessionInspector(timeout_seconds=7)
        mock_facade.execute_command.return_value = _command_result(
            stdout='{"query_status": "ok", "verdict": "clear"}'
        )
        await inspector.inspect(
            paas_device_id="dev--0@tpl-1", paas_facade=mock_facade
        )
        call_kwargs = mock_facade.execute_command.await_args.kwargs
        assert call_kwargs["timeout_seconds"] == 7

    @pytest.mark.asyncio
    async def test_result_is_dataclass_with_required_fields(
        self, inspector: ActiveSessionInspector, mock_facade: MagicMock
    ) -> None:
        mock_facade.execute_command.return_value = _command_result(
            stdout='{"query_status": "ok", "verdict": "active"}'
        )
        result = await inspector.inspect(
            paas_device_id="dev--0@tpl-1", paas_facade=mock_facade
        )
        assert isinstance(result, ActiveSessionInspectResult)
        # Active result retains raw_response for auditers/observability.
        assert result.raw_response == {"query_status": "ok", "verdict": "active"}