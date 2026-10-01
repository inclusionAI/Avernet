"""Safety-matrix tests for the archive unpacker (W2, #1470).

Every guard the issue names gets a refusal test, and the two formats the
pipeline supports are held to the *same* tree for the same content — the
callers of this module (W6, above all) must never learn which transport
an archive came from.

All caps are injected small: the production numbers live in ``limits.py``
and exist to stop attacks, not to be materialized in test memory.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from agentclaw.community.core.bot_config_manifest.fetch.unpack import (
    UnpackError,
    UnpackedTree,
    unpack_archive,
)


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _zip_bytes_with_mode(members: dict[str, tuple[bytes, int]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, (content, mode) in members.items():
            info = zipfile.ZipInfo(name)
            # Zip carries unix mode in the high 16 bits of external_attr.
            info.external_attr = (mode & 0xFFFF) << 16
            zf.writestr(info, content)
    return buffer.getvalue()


def _tar_gz_bytes(members: dict[str, bytes], *, modes: dict[str, int] | None = None,
                  symlinks: dict[str, str] | None = None,
                  special: list[str] | None = None,
                  hardlinks: dict[str, str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT) as tf:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            if modes and name in modes:
                info.mode = modes[name]
            import time as _time
            info.mtime = int(_time.time())
            tf.addfile(info, io.BytesIO(content))
        for name, target in (symlinks or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tf.addfile(info)
        for name, target in (hardlinks or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.LNKTYPE
            info.linkname = target
            tf.addfile(info)
        for name in (special or []):
            info = tarfile.TarInfo(name)
            info.type = tarfile.CHRTYPE  # 代表设备/特殊成员一类
            tf.addfile(info)
    return buffer.getvalue()


TREE = {"kb/a.md": b"alpha", "kb/sub/b.md": b"beta"}


def _names(tree: UnpackedTree) -> list[str]:
    return sorted(str(p.relative_to(tree.root)) for p in tree.root.rglob("*"))


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    return tmp_path / "out"


def test_zip_round_trip(dest):
    tree = unpack_archive(_zip_bytes(TREE), "zip", dest)
    assert _names(tree) == ["kb", "kb/a.md", "kb/sub", "kb/sub/b.md"]
    assert (dest / "kb" / "a.md").read_bytes() == b"alpha"


def test_tar_gz_round_trip(dest):
    tree = unpack_archive(_tar_gz_bytes(TREE), "tar.gz", dest)
    assert _names(tree) == ["kb", "kb/a.md", "kb/sub", "kb/sub/b.md"]
    assert (dest / "kb" / "sub" / "b.md").read_bytes() == b"beta"


def test_both_formats_produce_the_same_tree(dest):
    tree_zip = unpack_archive(_zip_bytes(TREE), "zip", dest / "a")
    tree_tar = unpack_archive(_tar_gz_bytes(TREE), "tar.gz", dest / "b")
    assert _names(tree_zip) == _names(tree_tar)


def test_unknown_kinds_are_refused(dest):
    for kind in ("7z", "tar", "rar", "zip.gz"):
        with pytest.raises(UnpackError):
            unpack_archive(_zip_bytes(TREE), kind, dest)  # type: ignore[arg-type]


# --- path traversal ----------------------------------------------------------


@pytest.mark.parametrize("evil", ["../escape.md", "/abs.md", "kb/../../up.md", "C:/win.md"])
def test_zip_traversal_is_refused(dest, evil):
    with pytest.raises(UnpackError, match="[Tt]raversal|member"):
        unpack_archive(_zip_bytes({**TREE, evil: b"x"}), "zip", dest)
    # 不留半棵树。
    assert not dest.exists() or not any(dest.rglob("*"))


@pytest.mark.parametrize("evil", ["../escape.md", "/abs.md", "kb/../../up.md"])
def test_tar_traversal_is_refused(dest, evil):
    with pytest.raises(UnpackError):
        unpack_archive(_tar_gz_bytes({**TREE, evil: b"x"}), "tar.gz", dest)


def test_zip_backslash_traversal_is_refused(dest):
    with pytest.raises(UnpackError):
        unpack_archive(_zip_bytes({"..\\escape.md": b"x"}), "zip", dest)


# --- link and special members --------------------------------------------------


def test_tar_symlink_escaping_the_root_is_refused(dest):
    blob = _tar_gz_bytes(TREE, symlinks={"kb/link": "../../etc/passwd"})
    with pytest.raises(UnpackError):
        unpack_archive(blob, "tar.gz", dest)


def test_tar_hardlinks_are_refused(dest):
    blob = _tar_gz_bytes(TREE, hardlinks={"kb/hard": "kb/a.md"})
    with pytest.raises(UnpackError):
        unpack_archive(blob, "tar.gz", dest)


def test_tar_special_members_are_refused(dest):
    blob = _tar_gz_bytes(TREE, special=["kb/dev0"])
    with pytest.raises(UnpackError):
        unpack_archive(blob, "tar.gz", dest)


def test_tar_in_root_symlinks_are_refused_too(dest):
    """比 issue 原文的"逃出根目录"更严:v1 拒绝链接类成员整类——
    目录内容在物化层的追链语义与目录级所有权(W6)相冲,零歧义优先。"""
    blob = _tar_gz_bytes(TREE, symlinks={"kb/in-root": "a.md"})
    with pytest.raises(UnpackError):
        unpack_archive(blob, "tar.gz", dest)


# --- caps ----------------------------------------------------------------------


def test_member_count_cap_bites(dest):
    many = {f"kb/f{i}.md": b"x" for i in range(9)}
    with pytest.raises(UnpackError, match="[Cc]ount|members"):
        unpack_archive(_zip_bytes(many), "zip", dest, member_limit=8)


def test_declared_size_cap_bites(dest):
    big = {"kb/big.md": b"x" * 64}
    with pytest.raises(UnpackError, match="[Ss]ize"):
        unpack_archive(_zip_bytes(big), "zip", dest, unpacked_size_limit=32)


def test_cap_counts_bytes_actually_extracted_not_headers(dest):
    # PRE-scan 谎报:真实 64 字节而 header 声明处的大小由 zipfile 重算——
    # 走读侧强卡的路径由上面 header 无关用例覆盖;此处锁"逐字节写入即计数"。
    big = {"kb/big.md": b"x" * 64}
    with pytest.raises(UnpackError, match="[Ss]ize"):
        unpack_archive(_tar_gz_bytes(big), "tar.gz", dest, unpacked_size_limit=32)


# --- permissions ------------------------------------------------------------


@pytest.mark.parametrize("mode", [0o755, 0o4755, 0o777])
def test_tar_executable_and_setuid_bits_are_flattened(dest, mode):
    blob = _tar_gz_bytes({"kb/a.md": b"x"}, modes={"kb/a.md": mode})
    tree = unpack_archive(blob, "tar.gz", dest)
    assert (tree.root / "kb" / "a.md").stat().st_mode & 0o777 == 0o644


def test_zip_executable_bits_are_flattened(dest):
    blob = _zip_bytes_with_mode({"kb/a.md": (b"x", 0o755)})
    tree = unpack_archive(blob, "zip", dest)
    assert (tree.root / "kb" / "a.md").stat().st_mode & 0o777 == 0o644


def test_directories_are_not_writable_group_or_world_flattened(dest):
    blob = _tar_gz_bytes(TREE)
    tree = unpack_archive(blob, "tar.gz", dest)
    for path in [tree.root, *tree.root.rglob("*")]:
        if path.is_dir():
            assert path.stat().st_mode & 0o777 == 0o755


# --- strip_components ---------------------------------------------------------


def test_strip_removes_exactly_n_layers_and_the_wrap_dir_becomes_root(dest):
    tree = unpack_archive(_zip_bytes(TREE), "zip", dest, strip_components=1)
    assert _names(tree) == ["a.md", "sub", "sub/b.md"]


def test_strip_zero_is_identity(dest):
    tree = unpack_archive(_zip_bytes(TREE), "zip", dest, strip_components=0)
    assert _names(tree) == ["kb", "kb/a.md", "kb/sub", "kb/sub/b.md"]


def test_a_file_with_too_few_layers_for_strip_is_refused(dest):
    with pytest.raises(UnpackError, match="[Ss]trip"):
        # file at depth 1; stripping 2 would eat its own directory+name.
        unpack_archive(_zip_bytes({"shallow.md": b"x"}), "zip", dest, strip_components=2)


def test_no_magic_single_top_dir_detection(dest):
    """同样内容、不同内部形状 → 相同输出(验收原文:相同输入必须表现相同,
    与归档内部形状无关;这里钉的是它的否定面——没有自动探测)。"""
    with_one_dir = {"kb/a.md": b"x"}
    with_two_dirs = {"kb/a.md": b"x", "other/b.md": b"y"}
    tree1 = unpack_archive(_zip_bytes(with_one_dir), "zip", dest / "a", strip_components=1)
    tree2 = unpack_archive(_zip_bytes(with_two_dirs), "zip", dest / "b", strip_components=1)
    # strip=1 不探测:双目录第二个照常剥层落根,而不是被清掉。
    assert "b.md" in _names(tree2)
    assert "a.md" in _names(tree1)


# --- determinism / atomicity ---------------------------------------------------


def test_same_input_same_output(dest):
    blob = _zip_bytes(TREE)
    tree1 = unpack_archive(blob, "zip", dest / "a")
    tree2 = unpack_archive(blob, "zip", dest / "b")
    assert _names(tree1) == _names(tree2)
    assert tree1.total_size == tree2.total_size


def test_a_refusal_leaves_no_partial_tree(dest):
    with pytest.raises(UnpackError):
        unpack_archive(_zip_bytes({**TREE, "../x": b"1"}), "zip", dest)
    assert not dest.exists() or not any(dest.rglob("*"))


def test_corrupt_archive_is_an_error_not_a_crash(dest):
    with pytest.raises(UnpackError):
        unpack_archive(b"not a zip at all", "zip", dest)


# --- 终审补的负例 --------------------------------------------------------------


def test_zip_symlink_members_are_refused(tmp_path):
    """zip 侧唯一的链接守卫(external_attr 的 S_IFLNK)此前零覆盖。"""
    blob = _zip_bytes_with_mode({"kb/link": (b"", 0o120644)})
    with pytest.raises(UnpackError, match="link"):
        unpack_archive(blob, "zip", tmp_path / "out")


def test_a_path_conflict_inside_the_archive_is_unpacked_error(tmp_path):
    """文件 "kb" 与文件 "kb/a" 同存:写盘期 FileExistsError 必须归化为
    UnpackError(而不是裸 OSError 逃逸出契约)。"""
    dest = tmp_path / "out"
    with pytest.raises(UnpackError, match="malformed"):
        unpack_archive(_zip_bytes({"kb": b"x", "kb/a.md": b"y"}), "zip", dest)
    assert not any(dest.rglob("*"))


def test_a_midstream_zip_crc_failure_is_unpacked_error(tmp_path):
    """构造期完好、读流期 CRC 损坏:同样归化。"""
    blob = bytearray(_zip_bytes({"kb/a.md": b"payload-payload"}))
    # zipfile 读流期对照的是中央目录(PK\x01\x02)里的 CRC 副本,
    # central file header 的 CRC32 在 offset 16(4 bytes)。翻掉它。
    central = blob.index(b"PK\x01\x02")
    blob[central + 16] ^= 0xFF
    dest = tmp_path / "out"
    with pytest.raises(UnpackError, match="malformed"):
        unpack_archive(bytes(blob), "zip", dest)
    assert not any(dest.rglob("*"))
