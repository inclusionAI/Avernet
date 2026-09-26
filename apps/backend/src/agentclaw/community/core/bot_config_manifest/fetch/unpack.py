"""Archive unpacking for manifest directory entries (W2, #1470).

The unpacker turns a fetched archive into a plain file tree under a
caller-supplied directory, with every guard the issue names enforced
*before* and *while* writing — a refused archive leaves no partial tree.

The two formats share one code path after member enumeration: names are
validated and stripped as strings, then members are streamed to disk by
extracting each file with a byte-counting copy, so neither format's
headers are trusted for sizes. Permissions are flattened afterwards
(uniform 0o644 / 0o755): executable or setuid bits from inside an archive
are never part of a workspace's contract — tools that must run are
``cli_tools`` (W9), which carries its own supply-chain gate.

Deliberate hardening beyond the issue text, stated in the module README:
**all link-type members are refused**, not only those whose targets escape
the root. The issue's minimum is "refuse escapes"; in-root links pass that
bar while still smuggling follow-semantics into a directory whose
ownership (W6) is defined by *covering the declared path*, and that
mismatch is where link bugs live. Refusing the class is one rule, zero
special cases.
"""

from __future__ import annotations

import io
import posixpath
import re
import shutil
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    ARCHIVE_MEMBER_LIMIT,
    FETCH_ENTRY_LIMITS,
)
from agentclaw.community.log import get_logger

logger = get_logger()

ArchiveKind = Literal["zip", "tar.gz"]

#: Windows drive-leading member names ("C:/x", "c:\\x") — refused as
#: absolute before any path arithmetic can normalize them away.
_DRIVE_RE = re.compile(r"^[A-Za-z]:")

_COPY_CHUNK = 256 * 1024

#: Uniform permission targets — see module docstring ("权限拍平")。
_DIR_MODE = 0o755
_FILE_MODE = 0o644


class UnpackError(Exception):
    """The archive was refused on safety rules, or malformed.

    Classification matters to the caller: ``FetchRefused``-style refusals
    and structural failures share one exception here because both mean
    "this archive never becomes a tree"; the apply layer (W4) records the
    entry as failed either way.
    """


@dataclass(frozen=True)
class UnpackedTree:
    """The extracted tree's inventory.

    ``members`` are archive-relative paths *after* ``strip_components`` —
    the same vocabulary the caller declared — sorted so two runs of the
    same bytes produce identical objects.
    """

    root: Path
    members: tuple[str, ...]
    total_size: int


def _check_member_name(name: str) -> list[str]:
    """Validate one member name; return its non-empty, non-`.` segments.

    Backslashes are normalized to slashes first: a zip written on Windows
    carries "kb\\a.md", and treating "\\\\" as a path character turns
    traversal ("..\\\\x") into a name the later ``/``-split would never
    see. After normalization the rules are pure POSIX.
    """
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        raise UnpackError(f"absolute member path: {name!r}")
    if _DRIVE_RE.match(normalized):
        raise UnpackError(f"drive-led member path: {name!r}")
    segments = [s for s in normalized.split("/") if s not in ("", ".")]
    if not segments:
        raise UnpackError(f"empty member path: {name!r}")
    if ".." in segments:
        raise UnpackError(f"member path escapes the root: {name!r}")
    return segments


def _strip(segments: list[str], strip_components: int, is_dir: bool, name: str) -> list[str]:
    """Strip exactly ``strip_components`` leading segments; no detection.

    A *directory* fully consumed by the strip becomes the root and needs no
    path (structural wrapper — the "``zip -r kb.zip kb/`` 壳目录" case in
    schema §3.2); a *file* with fewer layers than the strip is a
    configuration error and is refused outright, matching
    ``tar --strip-components``' refusal rather than silently relocating.
    """
    if strip_components == 0:
        return segments
    if len(segments) <= strip_components:
        if is_dir:
            return []
        raise UnpackError(
            f"member has fewer segments than strip_components={strip_components}: {name!r}"
        )
    return segments[strip_components:]


@dataclass(frozen=True)
class _Member:
    name: str          # archive-side name, for errors
    segments: list[str]
    is_dir: bool
    open_bytes: object  # callable -> binary stream, None for pure dirs


def _zip_members(archive: bytes) -> tuple[list[_Member], zipfile.ZipFile | None]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(archive))
    except (zipfile.BadZipFile, ValueError) as exc:
        raise UnpackError("malformed zip archive") from exc
    out: list[_Member] = []
    for info in zf.infolist():
        segments = _check_member_name(info.filename)
        is_dir = info.is_dir()
        if is_dir:
            out.append(_Member(info.filename, segments, True, None))
            continue
        # Link members in zips are carried as mode bits on otherwise
        # regular entries — the same class refusal tar gets.
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise UnpackError(f"link member refused: {info.filename!r}")
        out.append(
            _Member(
                info.filename, segments, False,
                lambda zf=zf, info=info: zf.open(info),
            )
        )
    return out, zf


