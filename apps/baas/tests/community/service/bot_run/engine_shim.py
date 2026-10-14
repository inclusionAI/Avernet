"""Test bridge to engine adapter's real a2a wire/bridge modules.

方案：engine src 树整体加 sys.path（真实源），再以标准 import 载入——
只绕开 a2a 包 __init__ 的 router 重导出（其引 fastapi_injector——baas
venv 无此依赖）。做法：向包注册一个空命名空间 + 手动填 wire/kernel/
engine_bridge。
"""
import importlib
import pathlib
import sys
import types

_ENGINE_SRC = (pathlib.Path(__file__).resolve().parents[6]
               / "engine" / "adapter" / "src")
if not _ENGINE_SRC.is_dir():
    raise RuntimeError(f"engine src not found at {_ENGINE_SRC}")

if str(_ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(_ENGINE_SRC))

# 构造 engine.community.api.transport.a2a 的 stub 包（跳过真实 __init__
# 的 router import 链），并预载它自己的三个真实子模块。
def _stub_pkg(fullname: str, path: pathlib.Path) -> types.ModuleType:
    mod = types.ModuleType(fullname)
    mod.__path__ = [str(path)]
    sys.modules[fullname] = mod
    return mod


_stub_pkg("engine", _ENGINE_SRC / "engine")
_stub_pkg("engine.community", _ENGINE_SRC / "engine" / "community")
_stub_pkg("engine.community.api",
          _ENGINE_SRC / "engine" / "community" / "api")
_stub_pkg("engine.community.api.transport",
          _ENGINE_SRC / "engine" / "community" / "api" / "transport")
_a2a = _stub_pkg("engine.community.api.transport.a2a",
                 _ENGINE_SRC / "engine" / "community" / "api"
                 / "transport" / "a2a")

_a2a.frames = importlib.import_module("engine.community.kernel.frames")
wire = importlib.import_module("engine.community.api.transport.a2a.wire")
_a2a.wire = wire
kernel = importlib.import_module("engine.community.api.transport.a2a.kernel")
_a2a.kernel = kernel
bridge = importlib.import_module(
    "engine.community.api.transport.a2a.engine_bridge")

__all__ = ["wire", "bridge", "kernel"]


def kernel_event_to_wire(event: dict) -> dict:
    """engine a2a server._wire 的等价转换（kernel 事件 → SSE wire 成员）。

    与 engine.community.api.transport.a2a.router 的 _wire 同构——测试用它
    走完整内核到 wire 的真实序列化路径再喂 B1 消费器。
    """
    out = dict(event.get("ev") or {})
    seq = event.get("seq")
    for kind in ("statusUpdate", "artifactUpdate"):
        if kind in out and out[kind] is not None:
            inner = dict(out[kind])
            meta = dict(inner.get("metadata") or {})
            if seq is not None:
                meta["seq"] = seq
            inner["metadata"] = meta
            out[kind] = inner
    return out


__all__ = ["wire", "bridge", "kernel", "kernel_event_to_wire"]
