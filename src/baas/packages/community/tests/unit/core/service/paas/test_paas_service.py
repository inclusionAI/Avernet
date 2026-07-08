"""Unit tests for PaasService ABC base class methods."""

import pytest


class TestFetchStartProgressNotImplemented:
    """Tests for fetch_start_progress ABC default behavior."""

    @pytest.mark.asyncio
    async def test_fetch_start_progress_not_implemented(self):
        """WHEN called on base PaasService, THEN NotImplementedError raised."""
        from secbaas.core.service.paas._paas_service import PaasService

        # Create a minimal concrete subclass that doesn't override fetch_start_progress
        class _MinimalPaasService(PaasService):
            async def create_device(self, config):
                pass

            async def destroy_device(self, paas_device_id):
                pass

            async def execute_command(
                self, paas_device_id, cmd, env=None, timeout_seconds=30
            ):
                pass

            async def restart_device(self, paas_device_id):
                pass

            async def update_device(self, paas_device_id, config=None):
                pass

            async def resolve_ws_conn_info(self, paas_device_id, port, path):
                pass

            async def resolve_invoke_http_info(self, paas_device_id, port, path=None):
                pass

            async def get_device_info(self, paas_device_id):
                pass

            async def invoke_http_in_device(
                self,
                paas_device_id,
                method,
                port,
                path,
                query_string,
                headers,
                body,
            ):
                pass

            async def update_outbound_operation_rule(self, paas_device_id, rule):
                pass

            async def update_device_ttl(self, paas_device_id):
                pass

            def get_platform_type(self):
                pass

            def get_credentials(self):
                pass

        svc = _MinimalPaasService()
        with pytest.raises(NotImplementedError, match="fetch_start_progress"):
            await svc.fetch_start_progress("device-id@42")
