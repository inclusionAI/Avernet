"""Integration tests for LocalDeviceFileSystem (pathlib-based DeviceFileSystemPlugin).

Uses real filesystem via tmp_path — no mocks.
"""
import pytest

from agentclaw.community.core.devices.services.local_device_filesystem import LocalDeviceFileSystem


@pytest.fixture
def fs():
    return LocalDeviceFileSystem()


# ── read_file ──────────────────────────────────────────────────────────


@pytest.mark.integration
class TestReadFile:
    @pytest.mark.asyncio
    async def test_read_existing_file(self, fs, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_bytes(b"hello world")
        assert await fs.read_file(str(f)) == b"hello world"

    @pytest.mark.asyncio
    async def test_read_nonexistent_returns_none(self, fs, tmp_path):
        assert await fs.read_file(str(tmp_path / "nope.txt")) is None

    @pytest.mark.asyncio
    async def test_read_directory_returns_none(self, fs, tmp_path):
        d = tmp_path / "subdir"
        d.mkdir()
        assert await fs.read_file(str(d)) is None

    @pytest.mark.asyncio
    async def test_read_binary_content(self, fs, tmp_path):
        f = tmp_path / "bin.dat"
        data = bytes(range(256))
        f.write_bytes(data)
        assert await fs.read_file(str(f)) == data


# ── write_file ─────────────────────────────────────────────────────────


@pytest.mark.integration
class TestWriteFile:
    @pytest.mark.asyncio
    async def test_write_creates_file(self, fs, tmp_path):
        f = tmp_path / "out.txt"
        await fs.write_file(str(f), b"content")
        assert f.read_bytes() == b"content"

    @pytest.mark.asyncio
    async def test_write_creates_parent_dirs(self, fs, tmp_path):
        f = tmp_path / "a" / "b" / "c" / "deep.txt"
        await fs.write_file(str(f), b"deep")
        assert f.read_bytes() == b"deep"

    @pytest.mark.asyncio
    async def test_write_overwrites_existing(self, fs, tmp_path):
        f = tmp_path / "exist.txt"
        f.write_bytes(b"old")
        await fs.write_file(str(f), b"new")
        assert f.read_bytes() == b"new"

    @pytest.mark.asyncio
    async def test_write_empty_content(self, fs, tmp_path):
        f = tmp_path / "empty.txt"
        await fs.write_file(str(f), b"")
        assert f.read_bytes() == b""


# ── delete_tree ────────────────────────────────────────────────────────


@pytest.mark.integration
class TestDeleteTree:
    @pytest.mark.asyncio
    async def test_delete_existing_tree(self, fs, tmp_path):
        d = tmp_path / "target"
        d.mkdir()
        (d / "file.txt").write_text("x")
        (d / "sub").mkdir()
        (d / "sub" / "nested.txt").write_text("y")

        assert await fs.delete_tree(str(d)) is True
        assert not d.exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_true(self, fs, tmp_path):
        assert await fs.delete_tree(str(tmp_path / "nope")) is True

    @pytest.mark.asyncio
    async def test_delete_does_not_affect_siblings(self, fs, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        (sibling / "keep.txt").write_text("keep")

        await fs.delete_tree(str(target))
        assert sibling.exists()
        assert (sibling / "keep.txt").read_text() == "keep"


# ── delete_file ────────────────────────────────────────────────────────


@pytest.mark.integration
class TestDeleteFile:
    @pytest.mark.asyncio
    async def test_delete_existing_file(self, fs, tmp_path):
        f = tmp_path / "gone.txt"
        f.write_text("x")
        assert await fs.delete_file(str(f)) is True
        assert not f.exists()

    @pytest.mark.asyncio
    async def test_delete_absent_file_is_idempotent(self, fs, tmp_path):
        assert await fs.delete_file(str(tmp_path / "never.txt")) is True

    @pytest.mark.asyncio
    async def test_delete_file_does_not_affect_siblings(self, fs, tmp_path):
        target = tmp_path / "target.txt"
        target.write_text("t")
        sibling = tmp_path / "keep.txt"
        sibling.write_text("keep")

        await fs.delete_file(str(target))
        assert not target.exists()
        assert sibling.read_text() == "keep"

    @pytest.mark.asyncio
    async def test_delete_file_removes_directory_recursively(self, fs, tmp_path):
        # delete_file targets files AND dirs (the resources delete endpoint deletes
        # both); a real directory is removed recursively.
        d = tmp_path / "tree"
        (d / "sub").mkdir(parents=True)
        (d / "sub" / "f.txt").write_text("x")
        assert await fs.delete_file(str(d)) is True
        assert not d.exists()

    @pytest.mark.asyncio
    async def test_delete_file_returns_false_on_oserror(self, fs, tmp_path, monkeypatch):
        d = tmp_path / "tree"
        d.mkdir()

        def _boom(*_a, **_k):
            raise OSError("rmtree failed")

        monkeypatch.setattr(
            "agentclaw.community.core.devices.services.local_device_filesystem.shutil.rmtree", _boom
        )
        assert await fs.delete_file(str(d)) is False


# ── list_dir ───────────────────────────────────────────────────────────


@pytest.mark.integration
class TestListDir:
    @pytest.mark.asyncio
    async def test_list_empty_dir(self, fs, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        assert await fs.list_dir(str(d)) == []

    @pytest.mark.asyncio
    async def test_list_nonexistent_returns_none(self, fs, tmp_path):
        assert await fs.list_dir(str(tmp_path / "nope")) is None

    @pytest.mark.asyncio
    async def test_list_flat_entries(self, fs, tmp_path):
        d = tmp_path / "flat"
        d.mkdir()
        (d / "a.txt").write_text("a")
        (d / "b.txt").write_text("b")
        (d / "sub").mkdir()

        result = await fs.list_dir(str(d))
        names = sorted(e["name"] for e in result)
        assert names == ["a.txt", "b.txt", "sub"]

    @pytest.mark.asyncio
    async def test_list_entry_has_required_keys(self, fs, tmp_path):
        d = tmp_path / "check"
        d.mkdir()
        (d / "file.txt").write_text("x")

        result = await fs.list_dir(str(d))
        entry = result[0]
        assert "name" in entry
        assert "path" in entry
        assert "is_dir" in entry
        assert "relative_path" in entry
        assert entry["is_dir"] is False

    @pytest.mark.asyncio
    async def test_list_recursive_includes_nested(self, fs, tmp_path):
        d = tmp_path / "tree"
        d.mkdir()
        (d / "top.txt").write_text("t")
        (d / "sub").mkdir()
        (d / "sub" / "deep.txt").write_text("d")

        result = await fs.list_dir(str(d), recursive=True)
        rel_paths = sorted(e["relative_path"] for e in result)
        assert "top.txt" in rel_paths
        assert "sub" in rel_paths
        assert any("deep.txt" in rp for rp in rel_paths)

    @pytest.mark.asyncio
    async def test_list_non_recursive_does_not_descend(self, fs, tmp_path):
        d = tmp_path / "shallow"
        d.mkdir()
        (d / "sub").mkdir()
        (d / "sub" / "hidden.txt").write_text("h")

        result = await fs.list_dir(str(d), recursive=False)
        names = [e["name"] for e in result]
        assert "sub" in names
        assert "hidden.txt" not in names


# ── exists ─────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestExists:
    @pytest.mark.asyncio
    async def test_existing_file(self, fs, tmp_path):
        f = tmp_path / "yes.txt"
        f.write_text("y")
        assert await fs.exists(str(f)) is True

    @pytest.mark.asyncio
    async def test_existing_dir(self, fs, tmp_path):
        d = tmp_path / "dir"
        d.mkdir()
        assert await fs.exists(str(d)) is True

    @pytest.mark.asyncio
    async def test_nonexistent(self, fs, tmp_path):
        assert await fs.exists(str(tmp_path / "nope")) is False
