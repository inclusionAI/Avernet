"""``LocalCliToolsService`` — the behaviours the HTTP layer cannot show.

Each test here pins something a router test would miss: the *ordering* inside a
replacement, what an interrupted write leaves behind, and whether a listing is
truth or replay.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from engine.community.core.cli_tools.directories import (
    ENGINE_CLI_DIRS,
    cli_dir_beside,
    cli_dir_env_var,
    cli_dir_for,
    cli_dir_resolver,
    default_cli_dir,
)
from engine.community.core.cli_tools.models import CliToolPayload
from engine.community.core.cli_tools.service import (
    MAX_NAME_LENGTH,
    TOOL_MODE,
    InvalidCliToolNameError,
    LocalCliToolsService,
)

ELF = b"\x7fELF\x02\x01\x01"


@pytest.fixture
def cli_dir(tmp_path: Path) -> Path:
    return tmp_path / "cli"


@pytest.fixture
def service(cli_dir: Path) -> LocalCliToolsService:
    return LocalCliToolsService(lambda: cli_dir)


class TestInstall:
    async def test_places_an_executable(self, service, cli_dir):
        await service.install("mycli", ELF + b"one")

        placed = cli_dir / "mycli"
        assert placed.read_bytes() == ELF + b"one"
        assert placed.stat().st_mode & 0o777 == TOOL_MODE

    async def test_leaves_every_other_tool_alone(self, service, cli_dir):
        await service.install("keep", ELF + b"keep")
        await service.install("mycli", ELF + b"one")

        await service.install("mycli", ELF + b"two")

        assert (cli_dir / "keep").read_bytes() == ELF + b"keep"
        assert (cli_dir / "mycli").read_bytes() == ELF + b"two"

    async def test_creates_the_directory_on_first_install(self, service, cli_dir):
        assert not cli_dir.exists()

        await service.install("mycli", ELF)

        assert (cli_dir / "mycli").is_file()

    async def test_a_failed_write_leaves_nothing_runnable(
        self, service, cli_dir, monkeypatch
    ):
        """Interrupted before ``os.replace``: no partial file under the name."""
        await service.install("mycli", ELF + b"old")

        def boom(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            await service.install("mycli", ELF + b"new")

        # The previous tool survives, and no scratch file is left behind.
        assert (cli_dir / "mycli").read_bytes() == ELF + b"old"
        assert sorted(p.name for p in cli_dir.iterdir()) == ["mycli"]

    async def test_at_the_documented_size_cap(self, service, cli_dir):
        """200 MiB is the platform's single-file ceiling (fetch/limits.py)."""
        payload = b"\x7fELF" + b"\0" * (200 * 1024 * 1024 - 4)

        await service.install("big", payload)

        assert (cli_dir / "big").stat().st_size == 200 * 1024 * 1024


class TestNameValidation:
    @pytest.mark.parametrize(
        "name", ["../escape", "a/b", "", ".", "..", "a\\b", " padded", "x\0y"]
    )
    async def test_a_dangerous_name_is_refused_and_writes_nothing(
        self, service, cli_dir, name
    ):
        with pytest.raises(InvalidCliToolNameError):
            await service.install(name, ELF)

        assert not cli_dir.exists() or list(cli_dir.iterdir()) == []

    async def test_the_guard_also_covers_reads_and_deletes(self, service):
        with pytest.raises(InvalidCliToolNameError):
            await service.read_tool("../secret")
        with pytest.raises(InvalidCliToolNameError):
            await service.delete("../secret")


class TestDelete:
    async def test_removes_the_command(self, service, cli_dir):
        await service.install("mycli", ELF)

        await service.delete("mycli")

        assert not (cli_dir / "mycli").exists()

    async def test_an_absent_command_is_success(self, service):
        await service.delete("never-existed")  # must not raise


