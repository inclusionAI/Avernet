"""本地开发模式支持模块。

在 LOCAL_DEV_MODE=true 时，注入 sofapy_base 的 stub 模块到 sys.modules，
使后端可以绕过 MOSN 直接启动。

必须在所有其他 agentclaw 模块 import 之前调用 patch_sofapy_for_local()。

配置不再走这里：``sofapy_base.app.config`` 的 fake 已在 B2 移除，配置由
``core/config`` 的 ConfigProvider（corp → sofapy，test/community → YAML）提供。
``sofapy_base.app.layotto_manager`` 的 fake 已在 B6 移除：core 不再读 layotto，
DRM 动态配置走注入的 ``DRMReaderPlugin``（corp=layotto，test/community=None）。这里只保留
runner / application / logger 的 prod-boot fake。``layotto`` 顶层包 stub 仍由
``_patch_layotto_stubs`` 保留，供 plugins/prod 的 passport/antprocess import 兜底
（OSS 打包阶段再处理）。
"""

import logging

from agentclaw.community.log import get_logger
import sys
import types

logger = get_logger()


def _create_stub_module(name: str, **attrs) -> types.ModuleType:
    """创建一个 stub 模块并注册到 sys.modules。"""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def patch_sofapy_for_local():
    """注入 sofapy_base stub 模块到 sys.modules。

    如果 sofapy_base 已经安装了，会 patch 已有模块。
    如果没安装，会创建完整的 stub 模块树。

    必须在 main.py 中、所有其他 agentclaw import 之前调用。
    """
    logger.info("[local] Setting up sofapy_base stubs for local mode")

    # Check if sofapy_base is already installed
    try:
        import sofapy_base
        _is_installed = True
    except ImportError:
        _is_installed = False

    if _is_installed:
        # Nothing to fake when sofapy_base is installed: config comes from the
        # ConfigProvider registry (B2) and DRM/layotto reads go through the
        # injected DRMReaderPlugin, whose non-corp impls return None (B6).
        pass
    else:
        # Create full stub module tree
        # sofapy_base
        _create_stub_module("sofapy_base")

        # sofapy_base.app
        _create_stub_module("sofapy_base.app")

        # NOTE: sofapy_base.app.config is intentionally NOT stubbed (B2). Config
        # comes from the ConfigProvider registry; core no longer imports it.
        # NOTE: sofapy_base.app.layotto_manager is intentionally NOT stubbed (B6).
        # core no longer reads layotto; DRM goes through the injected DRMReaderPlugin.

        # sofapy_base.app.application
        _SOFAPyApplication = type("SOFAPyApplication", (), {"__init__": lambda self: None})
        _create_stub_module(
            "sofapy_base.app.application",
            SOFAPyApplication=_SOFAPyApplication,
        )

        # sofapy_base.runner
        _create_stub_module(
            "sofapy_base.runner",
            run=lambda **kwargs: None,
        )

        # sofapy_base.logger — falls back to stdlib logging
        def _local_get_logger(name="start"):
            return logging.getLogger(name)

        def _local_set_logger(level=logging.INFO, log_dir=None):
            pass

        _create_stub_module("sofapy_base.logger")
        _create_stub_module(
            "sofapy_base.logger.logger",
            get_logger=_local_get_logger,
            set_logger=_local_set_logger,
        )

    # Stub layotto and arca if not installed
    _patch_layotto_stubs()
    _patch_arca_stubs()

    logger.info("[local] sofapy_base stubs ready for local mode")


class _StubBase:
    """Base class for all layotto/arca stubs. Accepts any args, returns self for chaining."""

    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name: str):
        return _StubBase()

    def __call__(self, *args, **kwargs):
        return _StubBase()

    def __bool__(self):
        return False

    def __iter__(self):
        return iter([])

    def __str__(self):
        return ""


class _CatchAllModule(types.ModuleType):
    """Module that returns _StubBase for any attribute access."""

    def __getattr__(self, name: str):
        return _StubBase


