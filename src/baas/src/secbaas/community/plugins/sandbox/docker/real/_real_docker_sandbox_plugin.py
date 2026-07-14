"""Real Docker sandbox plugin — production docker-py SDK implementation.

Provides:
- RealDockerSandbox: wraps docker.models.containers.Container, implements DockerSandbox Protocol
- RealDockerSandboxPlugin: wraps docker.from_env() client, implements DockerSandboxPlugin Protocol

All docker-py logic lives here per D-04. This is the only code in the entire
codebase that imports docker-py SDK. StandalonePaasService (Wave 2) only
does pre-validation + passthrough via these classes.
"""

from __future__ import annotations

import base64
import sys
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    import docker
    import docker.errors as docker_errors
    import requests.exceptions as requests_exceptions

from secbaas.community.api.device_manage import ErrorCode, PaasError
from secbaas.community.logger import get_logger
from secbaas.community.spi.sandbox.docker import DockerSandbox, DockerSandboxPlugin

logger = get_logger("plugin-sandbox-docker-real")


def _import_docker() -> None:
    """Lazy-import docker SDK classes into the module namespace.

    Called once at the top of each method that uses docker classes.
    After the first call, subsequent calls are no-ops.
    """
    _mod = sys.modules[__name__]
    if getattr(_mod, "_docker_loaded", False):
        return

    import docker as _docker
    import docker.errors as _docker_errors
    import requests.exceptions as _requests_exceptions

    _mod.docker = _docker
    _mod.docker_errors = _docker_errors
    _mod.requests_exceptions = _requests_exceptions
    _mod._docker_loaded = True


# ---------------------------------------------------------------------------
# RealDockerSandbox — wraps a docker Container, implements DockerSandbox Protocol
# ---------------------------------------------------------------------------


