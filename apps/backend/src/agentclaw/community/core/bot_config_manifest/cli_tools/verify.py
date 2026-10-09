"""What the platform checks before it distributes an executable (W9, #1477).

Two questions, and they are genuinely different:

* ``digest`` answers **"are these the bytes you asked for"**. The schema makes
  it mandatory for every ``cli_tools`` entry, and the fetch pipeline enforces
  it. It says nothing about what the bytes *are*.
* :func:`verify_amd64_elf` answers **"can this machine run them"**. A wrong
  binary — an arm64 build, a shell script, a README that happened to be the
  first file in the archive — has a perfectly valid digest. Without this
  check the platform would install it, the engine would mark it executable,
  and the failure would surface as an unreadable ``Exec format error`` inside
  a container days later.

:func:`select_subpath` answers the third question an archive raises: *which*
file in it is the command. One entry is one command is one file (schema §3.7),
so this refuses anything that is not exactly one regular file inside the tree.
"""
from __future__ import annotations

import struct
from pathlib import Path

from agentclaw.community.core.bot_config_manifest.fetch.unpack import UnpackedTree

#: Every ELF file starts with these four bytes.
ELF_MAGIC = b"\x7fELF"
#: ``e_machine`` sits at offset 18, two bytes, in the endianness ``EI_DATA``
#: (offset 5) declares. 0x3E is x86-64 — the architecture every ARCA and
#: teclaw container runs.
E_MACHINE_OFFSET = 18
EM_X86_64 = 0x3E
#: ``EI_DATA``: 1 = little-endian, 2 = big-endian.
EI_DATA_OFFSET = 5
#: ``EI_CLASS``: 1 = 32-bit, 2 = 64-bit. An x86-64 ``e_machine`` on a 32-bit
#: class is a malformed header, not a runnable binary.
EI_CLASS_OFFSET = 4
ELFCLASS64 = 2
#: ``e_type`` at offset 16, two bytes. Only these two can be executed: a
#: position-dependent executable and a PIE (which is what most toolchains
#: emit today, and which reads as a shared object).
E_TYPE_OFFSET = 16
ET_EXEC = 2
ET_DYN = 3
#: The 64-bit ELF header is 64 bytes. A file shorter than that cannot carry
#: one, whatever its first twenty bytes say — the check this constant replaced
#: stopped at ``e_machine``, so a twenty-byte fragment passed.
_ELF_HEADER_MINIMUM = 64

#: What ``e_type`` values mean, for a refusal that says what was found.
_TYPE_NAMES = {
    0: "an unknown-type ELF",
    1: "a relocatable object (.o)",
    4: "a core dump",
}

#: The few ``e_machine`` values worth naming in a refusal, so the message says
#: what the caller actually shipped instead of a bare number. Not exhaustive
#: on purpose — an unknown value is reported as a number, which is still the
#: answer to "what did I build this for".
_MACHINE_NAMES = {
    0x03: "x86 (32-bit)",
    0x28: "arm (32-bit)",
    0xB7: "aarch64",
    0xF3: "riscv",
    0x3E: "x86-64",
}


class CliToolVerificationError(ValueError):
    """The bytes are not a runnable tool for this platform."""


class CliToolSubpathError(ValueError):
    """``subpath`` does not name exactly one regular file inside the tree."""


def verify_amd64_elf(data: bytes, *, name: str) -> None:
    """Refuse anything that is not a runnable x86-64 ELF executable.

    Four questions, and all four are needed for the gate to mean what it says.
    Checking magic and ``e_machine`` alone accepts a twenty-byte fragment, an
    x86-64 *relocatable object*, and a core dump — each of which is stored,
    installed and then fails at exec time, which is exactly the outcome this
    check exists to move forward to install time.

    Raises:
        CliToolVerificationError: naming what was found, because "this is not
            an x86-64 executable" without saying what it *is* leaves the caller
            guessing between a wrong build, a wrapper script and a bad subpath.
    """
    if not data.startswith(ELF_MAGIC):
        raise CliToolVerificationError(
            f"{name!r} is not an ELF executable (it begins {data[:4]!r}); a CLI "
            "tool must be one self-contained x86-64 binary, not a script or an "
            "archive member picked by mistake"
        )
    if len(data) < _ELF_HEADER_MINIMUM:
        raise CliToolVerificationError(
            f"{name!r} is {len(data)} bytes — an ELF header alone is "
            f"{_ELF_HEADER_MINIMUM}, so this is a truncated file rather than a "
            "program"
        )
    if data[EI_CLASS_OFFSET] != ELFCLASS64:
        raise CliToolVerificationError(
            f"{name!r} is a 32-bit ELF; the containers that run it are x86-64"
        )
    little_endian = data[EI_DATA_OFFSET] != 2
    order = "<H" if little_endian else ">H"
    (machine,) = struct.unpack_from(order, data, E_MACHINE_OFFSET)
    if machine != EM_X86_64:
        found = _MACHINE_NAMES.get(machine, f"e_machine 0x{machine:02X}")
        raise CliToolVerificationError(
            f"{name!r} is an ELF built for {found}; the containers that run it "
            "are x86-64"
        )
    (file_type,) = struct.unpack_from(order, data, E_TYPE_OFFSET)
    if file_type not in (ET_EXEC, ET_DYN):
        found = _TYPE_NAMES.get(file_type, f"e_type 0x{file_type:02X}")
        raise CliToolVerificationError(
            f"{name!r} is {found}, not a program; a CLI tool must be an "
            "executable — link it rather than shipping the object file"
        )


def select_subpath(tree: UnpackedTree, subpath: str, *, location: str) -> Path:
    """The one declared file inside an unpacked archive.

    Three refusals, in the order a caller hits them: the member is not there,
    it is not a regular file, or it resolves outside the tree. The third is
    belt-and-braces — the unpacker refuses every link-type member, so nothing
    inside a tree it produced can point out of it — kept because this function
    takes a ``Path`` and a future caller may hand it a tree built by something
    with laxer rules.

    Raises:
        CliToolSubpathError: naming ``location`` so a manifest entry's failure
            says which entry.
    """
    normalised = subpath.strip("/")
    if not normalised or normalised not in tree.members:
        available = ", ".join(sorted(tree.members)[:10]) or "(the archive has no files)"
        raise CliToolSubpathError(
            f"{location}: the archive has no member {subpath!r}; it contains "
            f"{available}"
        )
    root = tree.root.resolve()
    candidate = (tree.root / normalised).resolve()
    if not candidate.is_file():
        raise CliToolSubpathError(
            f"{location}: {subpath!r} is not a regular file"
        )
    if root != candidate and root not in candidate.parents:
        raise CliToolSubpathError(
            f"{location}: {subpath!r} resolves outside the unpacked archive"
        )
    return candidate


__all__ = [
    "EI_CLASS_OFFSET",
    "EI_DATA_OFFSET",
    "ELFCLASS64",
    "ET_DYN",
    "ET_EXEC",
    "E_TYPE_OFFSET",
    "ELF_MAGIC",
    "EM_X86_64",
    "E_MACHINE_OFFSET",
    "CliToolSubpathError",
    "CliToolVerificationError",
    "select_subpath",
    "verify_amd64_elf",
]