class TestList:
    async def test_empty_when_nothing_was_ever_installed(self, service):
        assert await service.list_tools() == []

    async def test_reflects_a_file_written_behind_its_back(self, service, cli_dir):
        """Drift, not replay — the whole reason this endpoint exists."""
        await service.install("mycli", ELF)
        cli_dir.joinpath("sneaky").write_bytes(b"placed by hand")

        assert sorted(i.name for i in await service.list_tools()) == [
            "mycli",
            "sneaky",
        ]

    async def test_md5_changes_when_a_binary_is_swapped_in_place(
        self, service, cli_dir
    ):
        await service.install("mycli", ELF + b"one")
        before = (await service.list_tools())[0]

        cli_dir.joinpath("mycli").write_bytes(ELF + b"different")
        after = (await service.list_tools())[0]

        assert after.name == before.name  # same name...
        assert after.md5 != before.md5  # ...different binary, and it shows

    async def test_ignores_in_flight_scratch_files(self, service, cli_dir):
        await service.install("mycli", ELF)
        cli_dir.joinpath(".mycli.abc.part").write_bytes(b"half")

        assert [i.name for i in await service.list_tools()] == ["mycli"]


class TestReadTool:
    async def test_returns_bytes_and_hash(self, service):
        await service.install("mycli", ELF + b"one")

        got = await service.read_tool("mycli")

        assert got is not None
        assert got.data == ELF + b"one"
        assert got.size_bytes == len(ELF) + 3
        assert got.md5 == (await service.list_tools())[0].md5

    async def test_absent_is_none_not_an_error(self, service):
        assert await service.read_tool("nope") is None


class TestReplaceAll:
    async def test_removes_tools_not_named_in_the_request(self, service, cli_dir):
        await service.install("old", ELF + b"old")
        await service.install("kept", ELF + b"kept")

        await service.replace_all([CliToolPayload("kept", ELF + b"kept")])

        assert sorted(p.name for p in cli_dir.iterdir()) == ["kept"]

    async def test_an_empty_set_clears_every_tool(self, service, cli_dir):
        await service.install("a", ELF)
        await service.install("b", ELF)

        results = await service.replace_all([])

        assert results == []
        assert list(cli_dir.iterdir()) == []

    async def test_answers_for_every_requested_name(self, service):
        results = await service.replace_all(
            [CliToolPayload("a", ELF), CliToolPayload("b", ELF)]
        )

        assert {r.name for r in results} == {"a", "b"}
        assert all(r.success for r in results)

    async def test_a_partial_failure_is_reported_not_raised(self, service, cli_dir):
        results = await service.replace_all(
            [CliToolPayload("good", ELF), CliToolPayload("../bad", ELF)]
        )

        verdicts = {r.name: r for r in results}
        assert verdicts["good"].success is True
        assert verdicts["../bad"].success is False
        assert verdicts["../bad"].message  # carries the engine's own reason
        assert sorted(p.name for p in cli_dir.iterdir()) == ["good"]

    async def test_a_failed_install_does_not_delete_what_it_replaced(
        self, service, cli_dir, monkeypatch
    ):
        await service.install("mycli", ELF + b"old")

        real_replace = os.replace

        def fail_for_mycli(src, dst, *args, **kwargs):
            if str(dst).endswith("mycli"):
                raise OSError("disk full")
            return real_replace(src, dst, *args, **kwargs)

        monkeypatch.setattr(os, "replace", fail_for_mycli)
        results = await service.replace_all([CliToolPayload("mycli", ELF + b"new")])

        assert results[0].success is False
        # Degrades to "unchanged", never to "removed".
        assert (cli_dir / "mycli").read_bytes() == ELF + b"old"

    async def test_installs_before_pruning(self, service, cli_dir, monkeypatch):
        """No window in which a tool the request keeps is missing.

        Observed from inside the install of the second tool: at that moment
        the first must already be on disk *and* the tool being dropped must
        still be there — which is only true if every install precedes every
        delete.
        """
        await service.install("dropped", ELF + b"dropped")
        await service.install("kept", ELF + b"kept")

        seen: list[list[str]] = []
        real_replace = os.replace

        def observe(src, dst, *args, **kwargs):
            result = real_replace(src, dst, *args, **kwargs)
            seen.append(sorted(p.name for p in cli_dir.iterdir()))
            return result

        monkeypatch.setattr(os, "replace", observe)
        await service.replace_all(
            [CliToolPayload("kept", ELF + b"v2"), CliToolPayload("added", ELF)]
        )

        assert seen[-1] == ["added", "dropped", "kept"], (
            "the tool being dropped was pruned before the last install landed"
        )
        assert sorted(p.name for p in cli_dir.iterdir()) == ["added", "kept"]


