"""Unit tests for ClaudeCodeActiveHealthChecker and its probe script.

场景矩阵对应 2026-11 的生产沙箱实测结论（见 _claude_code_alive_probe
模块 docstring）：
- 真人活跃 / 无信号 / MemoryOS bound 产物排除 / 内置生码轻投递 /
  用户自建任务豁免 / MemoryOS 名称变体。
"""

import base64
import datetime
import json
import os
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.core.service.health_check.paas import (
    _claude_code_alive_probe as probe_mod,
)
from secbaas.community.core.service.health_check.paas._arca_paas_health_provider import (
    ClaudeCodeActiveHealthChecker,
)

BOUND_KEY = "user:350612:session:d181d444-ea25-41d5-b0b6-477897beff8f:agent:bot1"
BOUND_UUID = "d181d444-ea25-41d5-b0b6-477897beff8f"


def _utc(days_ago: int) -> str:
    """RFC3339 UTC 时间戳：2026-11-26T00:00:00Z 往前 days_ago 天。"""
    base = datetime.datetime(2026, 11, 26, 0, 0, 0, tzinfo=datetime.UTC)
    return (base - datetime.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def _fmt_utc(raw: str) -> str:
    """与 probe 相同的 RFC3339 -> 本地时间转换，用于构造期望值。"""
    iso = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return iso.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _fmt_epoch(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def _make_sessions_db(sessions_dir, rows) -> None:
    con = sqlite3.connect(sessions_dir / "sessions.sqlite3")
    con.execute(
        "CREATE TABLE sessions ("
        " session_key TEXT PRIMARY KEY,"
        " binding_json TEXT NOT NULL DEFAULT '{}',"
        " created_at TEXT NOT NULL,"
        " updated_at TEXT NOT NULL,"
        " session_kind TEXT NOT NULL DEFAULT 'user')"
    )
    con.executemany(
        "INSERT INTO sessions(session_key, created_at, updated_at, session_kind)"
        " VALUES (?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()


def _make_cron_tasks(sessions_dir, jobs) -> None:
    (sessions_dir / "cron-tasks.json").write_text(
        json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _make_jsonl(cc_dir, relpath: str, mtime_epoch: float) -> None:
    path = cc_dir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    os.utime(path, (mtime_epoch, mtime_epoch))


def _job(
    name: str, enabled: bool = True, bound_key: str | None = None, message: str = ""
) -> dict:
    return {
        "id": "cron-1",
        "name": name,
        "enabled": enabled,
        "schedule": {"kind": "cron", "expr": "0 2 * * *", "tz": "Asia/Shanghai"},
        "payload": {"kind": "agentTurn", "message": message},
        "boundSessionKey": bound_key,
        "state": {},
    }


class TestProbe:
    """probe() 纯函数场景矩阵。"""

    def test_real_user_session_active(self, tmp_path):
        """真人会话（1 天前）→ dbLatest 映射为本地时间。"""
        cc_dir = tmp_path / "claude"
        sessions_dir = tmp_path / "sessions"
        cc_dir.mkdir()
        sessions_dir.mkdir()
        _make_sessions_db(
            sessions_dir,
            [("user:1:session:aaa:agent:b", _utc(30), _utc(1), "user")],
        )

        result = probe_mod.probe(cc_dir=str(cc_dir), sessions_dir=str(sessions_dir))

        assert result["ok"] is True
        assert result["dbLatest"] == _fmt_utc(_utc(1))
        assert result["lastSessionTime"] == _fmt_utc(_utc(1))
        assert result["hasEnabledCron"] is False

    def test_builtin_bound_session_excluded(self, tmp_path):
        """MemoryOS bound session 每天刷新也必须被排除（沙箱 ARCA-18350b5a 实证）。"""
        cc_dir = tmp_path / "claude"
        sessions_dir = tmp_path / "sessions"
        cc_dir.mkdir()
        sessions_dir.mkdir()
        _make_cron_tasks(
            sessions_dir, [_job("MemoryOS每日知识诊断_bot1", bound_key=BOUND_KEY)]
        )
        _make_sessions_db(
            sessions_dir,
            [
                (BOUND_KEY, _utc(27), _utc(0), "user"),
                ("user:1:session:bbb:agent:b", _utc(60), _utc(43), "user"),
            ],
        )

        result = probe_mod.probe(cc_dir=str(cc_dir), sessions_dir=str(sessions_dir))

        assert result["dbLatest"] == _fmt_utc(_utc(43))
        assert result["lastSessionTime"] == _fmt_utc(_utc(43))
        assert result["builtinEnabledCount"] == 1
        assert result["builtinBoundExcluded"] == 1
        assert result["hasEnabledCron"] is False

    def test_builtin_bound_workspace_jsonl_excluded(self, tmp_path):
        """诊断任务在 bound workspace 里写的 jsonl 转录被排除。"""
        cc_dir = tmp_path / "claude"
        sessions_dir = tmp_path / "sessions"
        cc_dir.mkdir()
        sessions_dir.mkdir()
        _make_cron_tasks(
            sessions_dir, [_job("MemoryOS每日知识诊断_bot1", bound_key=BOUND_KEY)]
        )
        now = time.time()
        three_days_ago = now - 3 * 86400
        _make_jsonl(
            cc_dir,
            f"-home-admin--aicoding-workspace-{BOUND_UUID}/transcript.jsonl",
            now,
        )
        _make_jsonl(
            cc_dir,
            "-home-admin--aicoding-workspace-real/real.jsonl",
            three_days_ago,
        )

        result = probe_mod.probe(cc_dir=str(cc_dir), sessions_dir=str(sessions_dir))

        assert result["ccLatest"] == _fmt_epoch(three_days_ago)
        assert result["lastSessionTime"] == _fmt_epoch(three_days_ago)
        assert result["builtinBoundExcluded"] == 1

    def test_gencode_builtin_lightweight_not_active(self, tmp_path):
        """内置生码任务 enabled（轻投递、无产物）不算活跃（沙箱 ARCA-2bb6c719 实证）。"""
        cc_dir = tmp_path / "claude"
        sessions_dir = tmp_path / "sessions"
        cc_dir.mkdir()
        sessions_dir.mkdir()
        _make_cron_tasks(
            sessions_dir, [_job("7*24小时自动生码__20260714_bot", bound_key=None)]
        )
        _make_sessions_db(sessions_dir, [])

        result = probe_mod.probe(cc_dir=str(cc_dir), sessions_dir=str(sessions_dir))

        assert result["hasEnabledCron"] is False
        assert result["builtinEnabledCount"] == 1
        assert result["lastSessionTime"] == ""

    def test_user_created_job_counts_active(self, tmp_path):
        """用户自建任务（如 afsc需求自动承接）enabled 即活跃（不在名单内）。"""
        cc_dir = tmp_path / "claude"
        sessions_dir = tmp_path / "sessions"
        cc_dir.mkdir()
        sessions_dir.mkdir()
        _make_cron_tasks(sessions_dir, [_job("afsc需求自动承接")])

        result = probe_mod.probe(cc_dir=str(cc_dir), sessions_dir=str(sessions_dir))

        assert result["hasEnabledCron"] is True
        assert result["cronEnabledCount"] == 1
        assert result["builtinEnabledCount"] == 0

    def test_memoryos_name_variants_all_builtin(self, tmp_path):
        """MemoryOS 名称变体（定时保鲜记忆等）全部命中前缀名单。"""
        cc_dir = tmp_path / "claude"
        sessions_dir = tmp_path / "sessions"
        cc_dir.mkdir()
        sessions_dir.mkdir()
        bound2 = "user:9:session:11111111-2222-3333-4444-555555555555:agent:b"
        bound3 = "user:9:session:66666666-7777-8888-9999-000000000000:agent:b"
        _make_cron_tasks(
            sessions_dir,
            [
                _job("MemoryOS定时保鲜记忆_2026_bot", bound_key=bound2),
                _job("MemoryOS每日知识诊断_2026_bot", bound_key=bound3),
            ],
        )

        result = probe_mod.probe(cc_dir=str(cc_dir), sessions_dir=str(sessions_dir))

        assert result["builtinEnabledCount"] == 2
        assert result["builtinBoundExcluded"] == 2
        assert result["hasEnabledCron"] is False

    def test_disabled_builtin_not_counted_nor_excluded(self, tmp_path):
        """disabled 的内置任务不计数，其 bound session 不进排除清单。"""
        cc_dir = tmp_path / "claude"
        sessions_dir = tmp_path / "sessions"
        cc_dir.mkdir()
        sessions_dir.mkdir()
        _make_cron_tasks(
            sessions_dir,
            [_job("MemoryOS每日知识诊断_bot1", enabled=False, bound_key=BOUND_KEY)],
        )
        _make_sessions_db(sessions_dir, [(BOUND_KEY, _utc(40), _utc(39), "user")])

        result = probe_mod.probe(cc_dir=str(cc_dir), sessions_dir=str(sessions_dir))

        assert result["builtinEnabledCount"] == 0
        assert result["builtinBoundExcluded"] == 0
        # 未被排除的历史 bound session 仍作为普通会话信号参与
        assert result["dbLatest"] == _fmt_utc(_utc(39))

    def test_no_signal_returns_empty(self, tmp_path):
        """无 jsonl、空会话表、无任务 → lastSessionTime 空（沙箱 ARCA-dea94f38）。"""
        cc_dir = tmp_path / "claude"
        sessions_dir = tmp_path / "sessions"
        cc_dir.mkdir()
        sessions_dir.mkdir()
        _make_sessions_db(sessions_dir, [])

        result = probe_mod.probe(cc_dir=str(cc_dir), sessions_dir=str(sessions_dir))

        assert result["lastSessionTime"] == ""
        assert result["ccLatest"] == ""
        assert result["dbLatest"] == ""
        assert result["hasEnabledCron"] is False

    def test_missing_cron_file_defaults(self, tmp_path):
        """cron-tasks.json 不存在 → 无任务、无排除，会话源仍可用。"""
        cc_dir = tmp_path / "claude"
        sessions_dir = tmp_path / "sessions"
        cc_dir.mkdir()
        sessions_dir.mkdir()
        _make_sessions_db(
            sessions_dir, [("user:1:session:aaa:agent:b", _utc(5), _utc(2), "user")]
        )

        result = probe_mod.probe(cc_dir=str(cc_dir), sessions_dir=str(sessions_dir))

        assert result["hasEnabledCron"] is False
        assert result["builtinBoundExcluded"] == 0
        assert result["dbLatest"] == _fmt_utc(_utc(2))

    def test_non_user_kinds_excluded(self, tmp_path):
        """diagnosis 等非 user kind 的会话不参与活跃信号。"""
        cc_dir = tmp_path / "claude"
        sessions_dir = tmp_path / "sessions"
        cc_dir.mkdir()
        sessions_dir.mkdir()
        _make_sessions_db(
            sessions_dir,
            [
                ("user:1:session:d1:agent:b", _utc(50), _utc(20), "diagnosis"),
                ("user:1:session:d2:agent:b", _utc(50), _utc(10), "user"),
            ],
        )

        result = probe_mod.probe(cc_dir=str(cc_dir), sessions_dir=str(sessions_dir))

        assert result["dbLatest"] == _fmt_utc(_utc(10))

    def test_last_time_is_max_of_sources(self, tmp_path):
        """lastSessionTime 取双源最大值。"""
        cc_dir = tmp_path / "claude"
        sessions_dir = tmp_path / "sessions"
        cc_dir.mkdir()
        sessions_dir.mkdir()
        now = time.time()
        one_day_ago = now - 86400
        five_days_ago = now - 5 * 86400
        _make_jsonl(cc_dir, "proj/a.jsonl", one_day_ago)
        _make_sessions_db(
            sessions_dir,
            [("user:1:session:aaa:agent:b", _utc(50), _utc(30), "user")],
        )
        five_days_utc = "2026-11-21T00:00:00.000000Z"
        con = sqlite3.connect(sessions_dir / "sessions.sqlite3")
        con.execute(
            "INSERT INTO sessions(session_key, created_at, updated_at, session_kind)"
            " VALUES (?,?,?,?)",
            ("user:1:session:zzz:agent:b", _utc(40), five_days_utc, "user"),
        )
        con.commit()
        con.close()

        result = probe_mod.probe(cc_dir=str(cc_dir), sessions_dir=str(sessions_dir))

        assert result["ccLatest"] == _fmt_epoch(one_day_ago)
        assert result["dbLatest"] == _fmt_utc(five_days_utc)
        assert result["lastSessionTime"] == _fmt_epoch(one_day_ago)

    def test_payload_text_does_not_miscount(self, tmp_path):
        """payload.message 里出现 '\"enabled\": true' 字样不造成误计/漏计。"""
        cc_dir = tmp_path / "claude"
        sessions_dir = tmp_path / "sessions"
        cc_dir.mkdir()
        sessions_dir.mkdir()
        _make_cron_tasks(
            sessions_dir,
            [
                _job(
                    "我的任务",
                    message='提醒: 请注意 "enabled": true 字样不会干扰解析',
                )
            ],
        )

        result = probe_mod.probe(cc_dir=str(cc_dir), sessions_dir=str(sessions_dir))

        assert result["hasEnabledCron"] is True
        assert result["cronEnabledCount"] == 1
        assert result["builtinEnabledCount"] == 0


class TestClaudeCodeActiveHealthChecker:
    """checker 层：cmd 生成与 response 解析。"""

    def test_name(self) -> None:
        assert ClaudeCodeActiveHealthChecker().name == "active_claude_code"
        assert ClaudeCodeActiveHealthChecker(minutes=60).name == "active_claude_code"

    def test_build_cmd_shape(self) -> None:
        cmd = ClaudeCodeActiveHealthChecker._build_cmd()
        assert cmd.startswith("echo ")
        assert cmd.endswith(" | base64 -d | python3 -")

    def test_build_cmd_payload_compiles(self) -> None:
        """base64 载荷解码后是语法合法的探测脚本。"""
        cmd = ClaudeCodeActiveHealthChecker._build_cmd()
        b64 = cmd[len("echo ") : -len(" | base64 -d | python3 -")]
        source = base64.b64decode(b64).decode("utf-8")
        compile(source, "claude_code_alive_probe", "exec")
        assert "def probe" in source
        assert "lastSessionTime" in source
        assert "BUILTIN_CRON_NAME_PREFIXES" in source
        # 架构规则: 私有模块不带 __main__，入口由 checker 拼接
        assert "__main__" not in source
        assert source.rstrip().endswith("print(json.dumps(probe()))")

    @pytest.mark.asyncio
    async def test_check_live(self) -> None:
        checker = ClaudeCodeActiveHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = json.dumps(
            {
                "ok": True,
                "lastSessionTime": "2026-11-26 14:25:37",
                "hasEnabledCron": False,
                "ccLatest": "2026-10-20 03:32:42",
                "dbLatest": "2026-11-26 14:25:37",
                "cronEnabledCount": 0,
                "builtinEnabledCount": 2,
                "builtinBoundExcluded": 1,
            }
        )
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is True
        assert result.response["lastSessionTime"] == "2026-11-26 14:25:37"
        assert result.response["hasEnabledCron"] is False
        assert result.error is None
        assert result.timeout is False
        # 下发的命令就是探测脚本（base64 -> 裸 python3）
        sent_cmd = (
            mock_facade.execute_command.call_args.kwargs.get("cmd")
            or mock_facade.execute_command.call_args.args
        )
        assert sent_cmd == ClaudeCodeActiveHealthChecker._build_cmd()

    @pytest.mark.asyncio
    async def test_check_command_failure(self) -> None:
        checker = ClaudeCodeActiveHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 1
        mock_result.stderr = "python3: command not found"
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert "exit code 1" in result.error

    @pytest.mark.asyncio
    async def test_check_timeout(self) -> None:
        checker = ClaudeCodeActiveHealthChecker()
        mock_facade = MagicMock()
        mock_facade.execute_command = AsyncMock(side_effect=TimeoutError())

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert result.timeout is True

    @pytest.mark.asyncio
    async def test_check_exception(self) -> None:
        checker = ClaudeCodeActiveHealthChecker()
        mock_facade = MagicMock()
        mock_facade.execute_command = AsyncMock(
            side_effect=RuntimeError("probe failed")
        )

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert "probe failed" in result.error
