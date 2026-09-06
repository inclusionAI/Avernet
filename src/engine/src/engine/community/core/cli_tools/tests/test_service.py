"""``LocalCliToolsService`` — the behaviours the HTTP layer cannot show.

Each test here pins something a router test would miss: the *ordering* inside a
replacement, what an interrupted write leaves behind, and whether a listing is
truth or replay.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from engine.community.core.cli_tools.directories import (
    bot_cli_dir,
    cli_dir_beside,
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
    def test_the_rule_is_the_workspace_sibling(self):
        assert cli_dir_beside(Path("/bots/b7/workspace")) == Path("/bots/b7/cli")

    def test_follows_the_injected_workspace(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", "/bots/b7/openclaw/workspace")

        assert bot_cli_dir() == Path("/bots/b7/openclaw/cli")

    def test_falls_back_to_the_arca_image_convention(self, monkeypatch):
        """``/home/admin/.openclaw/workspace`` is the image's *bot* workspace.

        ``docker/agent/start_claude_code.sh`` points that engine's agent at it
        too, so the fallback is right for both engines — the ``.openclaw`` in
        the name is the image's convention, not one engine's private tree.
        """
        monkeypatch.delenv("OPENCLAW_WORKSPACE_DIR", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: Path("/home/admin")))

        assert bot_cli_dir() == Path("/home/admin/.openclaw/cli")

    def test_two_bots_on_one_host_never_share_a_tool_directory(self, monkeypatch):
        """The isolation that matters on singlebox.

        BaaS injects the workspace per bot *and* per engine, so reading it is
        what keeps bots apart. A fixed constant would give every bot on the
        host one tool directory, and one bot's whole-set replacement would
        delete another bot's tools.
        """
        monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", "/data/bot_a/openclaw/workspace")
        bot_a = bot_cli_dir()
        monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", "/data/bot_b/openclaw/workspace")
        bot_b = bot_cli_dir()

        assert bot_a != bot_b

    def test_two_engines_for_one_bot_never_share_a_tool_directory(self, monkeypatch):
        """BaaS puts the engine in the path, so a bot's two engines differ."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", "/data/bot_a/openclaw/workspace")
        as_openclaw = bot_cli_dir()
        monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", "/data/bot_a/claude_code/workspace")
        as_claude_code = bot_cli_dir()

        assert as_openclaw != as_claude_code

    def test_resolution_is_lazy_not_bound_at_construction(self, monkeypatch):
        """The env var is injected at spawn, after the engine object exists."""
        service = LocalCliToolsService(bot_cli_dir)
        monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", "/bots/late/workspace")

        assert service._dir() == Path("/bots/late/cli")
