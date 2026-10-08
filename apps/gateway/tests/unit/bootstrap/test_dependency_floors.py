"""Declared dependency floors that a lockfile would otherwise hide.

`uv sync --frozen` installs the pinned version, so CI exercises whatever the
lock says and a too-low floor in `pyproject.toml` stays invisible here. It is
not invisible to anyone installing the built wheel, who resolves the floor.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def _floor(package: str) -> tuple[int, ...]:
    deps = tomllib.loads(_PYPROJECT.read_text())["project"]["dependencies"]
    for dep in deps:
        match = re.fullmatch(rf"{package}>=([0-9.]+)", dep.strip())
        if match:
            return tuple(int(part) for part in match.group(1).split("."))
    raise AssertionError(f"{package} is not declared with a >= floor: {deps}")


def test_websockets_floor_includes_proxy_support() -> None:
    """``connect(proxy=…)`` is a parameter only from websockets 15.0.

    The outbound transport passes ``proxy=None`` so an ambient ``HTTPS_PROXY``
    cannot silently re-route a configured upstream. On 14.x that argument is not
    a parameter at all, so it would be swallowed into ``**kwargs`` and every
    upstream handshake would fail with an unexpected-keyword ``TypeError``.
    """
    assert _floor("websockets") >= (15, 0)