class TestDirectories:
    """Where tools land, and the knobs that move it."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("BOT_CLI_DIR", raising=False)
        for engine in ("OPENCLAW", "CLAUDE_CODE"):
            monkeypatch.delenv(f"BOT_CLI_DIR_{engine}", raising=False)

    def test_the_default_rule_is_the_workspace_sibling(self):
        assert cli_dir_beside(Path("/bots/b7/workspace")) == Path("/bots/b7/cli")

    def test_the_default_follows_the_injected_workspace(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", "/bots/b7/openclaw/workspace")

        assert cli_dir_for("openclaw") == Path("/bots/b7/openclaw/cli")

    def test_the_default_falls_back_to_the_image_convention(self, monkeypatch):
        monkeypatch.delenv("OPENCLAW_WORKSPACE_DIR", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: Path("/home/admin")))

        assert default_cli_dir() == Path("/home/admin/.openclaw/cli")


class TestTuning:
    """Every knob, in precedence order — no code change required for any."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", "/bots/b7/claude_code/workspace")
        monkeypatch.delenv("BOT_CLI_DIR", raising=False)
        for engine in ("OPENCLAW", "CLAUDE_CODE"):
            monkeypatch.delenv(f"BOT_CLI_DIR_{engine}", raising=False)

    def test_the_per_engine_variable_is_named_predictably(self):
        assert cli_dir_env_var("claude_code") == "BOT_CLI_DIR_CLAUDE_CODE"

    def test_a_global_override_moves_every_engine(self, monkeypatch):
        monkeypatch.setenv("BOT_CLI_DIR", "/opt/tools")

        assert cli_dir_for("claude_code") == Path("/opt/tools")
        assert cli_dir_for("openclaw") == Path("/opt/tools")

    def test_a_per_engine_override_moves_only_that_engine(self, monkeypatch):
        monkeypatch.setenv("BOT_CLI_DIR_CLAUDE_CODE", "/opt/cc-tools")

        assert cli_dir_for("claude_code") == Path("/opt/cc-tools")
        assert cli_dir_for("openclaw") == Path("/bots/b7/claude_code/cli")

    def test_the_per_engine_override_beats_the_global_one(self, monkeypatch):
        monkeypatch.setenv("BOT_CLI_DIR", "/opt/tools")
        monkeypatch.setenv("BOT_CLI_DIR_CLAUDE_CODE", "/opt/cc-tools")

        assert cli_dir_for("claude_code") == Path("/opt/cc-tools")
        assert cli_dir_for("openclaw") == Path("/opt/tools")

    def test_an_override_is_used_verbatim(self, monkeypatch):
        """No ``cli`` is appended — what you set is where tools land."""
        monkeypatch.setenv("BOT_CLI_DIR_CLAUDE_CODE", "/somewhere/else/bin")

        assert cli_dir_for("claude_code") == Path("/somewhere/else/bin")

    def test_a_blank_override_is_ignored_not_obeyed(self, monkeypatch):
        """An empty variable must not resolve tools to the filesystem root."""
        monkeypatch.setenv("BOT_CLI_DIR_CLAUDE_CODE", "   ")

        assert cli_dir_for("claude_code") == Path("/bots/b7/claude_code/cli")

    def test_the_code_table_moves_one_engine_without_env(self, monkeypatch):
        monkeypatch.setitem(
            ENGINE_CLI_DIRS, "claude_code", lambda: Path("/home/admin/.claude_code/cli")
        )

        assert cli_dir_for("claude_code") == Path("/home/admin/.claude_code/cli")
        assert cli_dir_for("openclaw") == Path("/bots/b7/claude_code/cli")

    def test_an_env_override_beats_the_code_table(self, monkeypatch):
        monkeypatch.setitem(
            ENGINE_CLI_DIRS, "claude_code", lambda: Path("/home/admin/.claude_code/cli")
        )
        monkeypatch.setenv("BOT_CLI_DIR_CLAUDE_CODE", "/opt/cc-tools")

        assert cli_dir_for("claude_code") == Path("/opt/cc-tools")

    def test_two_bots_on_one_host_never_share_a_tool_directory(self, monkeypatch):
        """The isolation the default exists to preserve.

        BaaS injects the workspace per bot *and* per engine, so reading it is
        what keeps bots apart. A fixed constant would give every bot on the
        host one tool directory, and one bot's whole-set replacement would
        delete another bot's tools.
        """
        monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", "/data/bot_a/openclaw/workspace")
        bot_a = cli_dir_for("openclaw")
        monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", "/data/bot_b/openclaw/workspace")
        bot_b = cli_dir_for("openclaw")

        assert bot_a != bot_b

    def test_resolution_is_lazy_not_bound_at_construction(self, monkeypatch):
        """Overrides and the injected workspace both land after startup."""
        service = LocalCliToolsService(cli_dir_resolver("claude_code"))
        monkeypatch.setenv("BOT_CLI_DIR_CLAUDE_CODE", "/set/afterwards")

        assert service._dir() == Path("/set/afterwards")


