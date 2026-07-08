"""
测试 POST /api/skills/symlink/clean 接口
"""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.skills import router
from engine.community.api.tests._skills_fixtures import install_skills_manager


@pytest.fixture
def client(tmp_path):
    yield from install_skills_manager(router, tmp_path)


class TestCleanSymlinks:
    """测试清理指定目录下所有软链"""

    def test_clean_symlinks(self, client: TestClient, tmp_path: Path):
        """正常清理目录下的软链"""
        source = tmp_path / "real_dir"
        source.mkdir()
        link_dir = tmp_path / "links"
        link_dir.mkdir()

        link_a = link_dir / "link_a"
        link_b = link_dir / "link_b"
        link_a.symlink_to(source)
        link_b.symlink_to(source)

        resp = client.post("/api/skills/symlink/clean", json={
            "directories": [str(link_dir)],
        })

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["directories_scanned"] == 1
        assert len(data["removed"]) == 2
        assert str(link_a) in data["removed"]
        assert str(link_b) in data["removed"]
        assert not link_a.exists()
        assert not link_b.exists()

    def test_only_removes_symlinks_not_files(self, client: TestClient, tmp_path: Path):
        """只删除软链，不删除普通文件和目录"""
        source = tmp_path / "real_dir"
        source.mkdir()
        target_dir = tmp_path / "mixed"
        target_dir.mkdir()

        # 普通文件
        regular_file = target_dir / "regular.txt"
        regular_file.write_text("keep me")
        # 子目录
        sub_dir = target_dir / "subdir"
        sub_dir.mkdir()
        # 软链
        link = target_dir / "link"
        link.symlink_to(source)

        resp = client.post("/api/skills/symlink/clean", json={
            "directories": [str(target_dir)],
        })

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["removed"]) == 1
        assert str(link) in data["removed"]
        assert regular_file.exists()
        assert sub_dir.is_dir()

    def test_nonexistent_directory_skipped(self, client: TestClient, tmp_path: Path):
        """不存在的目录跳过，不报错"""
        fake_dir = tmp_path / "nonexistent"

        resp = client.post("/api/skills/symlink/clean", json={
            "directories": [str(fake_dir)],
        })

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["directories_scanned"] == 0
        assert data["removed"] == []

    def test_multiple_directories(self, client: TestClient, tmp_path: Path):
        """多个目录同时清理"""
        source = tmp_path / "src"
        source.mkdir()

        dir_a = tmp_path / "dir_a"
        dir_a.mkdir()
        link_a = dir_a / "link"
        link_a.symlink_to(source)

        dir_b = tmp_path / "dir_b"
        dir_b.mkdir()
        link_b = dir_b / "link"
        link_b.symlink_to(source)

        resp = client.post("/api/skills/symlink/clean", json={
            "directories": [str(dir_a), str(dir_b)],
        })

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["directories_scanned"] == 2
        assert len(data["removed"]) == 2

    def test_empty_directory(self, client: TestClient, tmp_path: Path):
        """空目录，无软链可清理"""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        resp = client.post("/api/skills/symlink/clean", json={
            "directories": [str(empty_dir)],
        })

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["directories_scanned"] == 1
        assert data["removed"] == []

    def test_empty_directories_list_rejected(self, client: TestClient):
        """空列表应被拒绝"""
        resp = client.post("/api/skills/symlink/clean", json={
            "directories": [],
        })
        assert resp.status_code == 400

    def test_relative_path_rejected(self, client: TestClient):
        """相对路径应被拒绝"""
        resp = client.post("/api/skills/symlink/clean", json={
            "directories": ["relative/path"],
        })
        assert resp.status_code == 400

    def test_dotdot_path_rejected(self, client: TestClient):
        """包含 .. 的路径应被拒绝"""
        resp = client.post("/api/skills/symlink/clean", json={
            "directories": ["/tmp/../etc"],
        })
        assert resp.status_code == 400

    def test_file_path_skipped(self, client: TestClient, tmp_path: Path):
        """路径指向文件而非目录时跳过"""
        a_file = tmp_path / "a_file.txt"
        a_file.write_text("I am a file")

        resp = client.post("/api/skills/symlink/clean", json={
            "directories": [str(a_file)],
        })

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["directories_scanned"] == 0
        assert a_file.exists()

    def test_mixed_existing_and_nonexisting(self, client: TestClient, tmp_path: Path):
        """混合存在和不存在的目录"""
        source = tmp_path / "src"
        source.mkdir()

        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link = real_dir / "link"
        link.symlink_to(source)

        fake_dir = tmp_path / "fake"

        resp = client.post("/api/skills/symlink/clean", json={
            "directories": [str(real_dir), str(fake_dir)],
        })

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["directories_scanned"] == 1
        assert len(data["removed"]) == 1
        assert not link.exists()
