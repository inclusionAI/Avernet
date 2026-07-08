"""K8s platform PaaS adapter.

Delegates K8s device lifecycle operations to a K8sSandboxPlugin instance.
Core model: one Bot owns one StatefulSet, one Device maps to one Pod (ordinal).
Multiple devices share the same StatefulSet as ordinal replicas.

Lifecycle:
- create_device(): lazy-creates StatefulSet (replicas=1) on first call;
  scales replicas += 1 on subsequent calls.
- destroy_device(): scales replicas -= 1; deletes StatefulSet at replicas=0.
- Per-StatefulSet asyncio.Lock prevents concurrent create/destroy races (LFC-02).
- _map_error() translates stub RuntimeError("(XXX)") to PaasError.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any

from secbaas.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.api.device_manage import (
    CommandResult,
    DeviceCreateConfig,
    ErrorCode,
    K8sCreationResult,
    K8sCredentials,
    K8sDeviceInfo,
    PaasError,
    PodInfo,
)
from secbaas.api.tenant_manage import TenantType
from secbaas.logger import get_logger
from secbaas.spi.sandbox.k8s import K8sSandboxPlugin

from ._paas_service import PaasService

if TYPE_CHECKING:
    from secbaas.api.device_manage import OutBoundOperationRule
    from secbaas.api.health_check.bot import TTLInfo

_SANDBOX_ID_ORDINAL = re.compile(r"^(.+)-(\d+)$")


def _sandbox_id_to_paas_device_id(sandbox_id: str) -> str:
    """Convert plugin sandbox_id to paas_device_id format.

    Real plugin returns "{statefulset_name}-{ordinal}" (e.g., "my-app-3").
    This converts to paas_device_id format "{statefulset_name}--{ordinal}"
    (e.g., "my-app--3").

    Stub plugin returns random UUIDs (no trailing ordinal). These are
    returned as-is.

    Args:
        sandbox_id: Plugin-returned sandbox identifier.

    Returns:
        paas_device_id in "{name}--{ordinal}" format, or the original
        sandbox_id if it does not match the ordinal pattern.
    """
    m = _SANDBOX_ID_ORDINAL.match(sandbox_id)
    if m:
        return f"{m.group(1)}--{m.group(2)}"
    return sandbox_id


class K8sPaasService(PaasService):
    """K8s platform PaaS adapter — delegates to K8sSandboxPlugin.

    Delegates all core operations to a K8sSandboxPlugin instance.
    Manages StatefulSet lifecycle (lazy-create, scale up/down, destroy),
    per-StatefulSet concurrency control via asyncio.Lock, and error
    translation via _map_error().

    Attributes:
        _plugin: K8sSandboxPlugin implementing K8s API operations.
        _credentials: K8sCredentials with template/namespace/image config.
        _locks: Per-StatefulSet asyncio.Lock dict for concurrency control.
    """

    def __init__(self, plugin: K8sSandboxPlugin, credentials: K8sCredentials):
        """Initialize K8sPaasService with plugin and credentials.

        Args:
            plugin: K8sSandboxPlugin for K8s API operations (real or stub).
            credentials: K8sCredentials with template/namespace/image config.

        Raises:
            ValueError: If plugin or credentials is None.
        """
        if plugin is None:
            raise ValueError("plugin is required")
        if credentials is None:
            raise ValueError("credentials is required")
        self._plugin = plugin
        self._credentials = credentials
        self._logger = get_logger("core-service")
        self._locks: dict[str, asyncio.Lock] = {}
        self._statefulset_replicas: dict[str, int] = {}

    # ------------------------------------------------------------------
    # ABC metadata methods
    # ------------------------------------------------------------------

    async def get_credentials(self) -> K8sCredentials:
        """Get the K8s credentials used by this service instance.

        Returns:
            K8sCredentials containing template_id, namespace, image, etc.
        """
        return self._credentials

    async def get_platform_type(self) -> TenantType:
        """Return the K8s platform type.

        Returns:
            TenantType.K8S enum value.
        """
        return TenantType.K8S

    # ------------------------------------------------------------------
    # Internal helper methods
    # ------------------------------------------------------------------

    def _get_lock(self, statefulset_name: str) -> asyncio.Lock:
        """Get or create a per-StatefulSet asyncio.Lock for concurrency control.

        Per D-03, LFC-02: Two concurrent create_device() calls for the same
        StatefulSet both read replicas=1 and both patch to 2 — actual should
        be 3. The Lock serializes read-check-write cycles.

        Args:
            statefulset_name: K8s StatefulSet name used as lock key.

        Returns:
            asyncio.Lock for the specified StatefulSet.
        """
        if statefulset_name not in self._locks:
            self._locks[statefulset_name] = asyncio.Lock()
        return self._locks[statefulset_name]

    def _map_error(self, error: Exception, default_code: ErrorCode) -> PaasError:
        """Map stub RuntimeError (or real ApiException) to PaasError.

        Dual-path error translator per D-07, D-08. Phase 4 (stub) parses
        HTTP status codes from RuntimeError strings containing "(XXX)".
        Phase 7 (real) will read kubernetes.client.exceptions.ApiException.status.

        The mapping table is identical between Phase 4 and Phase 7; only
        the status code source differs.

        HTTP Status -> ErrorCode mapping table (D-07):
        - 404 -> DEVICE_NOT_FOUND (StatefulSet not found)
        - 409 -> DEVICE_UNAVAILABLE (concurrent scale conflict)
        - 422 -> CONFIG_INVALID (invalid resource spec)
        - 429 -> RATE_LIMITED (API Server rate limit)
        - 500/502/503 -> PLATFORM_UNAVAILABLE (API Server error)
        - Other 4xx -> CONFIG_INVALID (client config error)
        - Other 5xx -> PLATFORM_ERROR (server error)
        - Non-HTTP (no paren code) -> default_code (network error/timeout)

        Args:
            error: Original exception from plugin (RuntimeError or ApiException).
            default_code: Context-aware fallback ErrorCode when no HTTP status
                is found (e.g., DEVICE_CREATION_FAILED for create_device,
                COMMAND_FAILED for execute_command). Per D-08.

        Returns:
            PaasError with unified error code, message, and original exception.
        """
        msg = str(error)
        match = re.search(r"\((\d{3})\)", msg)
        if not match:
            return PaasError(default_code, msg, error)

        status = int(match.group(1))
        if status == 404:
            return PaasError(ErrorCode.DEVICE_NOT_FOUND, msg, error)
        if status == 409:
            return PaasError(ErrorCode.DEVICE_UNAVAILABLE, msg, error)
        if status == 422:
            return PaasError(ErrorCode.CONFIG_INVALID, msg, error)
        if status == 429:
            return PaasError(ErrorCode.RATE_LIMITED, msg, error)
        if status in (500, 502, 503):
            return PaasError(ErrorCode.PLATFORM_UNAVAILABLE, msg, error)
        if 400 <= status < 500:
            return PaasError(ErrorCode.CONFIG_INVALID, msg, error)
        if 500 <= status < 600:
            return PaasError(ErrorCode.PLATFORM_ERROR, msg, error)

        return PaasError(default_code, msg, error)

    def _derive_statefulset_name(self, config: DeviceCreateConfig) -> str:
        """Derive a sanitized K8s StatefulSet name from config.

        Uses config.name if set and non-empty, otherwise falls back to
        self._credentials.tenant_name. Sanitizes to RFC 1123 compliance
        (lowercase, replace non-alphanumeric with '-', strip trailing '-').

        Args:
            config: DeviceCreateConfig with optional name field.

        Returns:
            RFC 1123-safe StatefulSet name string.
        """
        raw = (config.name or "").strip()
        if not raw:
            raw = self._credentials.tenant_name or "k8s-device"
        # Sanitize to RFC 1123: lowercase, replace non-alphanumeric (except '-')
        # with '-', collapse consecutive '-', strip leading/trailing '-'.
        sanitized = re.sub(r"[^a-z0-9\-]", "-", raw.lower())
        sanitized = re.sub(r"-+", "-", sanitized)
        sanitized = sanitized.strip("-")
        if not sanitized:
            sanitized = "k8s-device"
        return sanitized

    def _build_metadata(self, config: DeviceCreateConfig) -> dict[str, str]:
        """Build K8s metadata labels for StatefulSet creation.

        Per LFC-09 (full label set) and LFC-08 (progressDeadlineSeconds).
        Labels are immutable-once-set on StatefulSet creation.

        Args:
            config: DeviceCreateConfig for device context (not currently used
                for metadata; labels derive from credentials).

        Returns:
            Dict with 5 standard labels + progressDeadlineSeconds annotation.
        """
        return {
            "app.kubernetes.io/managed-by": "secbaas",
            "app.kubernetes.io/part-of": "secbaas",
            "app.kubernetes.io/component": "bot-runtime",
            "tenant": self._credentials.tenant_name or "",
            "template-uuid": self._credentials.template_uuid,
            "progressDeadlineSeconds": "600",
        }

    def _generate_envoy_yaml(self, rules: list) -> str:
        """Generate Envoy proxy configuration YAML from K8sOutboundProxyRule list.

        Per D-06b: K8sPaasService is the sole owner of Envoy YAML generation.
        The plugin receives the pre-generated YAML string via the envoy_yaml
        parameter — it does NOT import rule models or call yaml.dump().
        """
        import yaml

        routes = [
            {
                "match": {
                    "prefix": r.url_pattern
                    if hasattr(r, "url_pattern")
                    else r["url_pattern"]
                },
                "route": {
                    "cluster": "passthrough",
                    "prefix_rewrite": r.rewrite_target
                    if hasattr(r, "rewrite_target")
                    else r["rewrite_target"],
                },
            }
            for r in rules
        ]

        config = {
            "admin": {
                "address": {
                    "socket_address": {
                        "address": "127.0.0.1",
                        "port_value": 15000,
                    }
                }
            },
            "static_resources": {
                "listeners": [
                    {
                        "name": "outbound_proxy_listener",
                        "address": {
                            "socket_address": {
                                "address": "0.0.0.0",
                                "port_value": 15001,
                            }
                        },
                        "filter_chains": [
                            {
                                "filters": [
                                    {
                                        "name": "envoy.filters.network.http_connection_manager",
                                        "typed_config": {
                                            "@type": "type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager",
                                            "stat_prefix": "outbound_proxy",
                                            "codec_type": "AUTO",
                                            "route_config": {
                                                "name": "outbound_routes",
                                                "virtual_hosts": [
                                                    {
                                                        "name": "default",
                                                        "domains": ["*"],
                                                        "routes": routes,
                                                    }
                                                ],
                                            },
                                            "http_filters": [
                                                {
                                                    "name": "envoy.filters.http.router",
                                                    "typed_config": {
                                                        "@type": "type.googleapis.com/envoy.extensions.filters.http.router.v3.Router",
                                                    },
                                                }
                                            ],
                                        },
                                    }
                                ]
                            }
                        ],
                    }
                ],
                "clusters": [
                    {
                        "name": "passthrough",
                        "type": "ORIGINAL_DST",
                        "lb_policy": "CLUSTER_PROVIDED",
                        "connect_timeout": "5s",
                        "original_dst_lb_config": {"use_http_header": True},
                    }
                ],
            },
        }
        return yaml.dump(config, default_flow_style=False, sort_keys=False)

    # ------------------------------------------------------------------
    # TIER 1: Full implementations — 7 method delegates
    # ------------------------------------------------------------------

    async def create_device(self, config: DeviceCreateConfig) -> K8sCreationResult:
        """Create or scale a K8s device.

        First call: creates StatefulSet (replicas=1) via plugin.create_device().
        Subsequent calls: scales replicas up via sandbox.update(replicas=N+1).
        Returns paas_device_id in format "{statefulset_name}--{ordinal}" (D-01).

        Per D-01: Bot <-> StatefulSet (1:1), Device <-> Pod/ordinal (1:1).
        Per D-03: per-StatefulSet asyncio.Lock prevents concurrent create/destroy races.

        Args:
            config: DeviceCreateConfig with optional name field for statefulset name.

        Returns:
            K8sCreationResult with device_id in "{statefulset_name}--{ordinal}" format.

        Raises:
            PaasError: With DEVICE_CREATION_FAILED if creation or scaling fails.
        """
        creds = self._credentials
        ns = creds.namespace or "default"
        statefulset_name = self._derive_statefulset_name(config)
        lock = self._get_lock(statefulset_name)

        # Generate envoy_yaml from credentials rules (D-06b).
        # The plugin receives pre-generated YAML, never raw rule objects.
        rules = creds.outbound_proxy_rules or []
        envoy_yaml = self._generate_envoy_yaml(rules) if rules else None

        async with lock:
            try:
                # Check existence by connecting to the first Pod (ordinal 0).
                # Per StatefulSet naming, Pod 0 always exists when the
                # StatefulSet exists (replicas >= 1).
                sandbox = await asyncio.to_thread(
                    self._plugin.connect_device, f"{statefulset_name}-0", ns
                )
                # StatefulSet exists — scale up
                current_replicas = self._statefulset_replicas.get(statefulset_name, 1)
                new_replicas = current_replicas + 1
                await asyncio.to_thread(sandbox.update, replicas=new_replicas)
                self._statefulset_replicas[statefulset_name] = new_replicas
                # Derive ordinal from the sandbox_id returned by connect_device
                # (e.g., "my-app-0" → use scale-up ordinal)
                self._logger.info(
                    "K8s device scaled up: statefulset=%s replicas=%d",
                    statefulset_name,
                    new_replicas,
                )
            except Exception as e:
                mapped = self._map_error(e, ErrorCode.DEVICE_CREATION_FAILED)
                if mapped.code == ErrorCode.DEVICE_NOT_FOUND:
                    # StatefulSet doesn't exist — create new (lazy-create)
                    metadata = self._build_metadata(config)
                    sandbox = await asyncio.to_thread(
                        self._plugin.create_device,
                        template_id=creds.template_id,
                        template_uuid=creds.template_uuid,
                        tenant_name=creds.tenant_name or "",
                        namespace=ns,
                        image=creds.image or "nginx:latest",
                        cpu_request=creds.cpu_request or "500m",
                        cpu_limit=creds.cpu_limit or "1",
                        memory_request=creds.memory_request or "512Mi",
                        memory_limit=creds.memory_limit or "1Gi",
                        envs=getattr(config, "envs", None),
                        metadata=metadata,
                        envoy_yaml=envoy_yaml,
                    )
                    self._statefulset_replicas[statefulset_name] = 1
                    self._logger.info(
                        "K8s device created (new StatefulSet): statefulset=%s replicas=1 sandbox_id=%s",
                        statefulset_name,
                        sandbox.sandbox_id,
                    )
                else:
                    raise mapped

        # D-01: device_id format {statefulset_name}--{ordinal}
        ordinal = self._statefulset_replicas[statefulset_name] - 1
        paas_device_id = f"{statefulset_name}--{ordinal}"
        return K8sCreationResult(device_id=paas_device_id)

    async def destroy_device(self, paas_device_id: str) -> bool:
        """Scale down or delete a K8s StatefulSet.

        Per D-04: scales replicas -= 1; deletes StatefulSet at replicas == 0.
        Per D-05: returns True for 404 (idempotent: already gone).

        Args:
            paas_device_id: paas_device_id in format "{statefulset_name}--{ordinal}".

        Returns:
            True if device destroyed or already gone (idempotent).

        Raises:
            PaasError: With CONFIG_INVALID if paas_device_id malformed.
            PaasError: With DEVICE_DESTROY_FAILED if destroy fails (non-404).
        """
        parts = paas_device_id.split("--", maxsplit=1)
        if len(parts) != 2:
            raise PaasError(
                ErrorCode.CONFIG_INVALID,
                f"Invalid paas_device_id: {paas_device_id}",
            )
        statefulset_name = parts[0]
        ns = self._credentials.namespace or "default"
        lock = self._get_lock(statefulset_name)

        async with lock:
            # Check existence via the first Pod (ordinal 0).
            # Per StatefulSet naming, Pod 0 always exists when the
            # StatefulSet exists (replicas >= 1).
            pod_name = f"{statefulset_name}-0"
            try:
                sandbox = await asyncio.to_thread(
                    self._plugin.connect_device, pod_name, ns
                )
            except Exception as e:
                mapped = self._map_error(e, ErrorCode.DEVICE_DESTROY_FAILED)
                if mapped.code == ErrorCode.DEVICE_NOT_FOUND:
                    return True
                raise mapped

            # Delete the StatefulSet (sandbox.destroy handles per-Pod scale-down
            # and eventual StatefulSet deletion via Foreground propagation).
            await asyncio.to_thread(sandbox.destroy)
            self._logger.info(
                "K8s StatefulSet destroyed: statefulset=%s", statefulset_name
            )

        return True

    async def restart_device(self, paas_device_id: str) -> bool:
        """Restart a K8s device via rolling restart.

        Delegates to sandbox.restart() which patches the Pod template
        annotation to trigger a rolling restart.

        Args:
            paas_device_id: paas_device_id in format "{statefulset_name}--{ordinal}".

        Returns:
            True if restart was initiated successfully.

        Raises:
            PaasError: With DEVICE_UNAVAILABLE if restart fails.
            PaasError: With CONFIG_INVALID if paas_device_id malformed.
        """
        pod_name = self._to_pod_name(paas_device_id)
        ns = self._credentials.namespace or "default"

        try:
            sandbox = await asyncio.to_thread(self._plugin.connect_device, pod_name, ns)
            await asyncio.to_thread(sandbox.restart)
            self._logger.info("K8s device restarted: pod=%s", pod_name)
            return True
        except Exception as e:
            raise self._map_error(e, ErrorCode.DEVICE_UNAVAILABLE)

    async def update_device(
        self, paas_device_id: str, config: DeviceCreateConfig | None = None
    ) -> bool:
        """Update a K8s device configuration via StatefulSet spec patch.

        Delegates to sandbox.update() which patches the StatefulSet spec.
        Semantically distinct from restart_device: update applies config
        changes (envs, resource limits, image), while restart only restarts
        the container process without changing config.

        Args:
            paas_device_id: paas_device_id in format "{statefulset_name}--{ordinal}".
            config: Optional DeviceCreateConfig with update fields.

        Returns:
            True if update was initiated successfully.

        Raises:
            PaasError: With DEVICE_UNAVAILABLE if update fails.
            PaasError: With CONFIG_INVALID if paas_device_id malformed.
        """
        pod_name = self._to_pod_name(paas_device_id)
        ns = self._credentials.namespace or "default"

        # Build update kwargs from config if provided
        kwargs: dict[str, Any] = {}
        if config is not None:
            if hasattr(config, "envs") and config.envs is not None:
                kwargs["envs"] = config.envs
            if hasattr(config, "name") and config.name is not None:
                kwargs["name"] = config.name

        try:
            sandbox = await asyncio.to_thread(self._plugin.connect_device, pod_name, ns)
            await asyncio.to_thread(sandbox.update, **kwargs)
            self._logger.info(
                "K8s device updated: pod=%s kwargs=%s",
                pod_name,
                kwargs,
            )
            return True
        except Exception as e:
            raise self._map_error(e, ErrorCode.DEVICE_UNAVAILABLE)

    async def execute_command(
        self,
        paas_device_id: str,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> CommandResult:
        """Execute a command on the K8s device via pod exec.

        Delegates to sandbox.exec_command() with timeout conversion
        (seconds to milliseconds). Returns a unified CommandResult
        dataclass built from the stub/real command result.

        Args:
            paas_device_id: paas_device_id in format "{statefulset_name}--{ordinal}".
            cmd: Command string to execute.
            env: Optional environment variables for the command context.
            timeout_seconds: Maximum execution time in seconds (default: 30).

        Returns:
            CommandResult with exit_code, stdout, stderr, execution_time_ms.

        Raises:
            PaasError: With COMMAND_FAILED on execution failure.
            PaasError: With DEVICE_UNAVAILABLE if device not reachable.
            PaasError: With CONFIG_INVALID if paas_device_id malformed.
        """
        pod_name = self._to_pod_name(paas_device_id)
        ns = self._credentials.namespace or "default"
        timeout_in_millis = timeout_seconds * 1000

        try:
            sandbox = await asyncio.to_thread(self._plugin.connect_device, pod_name, ns)
            result = await asyncio.to_thread(
                sandbox.exec_command, cmd, timeout_in_millis, env
            )
            self._logger.info(
                "K8s command executed: pod=%s exit_code=%s",
                pod_name,
                getattr(result, "exit_code", "?"),
            )
            return CommandResult(
                exit_code=getattr(result, "exit_code", 0),
                stdout=getattr(result, "stdout", ""),
                stderr=getattr(result, "stderr", ""),
                execution_time_ms=int(getattr(result, "elapsed_time", 0) * 1000)
                if hasattr(result, "elapsed_time")
                else 0,
                command=cmd,
                env=env,
            )
        except Exception as e:
            raise self._map_error(e, ErrorCode.COMMAND_FAILED)

    async def list_instances(self, params: dict[str, Any]) -> list[Any]:
        """List K8s sandbox instances (StatefulSets) matching criteria.

        Delegates to plugin.list_instances() with namespace and label_selector
        extracted from the params dict.

        Args:
            params: Query parameters dict. Supports:
                - namespace: K8s namespace (falls back to credentials.namespace).
                - label_selector: Optional K8s label selector string.

        Returns:
            List of StatefulSet summary objects from the plugin.
        """
        ns = params.get("namespace", self._credentials.namespace or "default")
        label_selector = params.get("label_selector")
        return await asyncio.to_thread(self._plugin.list_instances, ns, label_selector)

    async def invoke_http_in_device(
        self,
        paas_device_id: str,
        method: str,
        port: int,
        path: str,
        query_string: str | None,
        headers: dict[str, str],
        body: bytes,
    ) -> dict[str, Any]:
        """Invoke HTTP request directly on a K8s device via Pod IP.

        Delegates to plugin.invoke_http_in_device() which resolves Pod IP
        and performs the HTTP call.

        Args:
            paas_device_id: paas_device_id in format "{statefulset_name}--{ordinal}".
            method: HTTP method (GET, POST, PUT, DELETE, etc.).
            port: Target port on the device.
            path: Request path (e.g., /api/v1/users).
            query_string: Optional query string including leading '?'.
            headers: HTTP headers dict.
            body: Raw request body bytes.

        Returns:
            Dict with keys: status_code (int), headers (dict), body (base64 str).

        Raises:
            PaasError: With DEVICE_UNAVAILABLE if invocation fails.
            PaasError: With CONFIG_INVALID if paas_device_id malformed.
        """
        ns = self._credentials.namespace or "default"

        try:
            return await asyncio.to_thread(
                self._plugin.invoke_http_in_device,
                paas_device_id=paas_device_id,
                method=method,
                port=port,
                path=path,
                namespace=ns,
                query_string=query_string,
                headers=headers,
                body=body,
            )
        except Exception as e:
            raise self._map_error(e, ErrorCode.DEVICE_UNAVAILABLE)

    # ------------------------------------------------------------------
    # TIER 2: Basic passthrough implementations — 3 methods (D-09)
    # ------------------------------------------------------------------

    async def get_device_info(self, paas_device_id: str) -> K8sDeviceInfo:
        """Get K8s device info including StatefulSet status, namespace, and Pod IP.

        Queries sandbox info via plugin.connect_device() + sandbox.get_info().

        Args:
            paas_device_id: paas_device_id in format "{statefulset_name}--{ordinal}".

        Returns:
            K8sDeviceInfo with status, deployment_name, namespace, and pod_ip.

        Raises:
            PaasError: With CONFIG_INVALID if paas_device_id malformed.
        """
        pod_name = self._to_pod_name(paas_device_id)
        ns = self._credentials.namespace or "default"

        try:
            sandbox = await asyncio.to_thread(self._plugin.connect_device, pod_name, ns)
            info = sandbox.get_info()
        except Exception as e:
            raise self._map_error(e, ErrorCode.DEVICE_NOT_FOUND)
        # Extract container_statuses to build PodInfo list (D-12)
        container_statuses = info.get("container_statuses", [])
        pods: list[PodInfo] | None = None
        if container_statuses:
            pods = [
                PodInfo(
                    name=cs.get("name", ""),
                    ready=cs.get("ready", False),
                    restart_count=cs.get("restart_count", 0),
                    state=cs.get("state"),
                    image=cs.get("image"),
                )
                for cs in container_statuses
            ]
        deployment_name = self._extract_statefulset_name(paas_device_id)
        return K8sDeviceInfo(
            status=info.get("status", "UNKNOWN"),
            deployment_name=deployment_name,
            namespace=ns,
            pod_ip=info.get("pod_ip"),
            pods=pods,
        )

    async def resolve_ws_conn_info(
        self, paas_device_id: str, port: int, path: str
    ) -> WsConnectionInfo:
        """Resolve WebSocket connection info for a K8s device.

        Delegates to plugin.resolve_ws_conn_info() with full paas_device_id
        so the plugin can parse "{name}--{ordinal}" to derive the Pod name.

        Args:
            paas_device_id: paas_device_id in format "{statefulset_name}--{ordinal}".
            port: Target port on the device.
            path: WebSocket path (e.g., /api/openclaw/ws).

        Returns:
            WsConnectionInfo with ws_url, token, target, and expires_at.

        Raises:
            PaasError: With CONFIG_INVALID if paas_device_id malformed.
        """
        ns = self._credentials.namespace or "default"

        try:
            return await asyncio.to_thread(
                self._plugin.resolve_ws_conn_info,
                paas_device_id=paas_device_id,
                port=port,
                path=path,
                namespace=ns,
            )
        except Exception as e:
            raise self._map_error(e, ErrorCode.DEVICE_UNAVAILABLE)

    async def resolve_invoke_http_info(
        self, paas_device_id: str, port: int, path: str | None = None
    ) -> HttpConnectionInfo:
        """Resolve HTTP connection info for invoking endpoints on a K8s device.

        Delegates to plugin.resolve_invoke_http_info() with full paas_device_id
        so the plugin can parse "{name}--{ordinal}" to derive the Pod name.

        Args:
            paas_device_id: paas_device_id in format "{statefulset_name}--{ordinal}".
            port: Target port on the device.
            path: HTTP path (defaults to "/" if None).

        Returns:
            HttpConnectionInfo with http_url and token.

        Raises:
            PaasError: With CONFIG_INVALID if paas_device_id malformed.
        """
        ns = self._credentials.namespace or "default"
        resolved_path = path if path is not None else "/"

        try:
            return await asyncio.to_thread(
                self._plugin.resolve_invoke_http_info,
                paas_device_id=paas_device_id,
                port=port,
                path=resolved_path,
                namespace=ns,
            )
        except Exception as e:
            raise self._map_error(e, ErrorCode.DEVICE_UNAVAILABLE)

    # ------------------------------------------------------------------
    # TIER 3: update_outbound_operation_rule (1 method)
    # ------------------------------------------------------------------

    async def update_outbound_operation_rule(
        self,
        paas_device_id: str,
        outbound_operation_rule: OutBoundOperationRule,
    ) -> bool:
        """Update the outbound proxy rules ConfigMap for a K8s StatefulSet.

        Converts template-level K8sOutboundProxyRule list to Envoy YAML via
        _generate_envoy_yaml, then delegates to the plugin to update the
        per-StatefulSet ConfigMap. Envoy hot-reloads via ConfigMap volume
        mount symlink rotation (D-06, D-06a, D-06b).

        The outbound_operation_rule parameter is accepted for ABC compatibility
        but K8s ignores it — rules come from template config (K8sCredentials)
        per D-03.

        Args:
            paas_device_id: paas_device_id in format "{statefulset_name}--{ordinal}".
            outbound_operation_rule: Outbound operation rule (ignored on K8s).

        Returns:
            True if the ConfigMap was updated successfully.

        Raises:
            PaasError: With DEVICE_UNAVAILABLE if the plugin call fails.
            PaasError: With CONFIG_INVALID if paas_device_id is malformed.
        """
        statefulset_name = self._extract_statefulset_name(paas_device_id)
        rules = self._credentials.outbound_proxy_rules or []
        if not rules:
            self._logger.info(
                "No outbound rules configured — skipping ConfigMap update"
            )
            return True
        envoy_yaml = self._generate_envoy_yaml(rules)
        ns = self._credentials.namespace or "default"

        self._logger.info(
            "Updating outbound proxy rules: statefulset=%s rules_count=%d",
            statefulset_name,
            len(rules),
        )

        try:
            await asyncio.to_thread(
                self._plugin.update_outbound_operation_rule,
                statefulset_name,
                ns,
                envoy_yaml,
            )
            self._logger.info(
                "Outbound proxy rules updated: statefulset=%s", statefulset_name
            )
            return True
        except Exception as e:
            raise self._map_error(e, ErrorCode.DEVICE_UNAVAILABLE)

    # ------------------------------------------------------------------
    # TIER 4: NotImplementedError — Permanent (2 methods, D-09)
    # ------------------------------------------------------------------

    async def update_device_ttl(self, paas_device_id: str) -> TTLInfo:
        """Not supported: K8s platform does not support TTL extension.

        Args:
            paas_device_id: paas_device_id.

        Raises:
            NotImplementedError: Always — permanently unsupported on K8s.
        """
        raise NotImplementedError("K8s platform does not support TTL extension")

    async def open_folder(
        self, paas_device_id: str, folder_path: str | None = None
    ) -> bool:
        """Not supported: K8s platform does not support open_folder.

        Only LOCAL platform supports open_folder. This method exists to
        satisfy the PaasService ABC but is permanently unsupported for K8s.

        Args:
            paas_device_id: paas_device_id.
            folder_path: Optional folder path.

        Raises:
            NotImplementedError: Always — permanently unsupported on K8s.
        """
        raise NotImplementedError("K8s platform does not support open_folder")

    # ------------------------------------------------------------------
    # Internal helper: extract statefulset_name from paas_device_id
    # ------------------------------------------------------------------

    def _extract_statefulset_name(self, paas_device_id: str) -> str:
        """Extract statefulset name from paas_device_id.

        Per Pitfall 3 (RESEARCH.md): splits on first "--" (maxsplit=1)
        to handle potential "--" in ordinal values. Validates exactly 2 parts.

        Args:
            paas_device_id: paas_device_id in format "{statefulset_name}--{ordinal}".

        Returns:
            StatefulSet name (first segment before "--").

        Raises:
            PaasError: With CONFIG_INVALID if paas_device_id is malformed.
        """
        parts = paas_device_id.split("--", maxsplit=1)
        if len(parts) != 2:
            raise PaasError(
                ErrorCode.CONFIG_INVALID,
                f"Invalid paas_device_id: {paas_device_id}",
            )
        return parts[0]

    def _to_pod_name(self, paas_device_id: str) -> str:
        """Convert paas_device_id to K8s Pod name.

        paas_device_id format: "{statefulset_name}--{ordinal}"
        Pod name format:        "{statefulset_name}-{ordinal}"

        When paas_device_id does not contain the "--" separator (e.g.,
        UUID-based stub sandbox IDs), returns the value as-is since
        the stub sandbox_id directly matches the Pod name key.

        Args:
            paas_device_id: paas_device_id in format "{statefulset_name}--{ordinal}",
                or a UUID-based stub sandbox ID without separator.

        Returns:
            K8s Pod name in format "{statefulset_name}-{ordinal}", or the
            original paas_device_id if it contains no "--" separator.

        Raises:
            PaasError: With CONFIG_INVALID if paas_device_id has "--" but
                cannot be split into exactly 2 parts.
        """
        if "--" not in paas_device_id:
            return paas_device_id
        parts = paas_device_id.split("--", maxsplit=1)
        if len(parts) != 2:
            raise PaasError(
                ErrorCode.CONFIG_INVALID,
                f"Invalid paas_device_id: {paas_device_id}",
            )
        return f"{parts[0]}-{parts[1]}"
