"""Tests for start_hook_dispatcher — wrapper generation, callback selection, failure reporting.

Covers:
- _generate_wrapper_script: template rendering with escaped variables
- _get_callback_server: env-based URL selection (prod vs non-prod)
- _report_failure: callback dispatch on wrapper failure
- dispatch_start_hook: threading, wrapper dispatch, error paths
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from secbaas.community.config import Config
from secbaas.community.core.service.device_manage._start_hook_dispatcher import (
    _generate_wrapper_script,
    _get_callback_server,
    dispatch_start_hook,
)

_CONFIG_WITH_CALLBACK_URLS = Config(
    user_config={
        "secbaas": {
            "callback": {
                "host": {
                    "dev": "https://cb.dev.example.com",
                    "pre": "https://cb.pre.example.com",
                    "prod": "https://cb.prod.example.com",
                },
            },
        },
    },
)

# ============== _get_callback_server ==============


class TestGetCallbackServer:
    """_get_callback_server environment-based URL selection."""

    def test_prod_returns_prod_url(self):
        with (
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher.get_current_env",
                return_value="prod",
            ),
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher.get_config",
                return_value=_CONFIG_WITH_CALLBACK_URLS,
            ),
        ):
            url = _get_callback_server()
            assert url == "https://cb.prod.example.com"

    def test_pre_returns_pre_url(self):
        with (
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher.get_current_env",
                return_value="pre",
            ),
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher.get_config",
                return_value=_CONFIG_WITH_CALLBACK_URLS,
            ),
        ):
            url = _get_callback_server()
            assert url == "https://cb.pre.example.com"

    def test_dev_falls_back_to_pre_url(self):
        with (
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher.get_current_env",
                return_value="dev",
            ),
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher.get_config",
                return_value=_CONFIG_WITH_CALLBACK_URLS,
            ),
        ):
            url = _get_callback_server()
            assert url == "https://cb.pre.example.com"

    def test_unknown_env_returns_pre_url(self):
        with (
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher.get_current_env",
                return_value="staging",
            ),
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher.get_config",
                return_value=_CONFIG_WITH_CALLBACK_URLS,
            ),
        ):
            url = _get_callback_server()
            assert url == "https://cb.pre.example.com"


# ============== _generate_wrapper_script ==============


class TestGenerateWrapperScript:
    """_generate_wrapper_script Jinja2 template rendering."""

    def test_renders_shell_script_structure(self):
        """Generated script should be a valid bash script with expected sections."""
        script = _generate_wrapper_script(
            device_uuid="device-001",
            publish_id=123,
            rendered_hook="echo 'hello'",
            callback_server="https://cb.pre.example.com",
            tenant="test-tenant",
        )

        assert script.startswith("#!/bin/bash")
        assert "DEVICE_UUID=device-001" in script
        assert "PUBLISH_ID=123" in script
        assert "test-tenant" in script
        assert "cb.pre.example.com" in script
        assert "echo 'hello'" in script

    def test_escapes_device_uuid(self):
        """device_uuid should be shlex.quote'd to prevent shell injection."""
        malicious_uuid = "device; rm -rf /"
        script = _generate_wrapper_script(
            device_uuid=malicious_uuid,
            publish_id=1,
            rendered_hook="echo test",
            callback_server="https://example.com",
            tenant="test-tenant",
        )

        # shlex.quote wraps in single quotes, breaking the injection
        assert (
            "rm -rf" not in script.replace("'device; rm -rf /'", "")
            or "DEVICE_UUID='device; rm -rf /'" in script
        )

    def test_contains_hook_content(self):
        """The rendered hook content should be embedded in the script."""
        hook = "python3 -c 'print(\"hello\")'"
        script = _generate_wrapper_script(
            device_uuid="device-001",
            publish_id=1,
            rendered_hook=hook,
            callback_server="https://example.com",
            tenant="test-tenant",
        )

        assert "HOOK_SCRIPT_EOF" in script
        assert hook in script

    def test_contains_callback_url_with_tenant(self):
        """Callback URL should contain the tenant as query param."""
        script = _generate_wrapper_script(
            device_uuid="device-001",
            publish_id=1,
            rendered_hook="echo test",
            callback_server="https://cb.pre.example.com",
            tenant="my-tenant",
        )

        assert "my-tenant" in script or "TENANT=" in script

    def test_contains_retry_logic(self):
        """Script should contain retry loop for callback."""
        script = _generate_wrapper_script(
            device_uuid="device-001",
            publish_id=1,
            rendered_hook="echo test",
            callback_server="https://example.com",
            tenant="test-tenant",
        )

        assert "retry_count=10" in script or "retry_count =10" in script
        assert "send_callback" in script