class RealDockerSandbox(DockerSandbox):
    """Docker sandbox wrapping a docker.models.containers.Container.

    Implements the DockerSandbox Protocol (4 methods + 2 properties).
    All docker API calls go through the wrapped Container object.
    """

    def __init__(
        self,
        sandbox_id: str,
        container: Any,
        host_port: int,
    ) -> None:
        self._sandbox_id = sandbox_id
        self._container = container
        self._host_port = host_port
        self._logger = get_logger("plugin-sandbox-docker-real")

    @property
    def is_ready(self) -> bool:
        """Check if the container is in running state.

        Calls container.reload() to fetch fresh state from the Docker daemon
        before reading attrs (per RESEARCH Pitfall 3 — avoid stale state).
        """
        _import_docker()
        try:
            self._container.reload()
        except Exception:
            return False
        return self._container.attrs.get("State", {}).get("Status") == "running"

    @property
    def sandbox_id(self) -> str:
        """Return the sandbox container ID."""
        return self._sandbox_id

    def get_info(self) -> Any:
        """Extract container state information from container.attrs.

        Reloads the container to get fresh state from the Docker daemon.
        Returns a dict with keys: sandbox_id, status, container_id, host_port, image.

        Image name is read from attrs["Config"]["Image"] (human-readable name,
        e.g., "alpine:latest"), NOT attrs["Image"] (hash per RESEARCH Pitfall 2).
        Host port is extracted from HostConfig.PortBindings (persisted config)
        so it works on stopped containers too.
        """
        self._logger.info("[docker-real] get_info sandbox_id=%s", self._sandbox_id[:12])
        _import_docker()
        try:
            self._container.reload()
        except Exception:
            return {
                "sandbox_id": self._sandbox_id,
                "status": "unknown",
                "container_id": "",
                "host_port": self._host_port,
                "image": "",
            }

        attrs = self._container.attrs
        status = attrs.get("State", {}).get("Status", "unknown")
        container_id = attrs.get("Id", self._sandbox_id)

        # Per RESEARCH Pitfall 2: attrs["Image"] is the hash, not the name.
        # Use Config.Image for the human-readable image name.
        image = attrs.get("Config", {}).get("Image", attrs.get("Image", ""))

        # Extract host port from HostConfig.PortBindings (persisted — works
        # on stopped containers too, unlike NetworkSettings.Ports).
        port_bindings = attrs.get("HostConfig", {}).get("PortBindings", {})
        resolved_host_port = self._host_port
        if port_bindings:
            for binding_list in port_bindings.values():
                if binding_list and isinstance(binding_list, list):
                    host_port_str = binding_list[0].get("HostPort", "0")
                    try:
                        resolved_host_port = int(host_port_str)
                    except (ValueError, TypeError):
                        resolved_host_port = 0
                    break

        return {
            "sandbox_id": self._sandbox_id,
            "status": status,
            "container_id": container_id,
            "host_port": resolved_host_port,
            "image": image,
        }

    def exec_command(
        self,
        cmd: str,
        timeout_in_millis: int = 30000,
        envs: dict[str, str] | None = None,
    ) -> Any:
        """Execute a command inside the sandbox container via exec_run.

        Args:
            cmd: Command string to execute.
            timeout_in_millis: Maximum execution time in milliseconds.
            envs: Environment variables for the command context.

        Returns:
            A namespace object with exit_code, stdout, stderr, elapsed_time.
        """
        self._logger.info(
            "[docker-real] exec_command sandbox_id=%s timeout=%d cmd=%s",
            self._sandbox_id[:12],
            timeout_in_millis,
            cmd[:200],
        )
        _import_docker()
        start_time = time.monotonic()
        try:
            exit_code, output = self._container.exec_run(
                cmd=cmd,
                environment=envs or {},
                demux=True,
            )
        except Exception as e:
            raise RuntimeError(f"exec_command failed: {e}") from e

        stdout_bytes, stderr_bytes = output
        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        final_exit_code = exit_code if exit_code is not None else -1
        elapsed = time.monotonic() - start_time

        # Return a simple namespace object matching K8s ExecResult convention
        class _ExecResult:
            pass

        result = _ExecResult()
        result.exit_code = final_exit_code
        result.stdout = stdout
        result.stderr = stderr
        result.elapsed_time = elapsed
        return result

    def destroy(self) -> Any:
        """Destroy the sandbox (stop + remove container).

        Stop the container gracefully (30s timeout), then force remove.
        Idempotent: NotFound on stop or remove returns True.
        Best-effort: logs warnings but still returns True after cleanup errors.

        Returns:
            True on success or if container already gone.
        """
        self._logger.info("[docker-real] destroy sandbox_id=%s", self._sandbox_id[:12])
        _import_docker()

        # Step 1: Stop gracefully
        try:
            self._container.stop(timeout=30)
            self._logger.info(
                "[docker-real] Container %s stopped gracefully",
                self._sandbox_id[:12],
            )
        except docker_errors.NotFound:
            self._logger.info(
                "[docker-real] Container %s already removed during stop — idempotent",
                self._sandbox_id[:12],
            )
            return True
        except docker_errors.APIError as e:
            self._logger.warning(
                "[docker-real] Failed to stop container %s gracefully: %s — "
                "will still attempt remove",
                self._sandbox_id[:12],
                e,
            )
        except Exception as e:
            raise RuntimeError(f"destroy stop failed: {e}") from e

        # Step 2: Force remove
        try:
            self._container.remove(force=True)
            self._logger.info(
                "[docker-real] Container %s removed", self._sandbox_id[:12]
            )
        except docker_errors.NotFound:
            self._logger.info(
                "[docker-real] Container %s already removed — idempotent",
                self._sandbox_id[:12],
            )
            return True
        except docker_errors.APIError as e:
            self._logger.warning(
                "[docker-real] Failed to remove container %s: %s — "
                "treating as partial success",
                self._sandbox_id[:12],
                e,
            )
        except Exception as e:
            raise RuntimeError(f"destroy remove failed: {e}") from e

        return True

    def restart(self) -> Any:
        """Restart the sandbox container via container.restart().

        Returns:
            True on successful restart.

        Raises:
            RuntimeError: If the container is not found or restart fails.
        """
        self._logger.info("[docker-real] restart sandbox_id=%s", self._sandbox_id[:12])
        _import_docker()
        try:
            self._container.restart(timeout=30)
        except docker_errors.NotFound:
            raise RuntimeError(
                f"Container {self._sandbox_id[:12]} not found — cannot restart"
            ) from None
        except docker_errors.APIError as e:
            raise RuntimeError(f"restart failed: {e}") from e
        except Exception as e:
            raise RuntimeError(f"restart failed: {e}") from e

        self._logger.info(
            "[docker-real] Container %s restarted successfully",
            self._sandbox_id[:12],
        )
        return True


