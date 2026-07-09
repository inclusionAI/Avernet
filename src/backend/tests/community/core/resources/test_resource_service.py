"""Tests for core ResourceService — mocks ResourceRepositoryProtocol at the boundary."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.resources.models import ResourceType
from agentclaw.community.core.resources.service import DuplicateResourceError, ResourceService
from agentclaw.community.core.resources.services.file_service import FileService


def _mock_repo() -> MagicMock:
    repo = MagicMock()
    repo.list_resources.return_value = []
    repo.count_resources.return_value = 0
    repo.create.return_value = {"id": 99}
    return repo


def _make_service(repo=None, bot_id="bot1") -> ResourceService:
    if repo is None:
        repo = _mock_repo()
    return ResourceService(bot_id=bot_id, repository=repo)


# ---------------------------------------------------------------------------
# check_name_exists
# ---------------------------------------------------------------------------


class TestCheckNameExists:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_resources(self):
        svc = _make_service()
        result = await svc.check_name_exists(
            name="new-resource",
            resource_type=ResourceType.URL,
            parent_path=None,
            user_id=None,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_name_matches(self):
        repo = _mock_repo()
        repo.list_resources.return_value = [{"id": 1, "name": "existing"}]
        svc = _make_service(repo)
        result = await svc.check_name_exists(
            name="existing",
            resource_type=ResourceType.URL,
            parent_path=None,
            user_id=None,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_excludes_own_id(self):
        repo = _mock_repo()
        repo.list_resources.return_value = [{"id": 5, "name": "existing"}]
        svc = _make_service(repo)
        result = await svc.check_name_exists(
            name="existing",
            resource_type=ResourceType.URL,
            parent_path=None,
            user_id=None,
            exclude_id="5",
        )
        assert result is False


# ---------------------------------------------------------------------------
# list_resources
# ---------------------------------------------------------------------------


class TestListResources:
    def test_empty_repo_returns_empty_list(self):
        svc = _make_service()
        result = svc.list_resources()
        assert result == []

    def test_maps_dicts_to_resource_objects(self):
        repo = _mock_repo()
        stored = {
            "id": 1,
            "name": "api",
            "resource_type": "url",
            "status": "active",
            "attributes": {"url": "http://x.com", "method": "GET"},
        }
        repo.list_resources.return_value = [stored]
        svc = _make_service(repo)
        resources = svc.list_resources()
        assert len(resources) == 1
        assert resources[0].name == "api"
        assert resources[0].resource_type == ResourceType.URL

    def test_limit_and_offset_applied(self):
        repo = _mock_repo()
        items = [{"id": i, "name": f"r{i}", "resource_type": "url", "status": "active", "attributes": {}} for i in range(5)]
        repo.list_resources.return_value = items
        svc = _make_service(repo)
        result = svc.list_resources(limit=2, offset=1)
        assert len(result) == 2
        assert result[0].name == "r1"

    def test_passes_resource_type_filter(self):
        repo = _mock_repo()
        svc = _make_service(repo)
        svc.list_resources(resource_type=ResourceType.FILE)
        repo.list_resources.assert_called_once()
        call_kwargs = repo.list_resources.call_args[1]
        assert call_kwargs["resource_type"] == "file"

    def test_none_resource_type_passes_none(self):
        repo = _mock_repo()
        svc = _make_service(repo)
        svc.list_resources()
        call_kwargs = repo.list_resources.call_args[1]
        assert call_kwargs["resource_type"] is None


# ---------------------------------------------------------------------------
# count_children
# ---------------------------------------------------------------------------


class TestCountChildren:
    def test_delegates_to_repo(self):
        repo = _mock_repo()
        repo.count_resources.return_value = 7
        svc = _make_service(repo)
        assert svc.count_children("/some/path") == 7
        repo.count_resources.assert_called_once_with(parent_path="/some/path", bolt_id="bot1")


# ---------------------------------------------------------------------------
# create_url_resource
# ---------------------------------------------------------------------------


class TestCreateUrlResource:
    @pytest.mark.asyncio
    async def test_creates_and_returns_resource(self):
        repo = _mock_repo()
        repo.create.return_value = {"id": 42}
        svc = _make_service(repo)
        resource = await svc.create_url_resource(name="api", url="http://example.com")
        assert resource.id == 42
        assert resource.name == "api"
        assert resource.resource_type == ResourceType.URL
        repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_duplicate_when_name_exists(self):
        repo = _mock_repo()
        repo.list_resources.return_value = [{"id": 1, "name": "api"}]
        svc = _make_service(repo)
        with pytest.raises(DuplicateResourceError, match="api"):
            await svc.create_url_resource(name="api", url="http://example.com")

    @pytest.mark.asyncio
    async def test_creates_with_all_fields(self):
        repo = _mock_repo()
        repo.create.return_value = {"id": 10}
        svc = _make_service(repo)
        _resource = await svc.create_url_resource(
            name="api",
            url="http://api.example.com",
            method="POST",
            headers={"X-Key": "val"},
            parent_path="/apis",
            user_id="user1",
            created_by="user1",
        )
        stored_dict = repo.create.call_args[0][0]
        assert stored_dict["attributes"]["method"] == "POST"
        assert stored_dict["attributes"]["headers"] == {"X-Key": "val"}


# ---------------------------------------------------------------------------
# create_node_resource
# ---------------------------------------------------------------------------


class TestCreateNodeResource:
    @pytest.mark.asyncio
    async def test_creates_and_returns_node_resource(self):
        repo = _mock_repo()
        repo.create.return_value = {"id": 55}
        svc = _make_service(repo)
        resource = await svc.create_node_resource(
            name="my-node", node_address="10.0.0.1:8080"
        )
        assert resource.id == 55
        assert resource.resource_type == ResourceType.NODE

    @pytest.mark.asyncio
    async def test_raises_duplicate_when_name_exists(self):
        repo = _mock_repo()
        repo.list_resources.return_value = [{"id": 1, "name": "my-node"}]
        svc = _make_service(repo)
        with pytest.raises(DuplicateResourceError, match="my-node"):
            await svc.create_node_resource(name="my-node", node_address="10.0.0.1:8080")

    @pytest.mark.asyncio
    async def test_path_alias_defaults_to_name(self):
        repo = _mock_repo()
        repo.create.return_value = {"id": 1}
        svc = _make_service(repo)
        await svc.create_node_resource(name="mynode", node_address="10.0.0.1:80")
        stored_dict = repo.create.call_args[0][0]
        assert stored_dict["attributes"]["path_alias"] == "mynode"


# ---------------------------------------------------------------------------
# check_link_url_exists
# ---------------------------------------------------------------------------


class TestCheckLinkUrlExists:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_resources(self):
        svc = _make_service()
        result = await svc.check_link_url_exists(url="https://example.com", user_id=None)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_url_matches(self):
        repo = _mock_repo()
        repo.list_resources.return_value = [
            {"id": 1, "name": "my-link", "attributes": {"url": "https://example.com"}},
        ]
        svc = _make_service(repo)
        result = await svc.check_link_url_exists(url="https://example.com", user_id=None)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_url_differs(self):
        repo = _mock_repo()
        repo.list_resources.return_value = [
            {"id": 1, "name": "my-link", "attributes": {"url": "https://other.com"}},
        ]
        svc = _make_service(repo)
        result = await svc.check_link_url_exists(url="https://example.com", user_id=None)
        assert result is False

    @pytest.mark.asyncio
    async def test_excludes_own_id(self):
        repo = _mock_repo()
        repo.list_resources.return_value = [
            {"id": 5, "name": "my-link", "attributes": {"url": "https://example.com"}},
        ]
        svc = _make_service(repo)
        result = await svc.check_link_url_exists(
            url="https://example.com", user_id=None, exclude_id="5"
        )
        assert result is False


# ---------------------------------------------------------------------------
# create_link_resource
# ---------------------------------------------------------------------------


class TestCreateLinkResource:
    @pytest.mark.asyncio
    async def test_creates_and_returns_resource(self):
        repo = _mock_repo()
        repo.create.return_value = {"id": 10}
        svc = _make_service(repo)
        resource = await svc.create_link_resource(
            name="yuque-doc", url="https://yuque.com/doc/1", link_type="yuque"
        )
        assert resource.id == 10
        assert resource.resource_type == ResourceType.LINK
        repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_duplicate_when_url_exists(self):
        repo = _mock_repo()
        repo.list_resources.return_value = [
            {"id": 1, "name": "old-name", "attributes": {"url": "https://yuque.com/doc/1"}},
        ]
        svc = _make_service(repo)
        with pytest.raises(DuplicateResourceError, match="https://yuque.com/doc/1"):
            await svc.create_link_resource(
                name="new-name", url="https://yuque.com/doc/1", link_type="yuque"
            )

    @pytest.mark.asyncio
    async def test_allows_same_name_different_url(self):
        repo = _mock_repo()
        repo.list_resources.return_value = [
            {"id": 1, "name": "same-name", "attributes": {"url": "https://yuque.com/doc/1"}},
        ]
        repo.create.return_value = {"id": 2}
        svc = _make_service(repo)
        resource = await svc.create_link_resource(
            name="same-name", url="https://yuque.com/doc/2", link_type="yuque"
        )
        assert resource.id == 2


# ---------------------------------------------------------------------------
# FileService P0 — device_fs injection (transitional bridge for non-arca path)
# ---------------------------------------------------------------------------


def _fake_device_fs() -> MagicMock:
    fs = MagicMock()
    fs.list_dir = AsyncMock(return_value=[])
    fs.write_file = AsyncMock(return_value=None)
    fs.read_file = AsyncMock(return_value=b"hello")
    fs.delete_tree = AsyncMock(return_value=True)
    fs.exists = AsyncMock(return_value=True)
    return fs


class TestFileServiceDeviceFsInjection:
    """The 4 P0 methods route the local else-branch through device_fs when given."""

    @pytest.mark.asyncio
    async def test_list_flat_uses_device_fs(self, tmp_path):
        svc = FileService(data_dir=tmp_path)
        fs = _fake_device_fs()
        fs.list_dir = AsyncMock(return_value=[
            {"name": "a.txt", "path": str(tmp_path / "a.txt"), "is_dir": False, "relative_path": "a.txt"},
        ])
        items = await svc.list_flat("", device_fs=fs)
        fs.list_dir.assert_awaited_once()
        assert any(i["name"] == "a.txt" for i in items)

    @pytest.mark.asyncio
    async def test_list_files_uses_device_fs(self, tmp_path):
        svc = FileService(data_dir=tmp_path)
        fs = _fake_device_fs()
        fs.list_dir = AsyncMock(return_value=[])
        await svc.list_files("", device_fs=fs)
        fs.list_dir.assert_awaited()

    @pytest.mark.asyncio
    async def test_upload_file_uses_device_fs(self, tmp_path):
        svc = FileService(data_dir=tmp_path)
        fs = _fake_device_fs()
        result = await svc.upload_file(
            data=b"payload", filename="note.txt", device_fs=fs,
        )
        fs.write_file.assert_awaited_once()
        assert result["name"] == "note.txt"
        # bare FS must NOT be touched when fs is provided
        assert not (tmp_path / "note.txt").exists()

    @pytest.mark.asyncio
    async def test_delete_item_uses_device_fs(self, tmp_path):
        svc = FileService(data_dir=tmp_path)
        fs = _fake_device_fs()
        ok = await svc.delete_item("sub", device_fs=fs)
        fs.delete_tree.assert_awaited_once()
        assert ok is True

    @pytest.mark.asyncio
    async def test_get_file_path_reads_via_device_fs(self, tmp_path):
        svc = FileService(data_dir=tmp_path)
        fs = _fake_device_fs()
        fs.read_file = AsyncMock(return_value=b"hello")
        data = await svc.get_file_path("a.txt", device_fs=fs)
        fs.read_file.assert_awaited_once()
        assert data == b"hello"


class TestFileServiceBareFsFallback:
    """Without device_fs the methods keep the original bare-FS behaviour."""

    @pytest.mark.asyncio
    async def test_upload_then_list_then_get_then_delete(self, tmp_path):
        from pathlib import Path

        svc = FileService(data_dir=tmp_path)

        # upload_file now requires device_fs — use one that delegates to the real FS
        fs = MagicMock()
        async def _real_write(path_, data_):
            Path(path_).parent.mkdir(parents=True, exist_ok=True)
            Path(path_).write_bytes(data_)
        fs.write_file = _real_write
        fs.list_dir = AsyncMock(return_value=[])
        fs.read_file = AsyncMock(return_value=b"hello")
        fs.delete_tree = AsyncMock(return_value=True)

        res = await svc.upload_file(data=b"hi", filename="x.txt", device_fs=fs)
        assert (tmp_path / "x.txt").exists()
        assert res["name"] == "x.txt"

        flat = await svc.list_flat("")
        assert any(i["name"] == "x.txt" for i in flat)

        p = await svc.get_file_path("x.txt")
        from pathlib import Path
        assert isinstance(p, Path) and p.exists()

        assert await svc.delete_item("x.txt") is True
        assert not (tmp_path / "x.txt").exists()


# ---------------------------------------------------------------------------
# FileService — prod=arca 回归护栏（device_provider='arca' 路径零改动）
# ---------------------------------------------------------------------------


class TestFileServiceArcaPath:
    """FileService now routes all uploads through device_fs (which wraps Arca or
    local FS). These tests verify the Arca-indirect path via a remote-style mock.
    """

    @pytest.mark.asyncio
    async def test_upload_file_device_fs_write_file_called(self, tmp_path):
        """upload_file calls device_fs.write_file with the resolved path and data."""
        svc = FileService(data_dir=tmp_path)
        fs = _fake_device_fs()
        result = await svc.upload_file(
            data=b"prod-payload",
            filename="report.txt",
            device_fs=fs,
        )
        # write_file was called with the full resolved path
        expected_path = str(tmp_path / "report.txt")
        fs.write_file.assert_awaited_once_with(expected_path, b"prod-payload")
        assert result["name"] == "report.txt"

    @pytest.mark.asyncio
    async def test_upload_file_remote_fs_uses_byte_count_when_no_local_fs(self, tmp_path):
        """When device_fs writes to a remote FS (no local file), upload_file falls
        back to byte count for size metadata — matching the prior arca path behaviour."""
        svc = FileService(data_dir=tmp_path)
        fs = _fake_device_fs()
        # Simulate remote FS: write succeeds but file doesn't land on local disk
        result = await svc.upload_file(
            data=b"prod-payload",
            filename="report.txt",
            device_fs=fs,
        )
        fs.write_file.assert_awaited_once()
        assert result["size"] == len(b"prod-payload")
        # Remote write must not create a local file
        assert not (tmp_path / "report.txt").exists()

    @pytest.mark.asyncio
    async def test_upload_file_does_not_write_to_bare_fs_when_device_fs_given(self, tmp_path):
        """upload_file must NOT write to local disk when device_fs is provided
        (it delegates exclusively through device_fs.write_file)."""
        svc = FileService(data_dir=tmp_path)
        fs = _fake_device_fs()
        await svc.upload_file(
            data=b"prod-payload",
            filename="report.txt",
            device_fs=fs,
        )
        fs.write_file.assert_awaited_once()
        assert not (tmp_path / "report.txt").exists()