class _AutoStubMeta(type):
    """Metaclass that makes a class usable as a base class for auto-generated stubs.

    Any attribute access on the class returns a new stub class so that
    `class Foo(layotto.Facade)` or `class Bar(layotto.HessianObject)` works.
    """

    def __getattr__(cls, name: str):
        # Return a new stub class that can be used as a base
        return type(name, (), {"__init__": lambda self, *a, **kw: None})


class _LayottoStubBase(metaclass=_AutoStubMeta):
    """Base that auto-generated layotto classes can inherit from."""

    def __init__(self, *args, **kwargs):
        pass


def _create_catch_all_module(name: str, **fixed_attrs) -> types.ModuleType:
    """Create a module that returns stub classes for any undefined attribute."""

    class _Mod(types.ModuleType):
        def __getattr__(self, attr: str):
            if attr.startswith("_"):
                raise AttributeError(attr)
            return type(attr, (), {"__init__": lambda self, *a, **kw: None})

    mod = _Mod(name)
    for k, v in fixed_attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _patch_layotto_stubs():
    """Create layotto stub modules if layotto is not installed."""
    if "layotto" in sys.modules:
        return
    try:
        import layotto  # noqa: F401
        return  # already installed
    except ImportError:
        pass

    logger.info("[local] Creating layotto stub modules")

    def _no_mosn_client():
        raise RuntimeError("layotto MOSN client is not available in local mode")

    # layotto top-level: catch-all module that returns stub classes for any attribute
    _create_catch_all_module(
        "layotto",
        get_mosn_client=_no_mosn_client,
    )

    # Sub-modules that may be imported
    _create_catch_all_module("layotto.client")
    _create_catch_all_module(
        "layotto.client.layotto_manager",
        get_mosn_client=_no_mosn_client,
    )
    _create_catch_all_module("layotto.ext")
    _create_catch_all_module("layotto.ext.layotto_ext_grpc")
    _create_catch_all_module("layotto.ext.layotto_ext_grpc.layotto")
    _create_catch_all_module("layotto.ext.layotto_ext_grpc.layotto.ext")
    _create_catch_all_module("layotto.ext.layotto_ext_grpc.layotto.ext.grpc")

    RpcRequest = type("RpcRequest", (), {"__init__": lambda self, *a, **kw: None})
    SofaRpcResponse = type("SofaRpcResponse", (), {"__init__": lambda self, *a, **kw: None})
    _create_catch_all_module(
        "layotto.ext.layotto_ext_grpc.layotto.ext.grpc.rpc",
        RpcRequest=RpcRequest,
        SofaRpcResponse=SofaRpcResponse,
    )


def _patch_arca_stubs():
    """Create arca stub modules if arca is not installed."""
    if "arca" in sys.modules:
        return
    try:
        import arca  # noqa: F401
        return  # already installed
    except ImportError:
        pass

    logger.info("[local] Creating arca stub modules")

    # arca top-level
    SandboxFactory = type("SandboxFactory", (), {"__init__": lambda self, *a, **kw: None})
    _create_stub_module("arca", SandboxFactory=SandboxFactory)

    # arca.model
    _create_stub_module("arca.model")
    SandboxConfig = type("SandboxConfig", (), {"__init__": lambda self, *a, **kw: None})
    _create_stub_module("arca.model.config", SandboxConfig=SandboxConfig)

    # arca.model.sandbox - specific classes used in service_arca.py
    MountPoint = type("MountPoint", (), {"__init__": lambda self, *a, **kw: None})
    MountPermission = type("MountPermission", (), {"__init__": lambda self, *a, **kw: None})
    OutBoundOperationRule = type("OutBoundOperationRule", (), {"__init__": lambda self, *a, **kw: None})
    HeaderOperationRule = type("HeaderOperationRule", (), {"__init__": lambda self, *a, **kw: None})
    _create_stub_module(
        "arca.model.sandbox",
        MountPoint=MountPoint,
        MountPermission=MountPermission,
        OutBoundOperationRule=OutBoundOperationRule,
        HeaderOperationRule=HeaderOperationRule,
    )

    # arca.tracer
    _create_stub_module("arca.tracer")
    _create_stub_module(
        "arca.tracer.tracer",
        install_tracer=lambda *a, **kw: None,
    )