# ---------------------------------------------------------------------------
# RealDockerSandboxPlugin — production docker-py SDK implementation
# ---------------------------------------------------------------------------


class RealDockerSandboxPlugin(DockerSandboxPlugin):
    """Real Docker sandbox plugin using docker-py SDK.

    Implements the DockerSandboxPlugin Protocol (7 public methods).
    docker client is lazy-initialized internally via _get_client()
    (no constructor parameter per D-08 — DI Container manages as Singleton).

    All public methods raise PaasError on failure (never leak docker.errors.*
    or requests.exceptions.* per D-12).
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._logger = get_logger("plugin-sandbox-docker-real")

    # ------------------------------------------------------------------
    # Lazy client initialization (per D-09, D-13)
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Lazily initialize the docker client with fail-fast ping.

        On first call: imports docker, creates docker.from_env(), and
        verifies connectivity via ping(). Subsequent calls return the
        cached client.

        Returns:
            docker.DockerClient instance.

        Raises:
            PaasError(PLATFORM_UNAVAILABLE): If daemon is unreachable.
        """
        if self._client is not None:
            return self._client

        _import_docker()
        try:
            self._client = docker.from_env()
        except Exception as e:
            raise PaasError(
                ErrorCode.PLATFORM_UNAVAILABLE,
                f"Failed to initialize Docker client: {e}. "
                "Check DOCKER_HOST environment variable and verify "
                "Docker is running.",
                platform_error=e,
            ) from e

        # Fail-fast ping
        try:
            self._client.ping()
        except docker_errors.APIError as e:
            raise PaasError(
                ErrorCode.PLATFORM_UNAVAILABLE,
                f"Docker daemon not reachable: {e}. "
                "Ensure Docker is running and the socket is accessible. "
                "See docs for platform-specific setup "
                "(Linux: Docker Engine, macOS: Colima, Windows: Podman Desktop).",
                platform_error=e,
            ) from e
        except docker_errors.DockerException as e:
            raise PaasError(
                ErrorCode.PLATFORM_UNAVAILABLE,
                f"Docker daemon not reachable: {e}. "
                "Ensure Docker is running and the socket is accessible.",
                platform_error=e,
            ) from e
        except requests_exceptions.ConnectTimeout as e:
            raise PaasError(
                ErrorCode.PLATFORM_UNAVAILABLE,
                f"Docker daemon connection timed out: {e}. "
                "Verify the Docker daemon is running. "
                "Check DOCKER_HOST environment variable if using a non-default socket. "
                "See docs for platform-specific setup: "
                "Linux (Docker Engine /var/run/docker.sock), "
                "macOS (Colima ~/.colima/default/docker.sock), "
                "Windows (Podman Desktop named pipe).",
                platform_error=e,
            ) from e
        except requests_exceptions.ConnectionError as e:
            raise PaasError(
                ErrorCode.PLATFORM_UNAVAILABLE,
                f"Docker daemon not reachable: {e}. "
                "Possible causes:\n"
                "  1. Docker daemon is not running — start Docker and try again\n"
                "  2. Socket permission denied — add your user to the 'docker' group "
                "(Linux: sudo usermod -aG docker $USER, then log out and back in)\n"
                "  3. Socket path is incorrect — check DOCKER_HOST environment variable\n"
                "For platform-specific installation guides, see the project documentation.",
                platform_error=e,
            ) from e

        return self._client

    # ------------------------------------------------------------------
    # Error mapping: docker-py exceptions -> PaasError (per D-05, D-12)
    # ------------------------------------------------------------------

    def _map_docker_error(self, error: Exception) -> PaasError:
        """Map docker-py exceptions to unified ErrorCode via isinstance chain.

        isinstance order matters: subclasses (ImageNotFound) must be checked
        BEFORE their parent classes (NotFound). Per RESEARCH.md Pitfall 3.

        Args:
            error: A docker-py or requests exception.

        Returns:
            PaasError with the appropriate ErrorCode and original error preserved.

        Raises:
            The original error if it is not a recognized docker-py exception type
            (fallthrough — unexpected exceptions propagate up).
        """
        _import_docker()
        # ImageNotFound IS-A NotFound IS-A APIError IS-A DockerException
        if isinstance(error, docker_errors.ImageNotFound):
            return PaasError(
                ErrorCode.CONFIG_INVALID,
                f"Docker image not found: {error}",
                platform_error=error,
            )
        if isinstance(error, docker_errors.NotFound):
            return PaasError(
                ErrorCode.DEVICE_NOT_FOUND,
                f"Docker resource not found: {error}",
                platform_error=error,
            )
        if isinstance(error, docker_errors.APIError):
            explanation = getattr(error, "explanation", "") or ""
            if error.response.status_code == 409:
                return PaasError(
                    ErrorCode.DEVICE_ALREADY_EXISTS,
                    f"Container name conflict: {explanation}",
                    platform_error=error,
                )
            if error.is_server_error():  # 5xx
                if "OCI runtime" in explanation:
                    return PaasError(
                        ErrorCode.DEVICE_CREATION_FAILED,
                        f"Docker OCI runtime error: {explanation}",
                        platform_error=error,
                    )
                return PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    f"Docker daemon error: {explanation}",
                    platform_error=error,
                )
            # 4xx non-409
            return PaasError(
                ErrorCode.CONFIG_INVALID,
                f"Docker API client error: {explanation}",
                platform_error=error,
            )
        # ReadTimeout IS-A Timeout, NOT a ConnectionError.
        if isinstance(error, requests_exceptions.ReadTimeout):
            return PaasError(
                ErrorCode.PLATFORM_UNAVAILABLE,
                f"Docker operation timed out while waiting for response: {error}. "
                "If this occurred during image pull, check network connectivity "
                "or set image_pull_policy=never and manually run 'docker pull'.",
                platform_error=error,
            )
        # ConnectTimeout IS-A ConnectionError — MUST be checked BEFORE ConnectionError
        if isinstance(error, requests_exceptions.ConnectTimeout):
            return PaasError(
                ErrorCode.PLATFORM_UNAVAILABLE,
                f"Docker daemon connection timed out: {error}. "
                "Check DOCKER_HOST environment variable and verify the daemon "
                "is running and accessible from this host.",
                platform_error=error,
            )
        # requests ConnectionError is separate from docker.errors hierarchy
        if isinstance(error, requests_exceptions.ConnectionError):
            return PaasError(
                ErrorCode.PLATFORM_UNAVAILABLE,
                f"Docker daemon unreachable: {error}. "
                "Check that Docker is running and the socket is accessible.",
                platform_error=error,
            )
        if isinstance(error, docker_errors.DockerException):
            return PaasError(
                ErrorCode.PLATFORM_UNAVAILABLE,
                f"Docker error: {error}",
                platform_error=error,
            )
        # Fallthrough: unexpected exception — let it propagate
        raise error

    # ------------------------------------------------------------------
    # Private pipeline helpers (per D-07 — migrated from StandalonePaasService)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_image(image: str) -> tuple[str, str | None]:
        """Split image string into (repository, tag).

        e.g., "alpine:latest" -> ("alpine", "latest"), "nginx" -> ("nginx", None).
        When tag is None, docker-py defaults to "latest".
        """
        if ":" in image:
            idx = image.rfind(":")
            return image[:idx], image[idx + 1 :]
        return image, None

    def _ensure_image_sync(self, image: str, image_pull_policy: str) -> None:
        """Ensure the image is available locally, respecting pull policy.

        Synchronous method (caller wraps in asyncio.to_thread()).

        ALWAYS: pull every time.
        NEVER: verify exists, raise if not found.
        IF_NOT_PRESENT: check local cache first; pull only if missing.
        """
        _import_docker()
        client = self._get_client()

        if image_pull_policy == "always":
            self._logger.info("Pull policy is ALWAYS, pulling image: %s", image)
            try:
                client.images.pull(image)
            except Exception as e:
                raise self._map_docker_error(e) from e
        elif image_pull_policy == "never":
            self._logger.info("Pull policy is NEVER, checking local image: %s", image)
            try:
                client.images.get(image)
            except docker_errors.ImageNotFound:
                raise PaasError(
                    ErrorCode.CONFIG_INVALID,
                    f"Image '{image}' not found locally and pull policy is NEVER",
                ) from None
            except docker_errors.NotFound:
                raise PaasError(
                    ErrorCode.CONFIG_INVALID,
                    f"Image '{image}' not found locally and pull policy is NEVER",
                ) from None
            except Exception as e:
                raise self._map_docker_error(e) from e
        else:  # if_not_present (default)
            self._logger.info(
                "Pull policy is IF_NOT_PRESENT, checking local image: %s", image
            )
            try:
                client.images.get(image)
                self._logger.info(
                    "Image already present locally, skipping pull: %s", image
                )
            except (docker_errors.ImageNotFound, docker_errors.NotFound):
                self._logger.info("Image not found locally, pulling: %s", image)
                try:
                    client.images.pull(image)
                except Exception as e:
                    raise self._map_docker_error(e) from e
            except Exception as e:
                raise self._map_docker_error(e) from e

    def _create_container_sync(
        self,
        container_name: str,
        image: str,
        container_port: int,
        envs: dict[str, str] | None,
        cpu_limit: str | None,
        memory_limit: str | None,
        tenant_name: str,
        template_id: int,
    ) -> Any:
        """Create a Docker container with labels, ports, and resource limits.

        Synchronous method (caller wraps in asyncio.to_thread()).

        Returns:
            docker.models.containers.Container instance.
        """
        _import_docker()
        client = self._get_client()

        labels = {
            "baas.tenant": tenant_name or "unknown",
            "baas.template_id": str(template_id),
        }

        create_kwargs: dict[str, Any] = {
            "image": image,
            "name": container_name,
            "labels": labels,
            "ports": {f"{container_port}/tcp": None},
            "environment": envs or {},
            "detach": True,
            "network_mode": "bridge",
        }

        if cpu_limit is not None:
            try:
                create_kwargs["nano_cpus"] = int(float(cpu_limit) * 1e9)
            except (ValueError, TypeError):
                pass
        if memory_limit is not None:
            create_kwargs["mem_limit"] = memory_limit

        self._logger.info(
            "Creating container '%s' from image '%s'", container_name, image
        )
        try:
            container = client.containers.create(**create_kwargs)
        except Exception as e:
            raise self._map_docker_error(e) from e
        self._logger.info(
            "Container created: id=%s, name=%s", container.id, container_name
        )
        return container

    def _extract_host_port_sync(self, container: Any, container_port: int) -> int:
        """Extract the auto-assigned host port after container start.

        Calls container.reload() first to get fresh attrs from the daemon.
        Synchronous method (caller wraps in asyncio.to_thread()).
        """
        self._logger.debug(
            "Reloading container attrs to extract host port (container_port=%d)",
            container_port,
        )
        container.reload()
        port_bindings = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
        key = f"{container_port}/tcp"
        mappings = port_bindings.get(key)
        if not mappings or not isinstance(mappings, list) or len(mappings) == 0:
            raise PaasError(
                ErrorCode.DEVICE_UNAVAILABLE,
                f"Container port {container_port} has no host binding after start. "
                "Verify the container port mapping is correct and not conflicted.",
            )
        host_port_str = mappings[0].get("HostPort", "0")
        host_port = int(host_port_str)
        self._logger.info(
            "Extracted host port: %d (container port %d)",
            host_port,
            container_port,
        )
        return host_port

    def _poll_health(
        self,
        host_port: int,
        health_endpoint: str,
        health_timeout_seconds: int,
    ) -> None:
        """Poll the container health endpoint with exponential backoff.

        - 1s cold start buffer before first poll
        - Exponential backoff: 1s, 2s, 4s, 8s, 16s, 16s... capped at 16s
        - Total timeout: health_timeout_seconds (default 120s)
        - Healthy = HTTP 200 OK
        - Timeout raises DEVICE_NOT_READY

        Synchronous method — uses httpx (not aiohttp, matching plugin conventions).
        """
        health_url = f"http://localhost:{host_port}{health_endpoint}"
        self._logger.info(
            "Starting health check polling: %s (timeout=%ds, endpoint=%s)",
            health_url,
            health_timeout_seconds,
            health_endpoint,
        )

        deadline = time.monotonic() + health_timeout_seconds

        # Cold start buffer: 1s
        time.sleep(1.0)

        interval = 1.0
        while True:
            try:
                resp = httpx.get(health_url, timeout=5.0)
                if resp.status_code == 200:
                    self._logger.info(
                        "Health check passed (HTTP 200) on port %d", host_port
                    )
                    return
                self._logger.debug(
                    "Health check received non-200 status: %d", resp.status_code
                )
            except Exception:
                self._logger.debug("Health check connection failed, will retry")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PaasError(
                    ErrorCode.DEVICE_NOT_READY,
                    f"Container did not become healthy within "
                    f"{health_timeout_seconds}s.",
                )

            self._logger.debug(
                "Health check retrying in %.1fs (remaining: %.1fs)",
                interval,
                remaining,
            )
            time.sleep(interval)
            interval = min(interval * 2, 16.0)

    # ------------------------------------------------------------------
    # Public methods (7, per D-01 — DockerSandboxPlugin Protocol)
    # ------------------------------------------------------------------

    def create_device(
        self,
        template_id: int,
        template_uuid: str,
        tenant_name: str,
        container_name: str,
        image: str,
        container_port: int,
        envs: dict[str, str] | None = None,
        cpu_limit: str | None = None,
        memory_limit: str | None = None,
        image_pull_policy: str = "if_not_present",
        health_endpoint: str = "/health",
        health_timeout_seconds: int = 120,
    ) -> RealDockerSandbox:
        """Create a new Docker sandbox with full 5-step pipeline.

        Pipeline:
          1. Pull image (respecting image_pull_policy)
          2. Create container with port mapping and resource limits
          3. Start container
          4. Poll health endpoint until ready (up to health_timeout_seconds)
          5. Extract assigned host port

        Partial cleanup per D-06: if container is created but start fails,
        the container is force-removed. If health check times out, the
        container is preserved for debugging.

        Returns:
            RealDockerSandbox ready for use.

        Raises:
            PaasError: On creation failure (all mapped via _map_docker_error).
        """
        self._logger.info(
            "[docker-real] create_device: image=%s, container=%s, tenant=%s, "
            "template_id=%s",
            image,
            container_name,
            tenant_name,
            template_id,
        )

        container = None
        try:
            # Step 1: Pull image (respects pull policy)
            self._logger.info(
                "Step 1/5: Pulling image %s (policy=%s)", image, image_pull_policy
            )
            self._ensure_image_sync(image, image_pull_policy)

            # Step 2: Create container
            self._logger.info("Step 2/5: Creating container %s", container_name)
            container = self._create_container_sync(
                container_name=container_name,
                image=image,
                container_port=container_port,
                envs=envs,
                cpu_limit=cpu_limit,
                memory_limit=memory_limit,
                tenant_name=tenant_name,
                template_id=template_id,
            )

            # Step 3: Start container
            self._logger.info("Step 3/5: Starting container %s", container.id[:12])
            try:
                container.start()
            except Exception as e:
                raise self._map_docker_error(e) from e

            # Step 4: Health check polling
            self._logger.info(
                "Step 4/5: Extracting host port for container %s", container.id[:12]
            )
            host_port = self._extract_host_port_sync(container, container_port)
            self._logger.info(
                "Step 4/5: Health check polling for container %s on port %d",
                container.id[:12],
                host_port,
            )
            self._poll_health(host_port, health_endpoint, health_timeout_seconds)

            # Step 5: Return RealDockerSandbox
            self._logger.info(
                "Step 5/5: Sandbox ready — container_id=%s, host_port=%d",
                container.id[:12],
                host_port,
            )
            return RealDockerSandbox(
                sandbox_id=container.id,
                container=container,
                host_port=host_port,
            )

        except PaasError:
            # Best-effort partial cleanup per D-06
            if container is not None:
                self._logger.warning(
                    "Cleaning up container %s after create failure", container_name
                )
                try:
                    container.remove(force=True)
                except Exception as cleanup_err:
                    self._logger.warning(
                        "Failed to clean up container %s: %s",
                        container_name,
                        cleanup_err,
                    )
            raise

        except Exception as e:
            # Best-effort partial cleanup per D-06
            if container is not None:
                self._logger.warning(
                    "Cleaning up container %s after create failure", container_name
                )
                try:
                    container.remove(force=True)
                except Exception as cleanup_err:
                    self._logger.warning(
                        "Failed to clean up container %s: %s",
                        container_name,
                        cleanup_err,
                    )
            raise self._map_docker_error(e) from e

    def destroy_device(self, paas_device_id: str) -> bool:
        """Destroy a Docker sandbox by container ID.

        Idempotent: container not found returns True (no error).
        Best-effort: stop failure logs warning but still attempts remove;
        remove failure logs warning and returns True (partial success).

        Args:
            paas_device_id: Bare container ID or name (no @template_id suffix).

        Returns:
            True on success or if container already does not exist.
        """
        self._logger.info("[docker-real] destroy_device: %s", paas_device_id[:12])
        _import_docker()
        client = self._get_client()

        try:
            container = client.containers.get(paas_device_id)
        except docker_errors.NotFound:
            self._logger.info(
                "Container %s not found — already destroyed (idempotent)",
                paas_device_id[:12],
            )
            return True

        # Stop gracefully
        try:
            container.stop(timeout=30)
            self._logger.info("Container %s stopped gracefully", paas_device_id[:12])
        except docker_errors.NotFound:
            self._logger.info(
                "Container %s already removed during stop — idempotent",
                paas_device_id[:12],
            )
            return True
        except docker_errors.APIError as e:
            self._logger.warning(
                "Failed to stop container %s gracefully: %s — "
                "will still attempt remove",
                paas_device_id[:12],
                e,
            )
        except Exception as e:
            self._logger.warning(
                "Unexpected error stopping container %s: %s — "
                "will still attempt remove",
                paas_device_id[:12],
                e,
            )

        # Force remove
        try:
            container.remove(force=True)
            self._logger.info("Container %s removed", paas_device_id[:12])
        except docker_errors.NotFound:
            self._logger.info(
                "Container %s already removed — idempotent", paas_device_id[:12]
            )
            return True
        except docker_errors.APIError as e:
            self._logger.warning(
                "Failed to remove container %s: %s — treating as partial success",
                paas_device_id[:12],
                e,
            )
        except Exception as e:
            self._logger.warning(
                "Unexpected error removing container %s: %s — "
                "treating as partial success",
                paas_device_id[:12],
                e,
            )

        return True

    def connect_device(self, sandbox_id: str) -> RealDockerSandbox:
        """Connect to an existing Docker sandbox by container ID.

        Reconnects to an already-running container for operations like
        exec_command, get_info, restart, or destroy.

        Args:
            sandbox_id: The Docker container ID.

        Returns:
            RealDockerSandbox for the existing container.

        Raises:
            PaasError(DEVICE_NOT_FOUND): If the container is not found.
        """
        self._logger.info(
            "[docker-real] connect_device: sandbox_id=%s", sandbox_id[:12]
        )
        _import_docker()
        client = self._get_client()

        try:
            container = client.containers.get(sandbox_id)
        except docker_errors.NotFound:
            raise PaasError(
                ErrorCode.DEVICE_NOT_FOUND,
                f"Container {sandbox_id[:12]} not found",
            ) from None
        except Exception as e:
            raise self._map_docker_error(e) from e

        # Extract host_port from container attrs
        port_bindings = container.attrs.get("HostConfig", {}).get("PortBindings", {})
        host_port = 0
        if port_bindings:
            for binding_list in port_bindings.values():
                if binding_list and isinstance(binding_list, list):
                    host_port_str = binding_list[0].get("HostPort", "0")
                    try:
                        host_port = int(host_port_str)
                    except (ValueError, TypeError):
                        host_port = 0
                    break

        return RealDockerSandbox(
            sandbox_id=sandbox_id,
            container=container,
            host_port=host_port,
        )

    def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
    ) -> Any:
        """Resolve WebSocket connection info for a Docker sandbox.

        Returns localhost URL since Docker containers expose ports on the
        host. No Docker daemon query needed per D-02.

        Args:
            paas_device_id: Container ID (used in target identifier).
            port: Host port bound to the container.
            path: WebSocket path (e.g., /api/openclaw/ws).

        Returns:
            WsConnectionInfo with ws://127.0.0.1:{port}{path}.
        """
        normalized_path = "/" + path.lstrip("/")
        ws_url = f"ws://127.0.0.1:{port}{normalized_path}"
        from secbaas.community.api.bot_runtime import WsConnectionInfo

        return WsConnectionInfo(
            ws_url=ws_url,
            token="",
            target=f"DOCKER_{paas_device_id}:{port}",
            expires_at=datetime.max,
        )

    def resolve_invoke_http_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
    ) -> Any:
        """Resolve HTTP connection info for a Docker sandbox.

        Returns localhost URL since Docker containers expose ports on the
        host. No Docker daemon query needed per D-02.

        Args:
            paas_device_id: Container ID (used in target identifier).
            port: Host port bound to the container.
            path: HTTP path (e.g., /api/health).

        Returns:
            HttpConnectionInfo with http://127.0.0.1:{port}{path}.
        """
        normalized_path = "/" + path.lstrip("/")
        http_url = f"http://127.0.0.1:{port}{normalized_path}"
        from secbaas.community.api.bot_runtime import HttpConnectionInfo

        return HttpConnectionInfo(
            http_url=http_url,
            token="",
            target=f"DOCKER_{paas_device_id}:{port}",
        )

    def invoke_http_in_device(
        self,
        paas_device_id: str,
        method: str,
        port: int,
        path: str,
        query_string: str | None = None,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> dict[str, Any]:
        """Forward HTTP request to container service at 127.0.0.1:{port}{path}.

        Creates an independent httpx.Client per call (timeout 30s) to forward
        the HTTP request to the localhost-mapped container port.
        Request body is forwarded as raw bytes; response body is base64-encoded.

        Args:
            paas_device_id: Container ID (for logging, not used in URL).
            method: HTTP method (GET, POST, PUT, DELETE, etc.).
            port: Host port bound to the container.
            path: Request path (e.g., /api/v1/health).
            query_string: Optional query string.
            headers: HTTP headers dict.
            body: Raw request body bytes.

        Returns:
            Dict with keys: status_code (int), headers (dict),
            body (base64-encoded str).

        Raises:
            PaasError(PLATFORM_UNAVAILABLE): On httpx.HTTPError.
        """
        url = f"http://127.0.0.1:{port}{path}"
        if query_string:
            url += query_string

        self._logger.debug(
            "Invoking HTTP on Docker device: id=%s method=%s url=%s",
            paas_device_id[:12],
            method,
            url,
        )

        try:
            httpx_client = httpx.Client()
            try:
                response = httpx_client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    content=body,
                    timeout=30.0,
                )
                return {
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                    "body": base64.b64encode(response.content).decode("ascii"),
                }
            finally:
                httpx_client.close()
        except httpx.HTTPError as e:
            self._logger.error(
                "Docker invoke_http_in_device failed for %s: %s",
                paas_device_id[:12],
                e,
            )
            raise PaasError(
                ErrorCode.PLATFORM_UNAVAILABLE,
                f"HTTP forward to container {paas_device_id[:12]} failed: {e}",
            ) from e

    def close(self) -> None:
        """No-op close. Docker client lifecycle is managed by the DI container."""
        self._logger.info(
            "[docker-real] plugin close (no-op — client managed by DI container)"
        )
