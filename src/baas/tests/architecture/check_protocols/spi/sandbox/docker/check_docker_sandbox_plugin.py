from secbaas.community.plugins.sandbox.docker import RealDockerSandboxPlugin
from secbaas.community.spi.sandbox.docker import (
    DockerSandboxPlugin as DockerSandboxPluginProtocol,
)

# Assign value, will trigger mypy type check
_docker_sandbox_plugin: DockerSandboxPluginProtocol = RealDockerSandboxPlugin()
