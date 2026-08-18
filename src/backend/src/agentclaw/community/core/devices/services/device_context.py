"""DeviceContext — the typed exit of DeviceContextResolver.

The repo-wide data contract for provider resolution results. Downstream
dispatchers pick a plugin instance by ``provider``; plugins dial with
``conn_info`` (:class:`ConnInfo`).
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal

ProviderType = Literal["arca", "baas", "teclaw", "local"]


class ConnInfo(Mapping[str, Any]):
    """Dial-out info — typed read-only view over the provider-built conn_info.

    Historically conn_info was a bare ``dict[str, Any]``; the only way to
    learn its fields was reading the builder sources. This class declares
    every known field as a typed property with an example, while also
    implementing the ``Mapping`` protocol so existing dict-style reads
    (``conn_info["url"]`` / ``conn_info.get("tenant")``) keep working
    unchanged — the key set and ``==`` semantics match the underlying dict,
    and unknown builder-produced keys pass through (dict-style access only).

    Each provider writes only the field subset it needs (see
    ``conn_info_builders/``); a property whose field is absent returns the
    documented default, meaning "not applicable on this path".

    Seven-field schema contract (written by every provider, see
    ``tests/community/contract/test_conn_info_schema.py``):
    ``url`` / ``token`` / ``headers`` / ``use_proxy`` / ``sandbox_id`` /
    ``target`` / ``engine_type``. The one exception is the binding-routed
    path of ``DeviceContextResolver.resolve_for_binding_invoke``, where the
    transport fetches address/token per binding at call time, so no
    transport fields are pre-filled.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any] | None = None, /, **fields: Any) -> None:
        """Build from a mapping and/or keyword fields (keywords win on clashes).

        The input is shallow-copied; mutating the source dict afterwards
        does not affect this object.
        """
        merged: dict[str, Any] = dict(data) if data is not None else {}
        merged.update(fields)
        self._data = merged

    # ── Mapping protocol (keeps legacy dict-style reads working; get/keys/
    #    items/__contains__/__eq__ come from collections.abc.Mapping) ─────

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"ConnInfo({self._data!r})"

    def to_dict(self) -> dict[str, Any]:
        """Export a plain dict copy (when a real dict is required, e.g. JSON
        serialization)."""
        return dict(self._data)

    # ── Seven-field schema contract (all providers) ────────────────────

    @property
    def url(self) -> str:
        """HTTP(S) base URL of the engine adapter; callers append a path and
        request it directly.

        Example (arca proxy): ``"https://<arca-host>/proxypass/<target>:20003"``;
        example (baas invoke-http):
        ``"https://<baas-host>/api/v1/bots/<tenant>/<bot_uuid>/invoke-http/20003"``;
        example (local direct): ``"http://127.0.0.1:20003"``.
        Default ``""`` — the binding-routed path pre-fills nothing; the
        transport fetches the address per binding.
        """
        return self._data.get("url", "")

    @property
    def token(self) -> str:
        """Proxypass auth token. Example: ``"tok"``; ``""`` for local direct."""
        return self._data.get("token", "")

    @property
    def headers(self) -> dict[str, str]:
        """HTTP headers the request must carry.

        Example: ``{"x-proxypass-token": "tok"}``; ``{}`` for local direct.
        """
        return self._data.get("headers", {})

    @property
    def use_proxy(self) -> bool:
        """Whether to dial through the proxypass proxy.

        Example: ``True`` for arca/baas, ``False`` for local.
        """
        return self._data.get("use_proxy", False)

    @property
    def sandbox_id(self) -> str | None:
        """ARCA sandbox identifier.

        Example: ``"ARCA_ARCA-SANDBOX-xxx@0:20003"``. ``None`` is the norm —
        neither local direct nor the baas invoke-http path lands on a sandbox.
        """
        return self._data.get("sandbox_id")

    @property
    def target(self) -> str:
        """Dial target address (host:port or sandbox target).

        Example: ``"<baas-target>:20003"`` / ``"127.0.0.1:20003"``.
        Default ``""``.
        """
        return self._data.get("target", "")

    @property
    def engine_type(self) -> str:
        """Engine type, from ``ac_bots.active_engine``.

        Example: ``"openclaw"`` / ``"teclaw"``. Default ``""``.
        """
        return self._data.get("engine_type", "")

    # ── Extension fields (written as needed by baas / teclaw / v2 desktop) ──

    @property
    def type(self) -> str:
        """Connection type, the frontend's WS routing key.

        Example: ``"desktop"`` (bare ws direct) / ``"baas"`` (proxypass wss).
        Default ``""`` — the legacy arca/local paths don't write it.
        """
        return self._data.get("type", "")

    @property
    def bot_type(self) -> str:
        """``ac_bots.bot_type``. Example: ``"desktop"`` / ``"personal"`` /
        ``"service"``. Default ``""`` (bot lookup failed).
        """
        return self._data.get("bot_type", "")

    @property
    def binding_id(self) -> int | None:
        """``ac_entity_device_binding.id``. Example: ``201``.

        ``None`` means this path didn't carry it (e.g. the legacy arca
        path) — the transport's ``invoke`` branches on its presence to pick
        baas /http-info vs the fallback.
        """
        return self._data.get("binding_id")

    @property
    def bind_id(self) -> int | None:
        """Legacy alias of :attr:`binding_id` (historical baas naming).
        Example: ``201``.

        The resolver's ``_normalize_schema`` aliases ``bind_id`` into
        ``binding_id``; both keys coexist with the same value. New code
        should read :attr:`binding_id`.
        """
        return self._data.get("bind_id")

    @property
    def bot_uuid(self) -> str:
        """BaaS-side bot instance UUID (= ``ac_entity_device_binding.device_id``).

        Example: ``"device-201"``. Default ``""`` — only the baas/teclaw
        paths write it.
        """
        return self._data.get("bot_uuid", "")

    @property
    def tenant(self) -> str:
        """BaaS tenant. Example: ``"<tenant>"``. Default ``""`` — only the
        baas path writes it."""
        return self._data.get("tenant", "")

    @property
    def baas_base_url(self) -> str:
        """BaaS gateway base URL. Example: ``"https://<baas-host>"``.

        Default ``""`` — only the baas path writes it; the invoke-http URL
        is assembled from it.
        """
        return self._data.get("baas_base_url", "")

    @property
    def engine_port(self) -> int | None:
        """Engine adapter listen port. Example: ``20003``.

        ``None`` means this path didn't carry it (the legacy arca/local
        paths fold the port into ``url``).
        """
        return self._data.get("engine_port")

    @property
    def paas_device_id(self) -> str | None:
        """BaaS-side physical device ID. ``None`` means this path didn't
        carry it (non-baas)."""
        return self._data.get("paas_device_id")

    @property
    def device_affinity(self) -> str:
        """Device affinity key (= operator user_id), used by BaaS
        multi-instance routing.

        Example: ``"user-1"``. Default ``""`` (legacy callsites didn't pass
        user_id).
        """
        return self._data.get("device_affinity", "")

    @property
    def device_uuid(self) -> str | None:
        """Device UUID pinning a specific instance in multi-instance
        scenarios. Example: ``"DEVICE-002"``.

        ``None`` is the norm — no pinning; BaaS auto-selects an active
        instance.
        """
        return self._data.get("device_uuid")

    # ── v2 offline-branch fields ───────────────────────────────────────

    @property
    def available(self) -> bool:
        """Whether the device is online. Default ``True`` — only the v2
        offline branch explicitly writes ``False``."""
        return self._data.get("available", True)

    @property
    def message(self) -> str:
        """Reason the device is unavailable (accompanies ``available=False``).
        Default ``""``."""
        return self._data.get("message", "")


