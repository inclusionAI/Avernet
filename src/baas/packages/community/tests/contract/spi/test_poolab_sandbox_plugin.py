from secbaas.plugins.sandbox.poolab import StubPoolabSandboxPlugin
from secbaas.spi.sandbox.poolab import PoolabSandboxPlugin


class TestStubPoolabSandboxPlugin:
    def setup_method(self) -> None:
        self.plugin = StubPoolabSandboxPlugin()

    def test_is_protocol_instance(self) -> None:
        assert isinstance(self.plugin, PoolabSandboxPlugin)

    async def test_create_and_destroy(self) -> None:
        from secbaas.api.device_manage import PoolabCreateConfig

        config = PoolabCreateConfig(
            poolab_user_id="u1",
            poolab_tenant_id="t1",
        )
        result = await self.plugin.create_device(config)
        assert result.poolab_id is not None

        destroyed = await self.plugin.destroy_device(result.poolab_id)
        assert destroyed is True

    async def test_execute_command(self) -> None:
        result = await self.plugin.execute_command("stub-1", "ls")
        assert result.exit_code == 0
        assert result.stdout == "stub-output"

    async def test_close(self) -> None:
        await self.plugin.close()
