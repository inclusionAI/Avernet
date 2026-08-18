"""DeviceContext — DeviceContextResolver 的 typed 出口。

全仓唯一 provider 解析结果的数据契约。下游 dispatcher 用 ``provider``
选 plugin 实例,plugin 用 ``conn_info``(:class:`ConnInfo`)拨号。
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal

ProviderType = Literal["arca", "baas", "teclaw", "local"]


class ConnInfo(Mapping[str, Any]):
    """拨号信息 — provider 构建的 conn_info 的 typed 只读视图。

    历史上 conn_info 是裸 ``dict[str, Any]``,字段只能翻 builder 源码才知道。
    本类把每个已知字段收敛成带类型 + 示例的 property,同时实现 ``Mapping``
    协议,存量 ``conn_info["url"]`` / ``conn_info.get("tenant")`` 式读取原样
    兼容 — key 集合、``==`` 语义都与底层 dict 一致,builder 产出的未知 key
    也原样透传(仅 dict 式访问)。

    各 provider 只写自己需要的字段子集(见 ``conn_info_builders/``);缺席
    字段的 property 返回文档标注的缺省值,语义为"该链路不适用"。

    七字段 schema 契约(全 provider 必写,见
    ``tests/community/contract/test_conn_info_schema.py``):
    ``url`` / ``token`` / ``headers`` / ``use_proxy`` / ``sandbox_id`` /
    ``target`` / ``engine_type``。唯一例外是
    ``DeviceContextResolver.resolve_for_binding_invoke`` 的 binding 路由链路,
    地址/令牌由 transport 调用时按 binding 现取,不预置传输字段。
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any] | None = None, /, **fields: Any) -> None:
        """从 mapping 和/或 keyword 字段构造(keyword 覆盖同名 key)。

        入参浅拷贝,构造后外部改源 dict 不影响本对象。
        """
        merged: dict[str, Any] = dict(data) if data is not None else {}
        merged.update(fields)
        self._data = merged

    # ── Mapping 协议(兼容存量 dict 式读取;get/keys/items/__contains__/__eq__
    #    由 collections.abc.Mapping 提供) ──────────────────────────────

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"ConnInfo({self._data!r})"

    def to_dict(self) -> dict[str, Any]:
        """导出 plain dict 副本(需要真 dict 的场景,如 JSON 序列化)。"""
        return dict(self._data)

    # ── 七字段 schema 契约(全 provider) ─────────────────────────────

    @property
    def url(self) -> str:
        """engine adapter 的 HTTP(S) base URL,caller 拿它拼 path 直接请求。

        例(arca proxy): ``"https://<arca-host>/proxypass/<target>:20003"``;
        例(baas invoke-http):
        ``"https://<baas-host>/api/v1/bots/<tenant>/<bot_uuid>/invoke-http/20003"``;
        例(local 直连): ``"http://127.0.0.1:20003"``。
        缺省 ``""`` — binding 路由链路不预置,transport 按 binding 现取。
        """
        return self._data.get("url", "")

    @property
    def token(self) -> str:
        """proxypass 鉴权 token。例: ``"tok"``;local 直连为 ``""``。"""
        return self._data.get("token", "")

    @property
    def headers(self) -> dict[str, str]:
        """请求需带的 HTTP header。

        例: ``{"x-proxypass-token": "tok"}``;local 直连为 ``{}``。
        """
        return self._data.get("headers", {})

    @property
    def use_proxy(self) -> bool:
        """是否经 proxypass 代理拨号。例: arca/baas ``True``,local ``False``。"""
        return self._data.get("use_proxy", False)

    @property
    def sandbox_id(self) -> str | None:
        """ARCA 沙箱标识。

        例: ``"ARCA_ARCA-SANDBOX-xxx@0:20003"``。``None`` 为常态 —
        local 直连和 baas invoke-http 链路都不落沙箱。
        """
        return self._data.get("sandbox_id")

    @property
    def target(self) -> str:
        """拨号目标地址(host:port 或 sandbox target)。

        例: ``"<baas-target>:20003"`` / ``"127.0.0.1:20003"``。缺省 ``""``。
        """
        return self._data.get("target", "")

    @property
    def engine_type(self) -> str:
        """engine 类型,来自 ``ac_bots.active_engine``。

        例: ``"openclaw"`` / ``"teclaw"``。缺省 ``""``。
        """
        return self._data.get("engine_type", "")

    # ── 扩展字段(baas / teclaw / v2 desktop 链路按需写入) ────────────

    @property
    def type(self) -> str:
        """connection type,前端 WS 路由分流键。

        例: ``"desktop"``(裸 ws 直连)/ ``"baas"``(proxypass wss)。
        缺省 ``""`` — arca/local 老链路不写。
        """
        return self._data.get("type", "")

    @property
    def bot_type(self) -> str:
        """``ac_bots.bot_type``。例: ``"desktop"`` / ``"personal"`` /
        ``"service"``。缺省 ``""``(bot 反查不到)。
        """
        return self._data.get("bot_type", "")

    @property
    def binding_id(self) -> int | None:
        """``ac_entity_device_binding.id``。例: ``201``。

        ``None`` 表示该链路没带(如 arca 老路径)—— transport
        ``invoke`` 按有无该字段分流 baas /http-info vs fallback。
        """
        return self._data.get("binding_id")

    @property
    def bind_id(self) -> int | None:
        """:attr:`binding_id` 的 legacy 别名(baas 链路历史命名)。例: ``201``。

        resolver ``_normalize_schema`` 会把 ``bind_id`` alias 成
        ``binding_id``,两个 key 同值并存;新代码读 :attr:`binding_id`。
        """
        return self._data.get("bind_id")

    @property
    def bot_uuid(self) -> str:
        """BaaS 侧 bot 实例 UUID(= ``ac_entity_device_binding.device_id``)。

        例: ``"device-201"``。缺省 ``""`` — 仅 baas/teclaw 链路写。
        """
        return self._data.get("bot_uuid", "")

    @property
    def tenant(self) -> str:
        """BaaS 租户。例: ``"<tenant>"``。缺省 ``""`` — 仅 baas 链路写。"""
        return self._data.get("tenant", "")

    @property
    def baas_base_url(self) -> str:
        """BaaS 网关 base URL。例: ``"https://<baas-host>"``。

        缺省 ``""`` — 仅 baas 链路写,invoke-http URL 由它拼出。
        """
        return self._data.get("baas_base_url", "")

    @property
    def engine_port(self) -> int | None:
        """engine adapter 监听端口。例: ``20003``。

        ``None`` 表示该链路没带(arca/local 老路径端口已并入 ``url``)。
        """
        return self._data.get("engine_port")

    @property
    def paas_device_id(self) -> str | None:
        """BaaS 侧物理设备 ID。``None`` 表示该链路没带(非 baas)。"""
        return self._data.get("paas_device_id")

    @property
    def device_affinity(self) -> str:
        """设备亲和键(= 操作者 user_id),BaaS 多实例选径用。

        例: ``"user-1"``。缺省 ``""``(旧 callsite 未传 user_id)。
        """
        return self._data.get("device_affinity", "")

    @property
    def device_uuid(self) -> str | None:
        """多实例场景锁定的设备 UUID。例: ``"DEVICE-002"``。

        ``None`` 为常态 — 不锁实例,由 BaaS 自动选活跃实例。
        """
        return self._data.get("device_uuid")

    # ── v2 offline 分支字段 ─────────────────────────────────────────

    @property
    def available(self) -> bool:
        """设备是否在线。缺省 ``True`` — 仅 v2 offline 分支显式写 ``False``。"""
        return self._data.get("available", True)

    @property
    def message(self) -> str:
        """设备不可用原因(随 ``available=False`` 出现)。缺省 ``""``。"""
        return self._data.get("message", "")