def _tar_members(archive: bytes) -> tuple[list[_Member], tarfile.TarFile | None]:
    try:
        tf = tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise UnpackError("malformed tar.gz archive") from exc
    out: list[_Member] = []
    for member in tf.getmembers():
        segments = _check_member_name(member.name)
        if member.isdir():
            out.append(_Member(member.name, segments, True, None))
            continue
        if not member.isreg():
            # SYMTYPE / LNKTYPE / CHRTYPE / BLKTYPE / FIFOTYPE and any
            # exotic type: one rule, one refusal (module docstring).
            raise UnpackError(
                f"non-regular member refused: {member.name!r} "
                f"(type {member.type!r})"
            )
        if not segments:
            raise UnpackError(f"empty member path: {member.name!r}")
        out.append(
            _Member(
                member.name, segments, False,
                lambda tf=tf, member=member: tf.extractfile(member),  # type: ignore[arg-type,return-value]
            )
        )
    return out, tf


def unpack_archive(
    archive: bytes,
    kind: ArchiveKind,
    dest: Path,
    *,
    strip_components: int = 0,
    member_limit: int = ARCHIVE_MEMBER_LIMIT,
    unpacked_size_limit: int = FETCH_ENTRY_LIMITS["resources_unpacked"],
) -> UnpackedTree:
    """Extract ``archive`` to a fresh tree under ``dest``; refuse, atomically.

    Guards: member count, per-member name traversal/absolute/drive/link/
    device rules, strip exactness, and a running byte total enforced while
    streaming files — declared header sizes are never the authority. A
    refusal (any guard) leaves ``dest`` empty or absent.
    """
    if kind not in ("zip", "tar.gz"):
        raise UnpackError(f"unsupported archive kind: {kind!r}")
    if strip_components < 0:
        raise UnpackError(f"negative strip_components: {strip_components}")
    if dest.exists() and any(dest.rglob("*")):
        raise UnpackError(f"destination is not empty: {dest!r}")

    if kind == "zip":
        members, archive_handle = _zip_members(archive)
    else:
        members, archive_handle = _tar_members(archive)
    if len(members) > member_limit:
        raise UnpackError(
            f"{len(members)} members exceed the {member_limit}-member limit"
        )

    stripped = [
        _strip(m.segments, strip_components, m.is_dir, m.name) for m in members
    ]

    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    written: list[str] = []
    try:
        for member, final in zip(members, stripped):
            target = dest.joinpath(*final)
            if member.is_dir or not final:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            total += _stream_member(member, target, unpacked_size_limit - total)
            written.append(posixpath.join(*final))
    except (
        OSError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as exc:
        # 写盘阶段的原始异常归化:一个文件占用了目录名、CRC 校验失败、
        # tar 中途截断——契约(与 W4 的分类)说 malformed 就必须是
        # UnpackError,且不留半棵树。
        shutil.rmtree(dest, ignore_errors=True)
        raise UnpackError(f"malformed archive while writing: {exc}") from exc
    except Exception:
        # Refusals and anything else alike: no half tree survives.
        shutil.rmtree(dest, ignore_errors=True)
        raise
    finally:
        # The lazy per-member openers hold the archive handle; close it once
        # every stream is done (or failed), including on the refusal paths.
        if archive_handle is not None:
            archive_handle.close()

    # 权限拍平:统一普通文件/目录位——manifest 不交付可执行物。
    dest.chmod(_DIR_MODE)
    for path in sorted(dest.rglob("*"), reverse=True):
        if path.is_dir():
            path.chmod(_DIR_MODE)
        else:
            path.chmod(_FILE_MODE)

    tree = UnpackedTree(
        root=dest,
        members=tuple(sorted(written)),
        total_size=total,
    )
    logger.info(
        "[manifest.unpack] kind=%s members=%d bytes=%d strip=%d",
        kind, len(members), total, strip_components,
    )
    return tree


def _stream_member(member: _Member, target: Path, remaining: int) -> int:
    """Byte-counting copy; the running cap is the truth, headers are not."""
    opener = member.open_bytes
    assert opener is not None
    with opener() as source, open(target, "wb") as sink:
        copied = 0
        while True:
            chunk = source.read(_COPY_CHUNK)
            if not chunk:
                break
            copied += len(chunk)
            if copied > remaining:
                raise UnpackError(
                    f"unpacked size exceeds the limit while streaming: {member.name!r}"
                )
            sink.write(chunk)
    return copied