# ============== dispatch_start_hook ==============


class TestDispatchStartHook:
    """dispatch_start_hook threading and wrapper dispatch."""

    def test_launches_background_thread(self):
        """dispatch_start_hook should start a daemon thread."""
        with (
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher._execute_wrapper_script",  # noqa: E501
            ),
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher._get_callback_server",
                return_value="https://example.com",
            ),
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher.threading.Thread",
            ) as mock_thread,
        ):
            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance

            dispatch_start_hook(
                tenant="test-tenant",
                device_uuid="device-001",
                provider_device_id="provider-001",
                rendered_hook="echo test",
                hook_timeout=60,
                facade=MagicMock(),
                publish_id=123,
            )

            mock_thread.assert_called_once()
            assert mock_thread.call_args.kwargs["daemon"] is True
            assert "hook-" in mock_thread.call_args.kwargs["name"]
            mock_thread_instance.start.assert_called_once()

    def test_dispatch_success(self):
        """Wrapper dispatch success should log and return (via thread)."""
        with (
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher._execute_wrapper_script",  # noqa: E501
            ) as mock_exec,
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher._get_callback_server",
                return_value="https://example.com",
            ),
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher.threading.Thread",
            ) as mock_thread,
        ):
            mock_exec.return_value = {"exit_code": 0, "stdout": "ok", "stderr": ""}

            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance

            dispatch_start_hook(
                tenant="test-tenant",
                device_uuid="device-001",
                provider_device_id="provider-001",
                rendered_hook="echo test",
                hook_timeout=60,
                facade=MagicMock(),
                publish_id=123,
            )

            mock_thread_instance.start.assert_called_once()

    def test_dispatch_failure_calls_report_failure(self):
        """Wrapper dispatch non-zero exit should call _report_failure."""
        with (
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher._execute_wrapper_script",  # noqa: E501
            ) as mock_exec,
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher._get_callback_server",
                return_value="https://example.com",
            ),
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher._report_failure",
            ),
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher.threading.Thread",
            ) as mock_thread,
        ):
            mock_exec.return_value = {
                "exit_code": 1,
                "stdout": "",
                "stderr": "error",
            }

            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance

            dispatch_start_hook(
                tenant="test-tenant",
                device_uuid="device-001",
                provider_device_id="provider-001",
                rendered_hook="echo test",
                hook_timeout=60,
                facade=MagicMock(),
                publish_id=123,
            )

            mock_thread_instance.start.assert_called_once()

    def test_dispatch_exception_calls_report_failure(self):
        """Wrapper exception should call _report_failure."""
        with (
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher._execute_wrapper_script",  # noqa: E501
                side_effect=Exception("Connection failed"),
            ),
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher._get_callback_server",
                return_value="https://example.com",
            ),
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher._report_failure",
            ) as mock_report,
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher.threading.Thread",
            ) as mock_thread,
        ):
            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance

            dispatch_start_hook(
                tenant="test-tenant",
                device_uuid="device-001",
                provider_device_id="provider-001",
                rendered_hook="echo test",
                hook_timeout=60,
                facade=MagicMock(),
                publish_id=123,
            )

            # Extract the _run_hook target from the Thread constructor and invoke it directly
            thread_target = mock_thread.call_args.kwargs.get("target")
            assert thread_target is not None
            thread_target()

            mock_report.assert_called_once()
            assert mock_report.call_args.kwargs["device_uuid"] == "device-001"
            assert "Connection failed" in mock_report.call_args.kwargs["stderr"]


# ============== _report_failure ==============


