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
    claude_code_cli_dir,
    cli_dir_beside,
    openclaw_cli_dir,
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

    def test_openclaw_follows_the_injected_workspace(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", "/bots/b7/workspace")

        assert openclaw_cli_dir() == Path("/bots/b7/cli")

    def test_openclaw_falls_back_to_the_shared_arca_layout(self, monkeypatch):
        monkeypatch.delenv("OPENCLAW_WORKSPACE_DIR", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: Path("/home/admin")))

        assert openclaw_cli_dir() == Path("/home/admin/.openclaw/cli")

    def test_claude_code_has_its_own_tree(self):
        assert claude_code_cli_dir() == Path("/home/admin/.claude_code/cli")

    def test_the_two_engines_never_share_a_directory(self, monkeypatch):
        """A future engine must not silently inherit OpenClaw's tree."""
        monkeypatch.delenv("OPENCLAW_WORKSPACE_DIR", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: Path("/home/admin")))

        assert openclaw_cli_dir() != claude_code_cli_dir()

    def test_resolution_is_lazy_not_bound_at_construction(self, monkeypatch):
        """The env var is injected at spawn, after the engine object exists."""
        service = LocalCliToolsService(openclaw_cli_dir)
        monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", "/bots/late/workspace")

        assert service._dir() == Path("/bots/late/cli")


class TestHostileDirectoryContents:
    """The tool directory lives inside the bot container, where the agent can
    create files. Nothing it can put there may break delivery or leak data."""

    async def test_prune_survives_a_disk_name_the_validator_would_refuse(
        self, service, cli_dir
    ):
        """A file the agent created can carry a name no caller could send.

        Routing disk names back through the caller-input validator made the
        prune raise, which both lost every per-name verdict and left the
        replacement half-applied — permanently, since the offending file could
        never be removed by that path.
        """
        await service.install("kept", ELF)
        await service.install("dropped", ELF)
        cli_dir.joinpath("trail ").write_bytes(b"agent-made")
        cli_dir.joinpath("back\\slash").write_bytes(b"agent-made")

        results = await service.replace_all([CliToolPayload("kept", ELF + b"v2")])

        assert [(r.name, r.success) for r in results] == [("kept", True)]
        # Everything unnamed is gone, hostile names included.
        assert sorted(p.name for p in cli_dir.iterdir()) == ["kept"]

    async def test_a_tool_named_like_a_scratch_file_is_still_a_tool(
        self, service, cli_dir
    ):
        """``unpack.part`` is a legal tool name — dots are allowed after the
        first character — so the scratch filter must not swallow it."""
        await service.install("unpack.part", ELF)

        assert [i.name for i in await service.list_tools()] == ["unpack.part"]

        # ...and it is prunable, rather than stranded on the bot forever.
        await service.replace_all([])
        assert list(cli_dir.iterdir()) == []

    async def test_a_symlink_is_not_a_readable_tool(self, service, cli_dir, tmp_path):
        """Validating the name bounds the path built; it says nothing about
        what that path points at. Following one would make the download
        endpoint an arbitrary read of anything the engine account can open."""
        secret = tmp_path / "secret.txt"
        secret.write_bytes(b"TOP SECRET OUTSIDE THE CLI DIR")
        cli_dir.mkdir(parents=True, exist_ok=True)
        cli_dir.joinpath("leak").symlink_to(secret)

        assert await service.read_tool("leak") is None
        assert [i.name for i in await service.list_tools()] == []

    async def test_an_unremovable_entry_does_not_abort_the_rest_of_the_prune(
        self, service, cli_dir, monkeypatch
    ):
        await service.install("kept", ELF)
        await service.install("dropped_a", ELF)
        await service.install("dropped_b", ELF)

        real_unlink = Path.unlink

        def stubborn(self, *args, **kwargs):
            if self.name == "dropped_a":
                raise PermissionError("immutable")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", stubborn)
        results = await service.replace_all([CliToolPayload("kept", ELF)])

        assert results[0].success is True
        # dropped_b was still pruned despite dropped_a refusing.
        assert "dropped_b" not in {p.name for p in cli_dir.iterdir()}

    async def test_a_refused_name_leaves_an_existing_directory_untouched(
        self, service, cli_dir
    ):
        await service.install("kept", ELF)

        with pytest.raises(InvalidCliToolNameError):
            await service.install("../escape", ELF)

        assert sorted(p.name for p in cli_dir.iterdir()) == ["kept"]

    async def test_a_name_too_long_to_place_is_refused_consistently(self, service):
        """Rejected by validation rather than only by ``install``.

        ``mkstemp`` adds 15 characters, so an over-long name used to pass
        validation, list and delete but fail on install alone — surfacing as a
        permanently failing apply entry with no obvious cause.
        """
        too_long = "x" * (MAX_NAME_LENGTH + 1)

        for call in (
            service.install(too_long, ELF),
            service.delete(too_long),
            service.read_tool(too_long),
        ):
            with pytest.raises(InvalidCliToolNameError):
                await call


class TestConcurrency:
    async def test_a_concurrent_install_is_not_pruned_by_a_replacement(
        self, service, cli_dir
    ):
        """Unserialised, a replacement that had already installed its set
        could prune a tool a concurrent install just reported as placed — the
        platform records it as installed and the bot does not have it."""
        import asyncio

        await service.install("existing", ELF)

        await asyncio.gather(
            service.replace_all([CliToolPayload("existing", ELF + b"v2")]),
            service.install("concurrent", ELF),
        )

        names = {p.name for p in cli_dir.iterdir()}
        # Whichever order they serialised in, "concurrent" was reported
        # successful, so it must be on disk.
        assert "concurrent" in names
