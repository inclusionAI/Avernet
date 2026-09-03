"""What the platform checks before it distributes an executable (W9).

``digest`` answers "are these the bytes you asked for"; these two answer "can
this machine run them" and "which file in the archive is the command". The
second and third refusals in :func:`select_subpath` are unreachable through
``unpack_archive`` — it refuses every link-type member — so they are exercised
directly here, which is the only way to know the belt is buckled.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.cli_tools.verify import (
    CliToolSubpathError,
    CliToolVerificationError,
    select_subpath,
    verify_amd64_elf,
)
from agentclaw.community.core.bot_config_manifest.fetch.unpack import UnpackedTree


def _elf(machine: int, *, data_byte: int = 1, tail: bytes = b"\x00" * 32) -> bytes:
    header = bytearray(b"\x7fELF\x02" + bytes([data_byte]) + b"\x01" + b"\x00" * 13)
    header[18:20] = machine.to_bytes(2, "little" if data_byte != 2 else "big")
    return bytes(header) + tail


# ── the architecture check ────────────────────────────────────────────────


def test_an_x86_64_elf_passes() -> None:
    verify_amd64_elf(_elf(0x3E), name="mycli")


def test_a_file_too_short_to_hold_a_header_is_refused() -> None:
    with pytest.raises(CliToolVerificationError) as excinfo:
        verify_amd64_elf(b"\x7fELF", name="mycli")
    assert "too short" in str(excinfo.value)


def test_a_non_elf_file_says_what_it_begins_with() -> None:
    with pytest.raises(CliToolVerificationError) as excinfo:
        verify_amd64_elf(b"#!/bin/sh\n" + b"\x00" * 32, name="mycli")
    assert "not an ELF" in str(excinfo.value)


@pytest.mark.parametrize(
    "machine,expected",
    [(0xB7, "aarch64"), (0x28, "arm (32-bit)"), (0x03, "x86 (32-bit)"), (0xF3, "riscv")],
)
def test_a_wrong_architecture_names_what_was_found(machine, expected) -> None:
    """Without the name, the caller is guessing between a wrong build, a
    wrapper script and a bad subpath."""
    with pytest.raises(CliToolVerificationError) as excinfo:
        verify_amd64_elf(_elf(machine), name="mycli")
    assert expected in str(excinfo.value)


def test_an_unnamed_architecture_is_reported_as_its_number() -> None:
    with pytest.raises(CliToolVerificationError) as excinfo:
        verify_amd64_elf(_elf(0x5A), name="mycli")
    assert "0x5A" in str(excinfo.value)


def test_a_big_endian_elf_reads_e_machine_big_endian() -> None:
    """``EI_DATA`` decides how to read the two bytes; reading them the other
    way would report an absurd architecture for an otherwise valid file."""
    with pytest.raises(CliToolVerificationError) as excinfo:
        verify_amd64_elf(_elf(0xB7, data_byte=2), name="mycli")
    assert "aarch64" in str(excinfo.value)


# ── selecting the one file ────────────────────────────────────────────────


def _tree(tmp_path, members: tuple[str, ...]) -> UnpackedTree:
    root = tmp_path / "tree"
    root.mkdir()
    return UnpackedTree(root=root, members=members, total_size=0)


def test_the_declared_member_is_returned(tmp_path) -> None:
    tree = _tree(tmp_path, ("bin/mycli",))
    (tree.root / "bin").mkdir()
    (tree.root / "bin" / "mycli").write_bytes(b"x")
    assert select_subpath(tree, "bin/mycli", location="mycli").read_bytes() == b"x"


def test_a_leading_slash_is_tolerated(tmp_path) -> None:
    tree = _tree(tmp_path, ("mycli",))
    (tree.root / "mycli").write_bytes(b"x")
    assert select_subpath(tree, "/mycli", location="mycli").name == "mycli"


def test_an_absent_member_names_what_the_archive_holds(tmp_path) -> None:
    tree = _tree(tmp_path, ("a", "b"))
    with pytest.raises(CliToolSubpathError) as excinfo:
        select_subpath(tree, "c", location="mycli")
    assert "a, b" in str(excinfo.value)


def test_an_empty_archive_says_so(tmp_path) -> None:
    with pytest.raises(CliToolSubpathError) as excinfo:
        select_subpath(_tree(tmp_path, ()), "c", location="mycli")
    assert "no files" in str(excinfo.value)


def test_a_member_that_is_not_a_regular_file_is_refused(tmp_path) -> None:
    """One entry is one command is one *file*."""
    tree = _tree(tmp_path, ("bin",))
    (tree.root / "bin").mkdir()
    with pytest.raises(CliToolSubpathError) as excinfo:
        select_subpath(tree, "bin", location="mycli")
    assert "not a regular file" in str(excinfo.value)


def test_a_member_resolving_outside_the_tree_is_refused(tmp_path) -> None:
    """Belt behind the unpacker's brace: it refuses every link-type member, so
    nothing in a tree it produced can point out of one. This function takes a
    tree, and a future caller may build one with laxer rules."""
    outside = tmp_path / "outside"
    outside.write_bytes(b"secret")
    tree = _tree(tmp_path, ("escape",))
    (tree.root / "escape").symlink_to(outside)
    with pytest.raises(CliToolSubpathError) as excinfo:
        select_subpath(tree, "escape", location="mycli")
    assert "outside" in str(excinfo.value)
