"""
测试 /api/skills/symlink/bindpath 接口
"""
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine.community.api.skills import router
from engine.community.api.tests._skills_fixtures import install_skills_manager


@pytest.fixture
def client(tmp_path):
    yield from install_skills_manager(router, tmp_path)


class TestBindPathSymlink:
    """测试绝对路径软链同步"""

    def test_create_symlink(self, client: TestClient, tmp_path: Path):
        """首次创建软链"""
        source = tmp_path / "src_dir"
        source.mkdir()
        target = tmp_path / "links" / "my_link"

        resp = client.post("/api/skills/symlink/bindpath", json={
            "symlinks": [{"source": str(source), "target": str(target)}],
            "clean_target_dir": False,
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert str(target) in data["data"]["created"]
        assert target.is_symlink()
        assert target.resolve() == source.resolve()

    def test_idempotent_second_call(self, client: TestClient, tmp_path: Path):
        """相同参数第二次调用应该 kept，不能报错"""
        source = tmp_path / "src_dir"
        source.mkdir()
        target = tmp_path / "links" / "my_link"

        payload = {
            "symlinks": [{"source": str(source), "target": str(target)}],
            "clean_target_dir": False,
        }

        # 第一次
        resp1 = client.post("/api/skills/symlink/bindpath", json=payload)
        assert resp1.status_code == 200
        assert str(target) in resp1.json()["data"]["created"]

        # 第二次 —— 同样参数
        resp2 = client.post("/api/skills/symlink/bindpath", json=payload)
        assert resp2.status_code == 200
        data2 = resp2.json()["data"]
        assert str(target) in data2["kept"]
        assert data2["created"] == []
        assert data2["updated"] == []

    def test_update_symlink_dest(self, client: TestClient, tmp_path: Path):
        """source 变更后应该 updated"""
        source_a = tmp_path / "dir_a"
        source_a.mkdir()
        source_b = tmp_path / "dir_b"
        source_b.mkdir()
        target = tmp_path / "links" / "my_link"

        # 创建指向 a
        resp1 = client.post("/api/skills/symlink/bindpath", json={
            "symlinks": [{"source": str(source_a), "target": str(target)}],
            "clean_target_dir": False,
        })
        assert resp1.status_code == 200

        # 改为指向 b
        resp2 = client.post("/api/skills/symlink/bindpath", json={
            "symlinks": [{"source": str(source_b), "target": str(target)}],
            "clean_target_dir": False,
        })
        assert resp2.status_code == 200
        data2 = resp2.json()["data"]
        assert str(target) in data2["updated"]
        assert os.readlink(str(target)) == str(source_b)

    def test_clean_target_dir(self, client: TestClient, tmp_path: Path):
        """clean_target_dir=true 时清理同目录下未列出的软链"""
        source_a = tmp_path / "dir_a"
        source_a.mkdir()
        source_b = tmp_path / "dir_b"
        source_b.mkdir()
        link_dir = tmp_path / "links"
        link_dir.mkdir()

        # 手动创建一个"多余"的软链
        extra_link = link_dir / "extra"
        extra_link.symlink_to(source_b)

        target = link_dir / "my_link"
        resp = client.post("/api/skills/symlink/bindpath", json={
            "symlinks": [{"source": str(source_a), "target": str(target)}],
            "clean_target_dir": True,
        })

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert str(target) in data["created"]
        assert str(extra_link) in data["removed"]
        assert not extra_link.exists()

    def test_clean_target_dir_false_keeps_extra(self, client: TestClient, tmp_path: Path):
        """clean_target_dir=false 时不清理其他软链"""
        source = tmp_path / "dir_a"
        source.mkdir()
        link_dir = tmp_path / "links"
        link_dir.mkdir()

        extra_link = link_dir / "extra"
        extra_link.symlink_to(source)

        target = link_dir / "my_link"
        resp = client.post("/api/skills/symlink/bindpath", json={
            "symlinks": [{"source": str(source), "target": str(target)}],
            "clean_target_dir": False,
        })

        assert resp.status_code == 200
        assert extra_link.is_symlink()  # 仍然存在

    def test_clean_target_dir_preserves_layout_bridges(
        self, client: TestClient, tmp_path: Path
    ):
        """全量 mapping 清理不能删除 skills-local / skills-repo 结构桥。"""
        source = tmp_path / "pool" / "skill-a"
        source.mkdir(parents=True)
        link_dir = tmp_path / "skills"
        pool_local = tmp_path / "pool" / "skills-local"
        pool_repo = tmp_path / "pool" / "skills-repo"
        pool_local.mkdir()
        pool_repo.mkdir()
        link_dir.mkdir()
        local_bridge = link_dir / "skills-local"
        repo_bridge = link_dir / "skills-repo"
        local_bridge.symlink_to(pool_local, target_is_directory=True)
        repo_bridge.symlink_to(pool_repo, target_is_directory=True)

        response = client.post(
            "/api/skills/symlink/bindpath",
            json={
                "symlinks": [
                    {
                        "source": str(source),
                        "target": str(link_dir / "skill-a"),
                    }
                ],
                "clean_target_dir": True,
            },
        )

        assert response.status_code == 200
        assert local_bridge.is_symlink()
        assert repo_bridge.is_symlink()

    def test_target_occupied_by_real_file(self, client: TestClient, tmp_path: Path):
        """target 被真实文件占用时应返回 409"""
        source = tmp_path / "dir_a"
        source.mkdir()
        target = tmp_path / "links" / "my_link"
        target.parent.mkdir(parents=True)
        target.write_text("I am a real file")

        resp = client.post("/api/skills/symlink/bindpath", json={
            "symlinks": [{"source": str(source), "target": str(target)}],
            "clean_target_dir": False,
        })
        assert resp.status_code == 409

    def test_relative_path_rejected(self, client: TestClient):
        """相对路径应该被拒绝"""
        resp = client.post("/api/skills/symlink/bindpath", json={
            "symlinks": [{"source": "relative/path", "target": "/tmp/link"}],
        })
        assert resp.status_code == 400

        resp = client.post("/api/skills/symlink/bindpath", json={
            "symlinks": [{"source": "/tmp/src", "target": "relative/path"}],
        })
        assert resp.status_code == 400

    def test_dotdot_rejected(self, client: TestClient):
        """包含 .. 的路径应该被拒绝"""
        resp = client.post("/api/skills/symlink/bindpath", json={
            "symlinks": [{"source": "/tmp/../etc/passwd", "target": "/tmp/link"}],
        })
        assert resp.status_code == 400

    def test_duplicate_target_rejected(self, client: TestClient, tmp_path: Path):
        """重复 target 应该被拒绝"""
        source = tmp_path / "dir_a"
        source.mkdir()
        target = str(tmp_path / "links" / "my_link")

        resp = client.post("/api/skills/symlink/bindpath", json={
            "symlinks": [
                {"source": str(source), "target": target},
                {"source": str(source), "target": target},
            ],
        })
        assert resp.status_code == 400

    def test_directory_symlink_idempotent(self, client: TestClient, tmp_path: Path):
        """目录软链场景：反复调用应保持幂等"""
        source = tmp_path / "workspace" / "skills" / "repo" / "aix-cui"
        source.mkdir(parents=True)
        (source / "main.py").write_text("print('hello')")

        target = tmp_path / "workspace" / "skills" / "aix-cui"

        payload = {
            "symlinks": [{"source": str(source), "target": str(target)}],
            "clean_target_dir": True,
        }

        # 第一次
        resp1 = client.post("/api/skills/symlink/bindpath", json=payload)
        assert resp1.status_code == 200
        assert str(target) in resp1.json()["data"]["created"]
        # 通过软链能访问到内容
        assert (target / "main.py").read_text() == "print('hello')"

        # 第二次
        resp2 = client.post("/api/skills/symlink/bindpath", json=payload)
        assert resp2.status_code == 200
        data2 = resp2.json()["data"]
        assert str(target) in data2["kept"]
        assert data2["created"] == []
        assert data2["updated"] == []

        # 第三次
        resp3 = client.post("/api/skills/symlink/bindpath", json=payload)
        assert resp3.status_code == 200
        assert str(target) in resp3.json()["data"]["kept"]