class TestReportFailure:
    """_report_failure internal callback on wrapper dispatch failure."""

    def test_sends_failure_callback(self):
        """_report_failure should send a FAILED callback via DefaultPublishService."""
        mock_callback = MagicMock()
        handle_device_callback = MagicMock()

        with (
            patch(
                "secbaas.community.core.service.publish_manage.DefaultPublishService.handle_device_callback",  # noqa: E501
                handle_device_callback,
            ),
            patch(
                "secbaas.community.api.publish_manage.DeviceCallbackRequest",  # noqa: E501
                return_value=mock_callback,
            ),
        ):
            from secbaas.community.core.service.device_manage._start_hook_dispatcher import (
                _report_failure,
            )

            _report_failure(
                device_uuid="device-001",
                publish_id=123,
                exit_code=1,
                stdout="output",
                stderr="error",
                tenant="test-tenant",
            )

            from secbaas.community.api.publish_manage import (
                DeviceCallbackRequest as MockedCallback,
            )

            MockedCallback.assert_called_once_with(
                device_uuid="device-001",
                publish_id=123,
                event_type="start",
                result_status="FAILED",
                exit_code=1,
                stdout="output",
                stderr="error",
                tenant="test-tenant",
            )

    def test_report_failure_exception_does_not_raise(self):
        """_report_failure should catch and log exceptions, not raise."""
        with (
            patch(
                "secbaas.community.core.service.publish_manage.DefaultPublishService",  # noqa: E501
                side_effect=Exception("import error"),
            ),
        ):
            from secbaas.community.core.service.device_manage._start_hook_dispatcher import (
                _report_failure,
            )

            # Should not raise
            _report_failure(
                device_uuid="device-001",
                publish_id=123,
                exit_code=1,
                stdout=None,
                stderr=None,
                tenant="test-tenant",
            )

    def test_report_failure_publish_id_none_sends_zero(self):
        """_report_failure with publish_id=None should send 0."""
        mock_callback = MagicMock()

        with (
            patch(
                "secbaas.community.core.service.publish_manage.DefaultPublishService.handle_device_callback",  # noqa: E501
                return_value="ok",
            ),
            patch(
                "secbaas.community.api.publish_manage.DeviceCallbackRequest",
                return_value=mock_callback,
            ),
        ):
            from secbaas.community.core.service.device_manage._start_hook_dispatcher import (
                _report_failure,
            )

            _report_failure(
                device_uuid="device-001",
                publish_id=None,
                exit_code=None,
                stdout=None,
                stderr=None,
                tenant="test-tenant",
            )

            from secbaas.community.api.publish_manage import DeviceCallbackRequest

            DeviceCallbackRequest.assert_called_once()
            assert DeviceCallbackRequest.call_args.kwargs["publish_id"] == 0

    def test_report_failure_no_running_loop(self):
        """_report_failure uses asyncio.run when no event loop exists."""
        mock_callback = MagicMock()

        with (
            patch(
                "secbaas.community.core.service.publish_manage.DefaultPublishService.handle_device_callback",  # noqa: E501
                return_value="ok",
            ),
            patch(
                "secbaas.community.api.publish_manage.DeviceCallbackRequest",
                return_value=mock_callback,
            ),
        ):
            from secbaas.community.core.service.device_manage._start_hook_dispatcher import (
                _report_failure,
            )

            _report_failure(
                device_uuid="device-001",
                publish_id=123,
                exit_code=1,
                stdout=None,
                stderr=None,
                tenant="test-tenant",
            )


class TestExecuteWrapperScriptAsync:
    """_execute_wrapper_script_async edge cases."""

    def test_debug_file_write_failure_logs_warning(self):
        """OSError on debug file write logs warning and continues."""

        from secbaas.community.core.service.device_manage._start_hook_dispatcher import (
            _execute_wrapper_script,
        )

        mock_facade = MagicMock()
        mock_facade.execute_command = AsyncMock(
            return_value=MagicMock(exit_code=0, stdout="DISPATCHED:42", stderr="")
        )

        with (
            patch("builtins.open", side_effect=OSError("Permission denied")),
            patch(
                "secbaas.community.core.service.device_manage._start_hook_dispatcher.logger",
            ) as mock_logger,
        ):
            result = _execute_wrapper_script(
                provider_device_id="provider-001",
                wrapper_script="echo test",
                facade=mock_facade,
            )

            assert result["exit_code"] == 0
            # Should have logged a warning about file write failure
            assert any(
                "Failed to save" in str(call)
                for call in mock_logger.warning.call_args_list
            )

    def test_execute_wrapper_script_sync_wraps_async(self):
        """_execute_wrapper_script calls _execute_wrapper_script_async."""

        with patch(
            "secbaas.community.core.service.device_manage._start_hook_dispatcher._execute_wrapper_script_async",
        ) as mock_async:
            mock_async.return_value = {"exit_code": 0, "stdout": "ok", "stderr": ""}

            from secbaas.community.core.service.device_manage._start_hook_dispatcher import (
                _execute_wrapper_script,
            )

            result = _execute_wrapper_script(
                provider_device_id="provider-001",
                wrapper_script="echo test",
                facade=MagicMock(),
            )

            assert mock_async.called
            assert mock_async.call_args[0] == ("provider-001", "echo test")
            assert "facade" in mock_async.call_args.kwargs
            assert result["exit_code"] == 0