class TestErrorPaths:
    """The defensive branches, exercised rather than assumed."""

    async def test_a_failure_opening_the_scratch_file_leaks_no_descriptor(
        self, service, cli_dir, monkeypatch
    ):
        """``mkstemp`` hands back a raw fd that only ``fdopen`` takes over."""
        opened: list[int] = []
        real_mkstemp = tempfile.mkstemp

        def record(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            opened.append(fd)
            return fd, path

        monkeypatch.setattr(tempfile, "mkstemp", record)
        monkeypatch.setattr(
            os, "fdopen", lambda *a, **k: (_ for _ in ()).throw(MemoryError("no room"))
        )

        with pytest.raises(MemoryError):
            await service.install("mycli", ELF)

        # The descriptor was closed, so re-using its number does not raise.
        for fd in opened:
            with pytest.raises(OSError):
                os.fstat(fd)
        assert list(cli_dir.iterdir()) == []

    async def test_a_directory_in_the_tool_directory_is_not_a_tool(
        self, service, cli_dir
    ):
        await service.install("mycli", ELF)
        (cli_dir / "subdir").mkdir()

        assert [i.name for i in await service.list_tools()] == ["mycli"]
        assert await service.read_tool("subdir") is None

    async def test_a_tool_directory_that_is_a_file_reads_as_empty(
        self, tmp_path
    ):
        """A path that exists but is not a directory is "no commands", not a crash."""
        not_a_dir = tmp_path / "cli"
        not_a_dir.write_bytes(b"surprise")
        service = LocalCliToolsService(lambda: not_a_dir)

        assert await service.list_tools() == []

    async def test_an_unsyncable_directory_does_not_fail_the_install(
        self, service, cli_dir, monkeypatch
    ):
        """The durability fsync is best-effort; some filesystems refuse it."""
        real_open = os.open

        def refuse_dir_open(path, flags, *args, **kwargs):
            if str(path) == str(cli_dir):
                raise OSError("cannot open directory")
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", refuse_dir_open)
        await service.install("mycli", ELF)

        assert (cli_dir / "mycli").read_bytes() == ELF
