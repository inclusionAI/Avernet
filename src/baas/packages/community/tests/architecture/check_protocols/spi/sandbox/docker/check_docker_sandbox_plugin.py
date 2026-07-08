from secbaas.plugins.sandbox.docker import RealDockerSandboxPlugin
from secbaas.spi.sandbox.docker import (
    DockerSandboxPlugin as DockerSandboxPluginProtocol,
)

# Assign value, will trigger mypy type check
_docker_sandbox_plugin: DockerSandboxPluginProtocol = RealDockerSandboxPlugin()