@dataclass(frozen=True)
class DeviceContext:
    """Container access context — the typed exit of DeviceContextResolver.

    The data contract between routing callers and plugins:
    - dispatchers pick a plugin instance by ``(provider, bot_type)`` (the
      bot_type dimension is new this phase; rationale in
      docs/superpowers/specs/2026-06-17-baas-transport-bot-type-strategy-design.md)
    - plugins dial with ``conn_info`` (field subsets differ per provider;
      see :class:`ConnInfo` for the typed view)

    Fields:
        provider: The factual value from
            ``ac_entity_device_binding.device_provider``
        conn_info: Dial-out fields (naming aligned by the resolver). A plain
            dict passed at construction is wrapped into :class:`ConnInfo` by
            ``__post_init__`` — see it for the field list, types, and
            examples; dict-style reads (``ctx.conn_info["url"]``) keep working
        binding_id: From the binding table
        bot_id: Passed through from the caller. Required on both entry
            points — ``resolve_for_bot`` takes it as an argument, and
            ``resolve_for_binding`` callers pass it as an explicit kwarg
            (downstream ``_build_binding_ctx`` really does use it against
            bot_repo for the owner check).
        user_id: Passed through from the caller (operator identity;
            non-owners go through the permission check)
        bot_type: The ``ac_bots.bot_type`` value (``"desktop"`` /
            ``"personal"`` / ``"service"`` etc.), fetched internally by the
            resolver from ``bot_repo``. Dispatchers route on the
            two-dimensional ``(provider, bot_type)`` (this phase baas
            branches again on bot_type internally: desktop builds its own
            URL, everything else goes through invoke_http). ``""`` when the
            data is unknown.
    """
    provider: ProviderType
    conn_info: ConnInfo
    binding_id: int
    bot_id: str
    user_id: str
    bot_type: str = ""  # Task 1 defaults to "" — since Task 2 the resolver injects the real value from bot_repo

    def __post_init__(self) -> None:
        # Legacy tests/callers still pass a plain dict — wrap it into
        # ConnInfo so ``ctx.conn_info``'s typed attribute access works on
        # every construction path.
        if not isinstance(self.conn_info, ConnInfo):
            object.__setattr__(self, "conn_info", ConnInfo(self.conn_info))


class BotNotFoundError(RuntimeError):
    """The bot_id does not exist, or the caller may not access the bot."""


class DeviceNotBoundError(RuntimeError):
    """The bot exists but has no active binding (not applied / released)."""


class UnknownProviderError(RuntimeError):
    """binding.device_provider is a value the resolver does not recognize.

    Should never happen in production — hitting this means the binding
    table data is corrupt.
    """


class ConnInfoBuildError(RuntimeError):
    """A ConnInfoBuilder's underlying call (baas /http-info, arca proxy,
    etc.) failed."""