@dataclass(frozen=True)
class DeviceContext:
    """容器访问上下文 — DeviceContextResolver 的 typed 出口。

    分流 caller 和 plugin 之间的数据契约:
    - dispatcher 用 ``(provider, bot_type)`` 选 plugin 实例(本期新增 bot_type 维度,
      原因见 docs/superpowers/specs/2026-06-17-baas-transport-bot-type-strategy-design.md)
    - plugin 用 ``conn_info`` 拨号(各 provider 字段子集不同,typed 视图见
      :class:`ConnInfo`)

    Fields:
        provider: 来自 ``ac_entity_device_binding.device_provider`` 的事实值
        conn_info: 拨号字段(命名经 resolver 对齐)。构造时传 plain dict 会被
            ``__post_init__`` 自动包成 :class:`ConnInfo` — 字段清单/类型/示例
            都看它;dict 式读取(``ctx.conn_info["url"]``)继续兼容
        binding_id: 来自 binding 表
        bot_id: caller 透传。两条入口都必填 — ``resolve_for_bot`` 入参自带,
            ``resolve_for_binding`` 由 caller 显式 kwarg 传(downstream
            ``_build_binding_ctx`` 真用它去 bot_repo 做 owner 校验)。
        user_id: caller 透传(操作者身份;非 owner 时通过权限校验)
        bot_type: ``ac_bots.bot_type`` 字段值(``"desktop"`` / ``"personal"`` /
            ``"service"`` 等),resolver 内部从 ``bot_repo`` 拿。dispatcher 用
            ``(provider, bot_type)`` 二维分流(本期 baas 内部按 bot_type 二次分,
            desktop 走自拼 URL,其他走 invoke_http)。数据未知时为 ``""``。
    """
    provider: ProviderType
    conn_info: ConnInfo
    binding_id: int
    bot_id: str
    user_id: str
    bot_type: str = ""  # Task 1 默认 "" — Task 2 后 resolver 内部 bot_repo 注入真值

    def __post_init__(self) -> None:
        # 存量测试/caller 仍传 plain dict — 统一包成 ConnInfo,保证
        # ``ctx.conn_info`` 的 typed 属性访问在任何构造路径下都可用。
        if not isinstance(self.conn_info, ConnInfo):
            object.__setattr__(self, "conn_info", ConnInfo(self.conn_info))


class BotNotFoundError(RuntimeError):
    """bot_id 不存在,或 caller 无权访问该 bot。"""


class DeviceNotBoundError(RuntimeError):
    """bot 存在但无 active binding(未 apply / 已 release)。"""


class UnknownProviderError(RuntimeError):
    """binding.device_provider 是 resolver 不认识的值。

    不应在生产中发生 — 触发即说明 binding 表数据异常。
    """


class ConnInfoBuildError(RuntimeError):
    """ConnInfoBuilder 调底层(baas /http-info、arca proxy 等)失败。"""
