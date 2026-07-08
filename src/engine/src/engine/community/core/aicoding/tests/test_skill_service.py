"""Contract tests for SkillListService.

Covers the aix-CLI shape consumed by ``GET /api/aicoding/skills``:

* ``aix skill list --json`` — success passthrough, ``exit_code != 0``, JSON
  parse failure, and ``_safe_exec`` swallowing ``FileNotFoundError`` /
  ``ValueError`` / ``OSError`` / ``asyncio.TimeoutError``.
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

import pytest
from fastapi import HTTPException

from engine.community.core.bash.models import BashExecResult
from engine.community.core.aicoding.skill_service import SkillListService


# ── test doubles ────────────────────────────────────────────────────────────


class FakeBashPlugin:
    """Configurable BashPlugin that picks responses by ``(cmd_substr, cwd)``.

    ``cmd_match`` matches if it appears as a substring of the executed command;
    ``cwd=None`` is cwd-agnostic (``_resolve_workspace_base()`` 的具体值不参与断言)。
    """

    def __init__(self) -> None:
        self.responses: list[tuple[str, Optional[str], BashExecResult]] = []
        self.raise_for: list[tuple[str, Optional[str], BaseException]] = []
        self.calls: list[tuple[str, str, int]] = []

    def add(self, cmd_match: str, cwd: Optional[str], result: BashExecResult) -> None:
        self.responses.append((cmd_match, cwd, result))

    def add_raise(self, cmd_match: str, cwd: Optional[str], exc: BaseException) -> None:
        self.raise_for.append((cmd_match, cwd, exc))

    async def exec(
        self, cmd: str, cwd: str, timeout: int = 30, auth=None  # noqa: ANN001
    ) -> BashExecResult:
        self.calls.append((cmd, cwd, timeout))
        for cmd_match, want_cwd, exc in self.raise_for:
            if cmd_match in cmd and (want_cwd is None or want_cwd == cwd):
                raise exc
        for cmd_match, want_cwd, res in self.responses:
            if cmd_match in cmd and (want_cwd is None or want_cwd == cwd):
                return res
        return BashExecResult(stdout="", stderr="", exit_code=0)


# ── success ─────────────────────────────────────────────────────────────────


async def test_list_skills_passes_through_aix_output():
    bash = FakeBashPlugin()
    payload = {"backends": {"cc": {"skills": [{"name": "aix", "source": "cc-plugin-cache"}]}}}
    bash.add("aix skill list", None, BashExecResult(
        stdout=json.dumps(payload), stderr="", exit_code=0,
    ))
    svc = SkillListService(bash_plugin=bash)

    result = await svc.list_skills()

    assert result == payload
    # 命令透传，无 session_id 拼接
    assert bash.calls[0][0] == "aix skill list --json"


# ── error paths ─────────────────────────────────────────────────────────────


async def test_list_skills_nonzero_exit_raises_500_with_stderr():
    bash = FakeBashPlugin()
    bash.add("aix skill list", None, BashExecResult(
        stdout="", stderr="command not found: aix", exit_code=127,
    ))
    svc = SkillListService(bash_plugin=bash)

    with pytest.raises(HTTPException) as ei:
        await svc.list_skills()
    assert ei.value.status_code == 500
    assert "command not found: aix" in ei.value.detail
    assert "aix skill list failed" in ei.value.detail


async def test_list_skills_invalid_json_raises_500():
    bash = FakeBashPlugin()
    bash.add("aix skill list", None, BashExecResult(
        stdout="not-json{{{", stderr="", exit_code=0,
    ))
    svc = SkillListService(bash_plugin=bash)

    with pytest.raises(HTTPException) as ei:
        await svc.list_skills()
    assert ei.value.status_code == 500
    assert "Failed to parse aix output" in ei.value.detail


@pytest.mark.parametrize("exc", [
    FileNotFoundError("no cwd"),
    ValueError("cwd not whitelisted"),
    OSError("subprocess spawn failed"),
    asyncio.TimeoutError(),
])
async def test_safe_exec_swallows_bash_exceptions_then_500(exc):
    """_safe_exec 吞掉 exec 异常返回 None → service 收敛为 500(no stderr)。"""
    bash = FakeBashPlugin()
    bash.add_raise("aix skill list", None, exc)
    svc = SkillListService(bash_plugin=bash)

    with pytest.raises(HTTPException) as ei:
        await svc.list_skills()
    assert ei.value.status_code == 500
    assert "no stderr" in ei.value.detail


async def test_list_skills_empty_stdout_raises_500():
    bash = FakeBashPlugin()
    # aix 返回空串 → json.loads("") 抛 JSONDecodeError → 500
    bash.add("aix skill list", None, BashExecResult(
        stdout="", stderr="", exit_code=0,
    ))
    svc = SkillListService(bash_plugin=bash)

    with pytest.raises(HTTPException) as ei:
        await svc.list_skills()
    assert ei.value.status_code == 500
    assert "Failed to parse aix output" in ei.value.detail
