"""DWS-specific configuration on the bounded hosting command runner."""

from collections.abc import Mapping
from pathlib import Path

from engine.community.plugins.dws_hosting.command import HostingCommandProcess


class DwsProcess(HostingCommandProcess):
    def __init__(
        self,
        command: tuple[str, ...],
        config_dir: Path,
        environment: Mapping[str, str],
        *,
        timeout: float = 60,
        line_limit: int = 262_144,
        output_limit: int = 8_388_608,
        shutdown_seconds: float = 5,
    ) -> None:
        super().__init__(
            command,
            config_dir,
            environment,
            timeout=timeout,
            line_limit=line_limit,
            output_limit=output_limit,
            shutdown_seconds=shutdown_seconds,
        )
        self.config_dir = config_dir
        self.environment["DWS_CONFIG_DIR"] = str(config_dir)
