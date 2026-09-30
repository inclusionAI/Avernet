"""
测试 Bot 配置 API
"""
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from engine.community.api.bot import router
from engine.community.shared.credentials import CredentialsService


@pytest.fixture
def temp_credentials_file(tmp_path: Path) -> Path:
    """创建临时凭证文件"""
    return tmp_path / ".credentials"


@pytest.fixture
def credentials_service(temp_credentials_file: Path, monkeypatch):
    """创建使用临时文件的 CredentialsService 实例"""
    # 重置单例
    CredentialsService.reset()

    # 通过环境变量指定临时文件路径
    monkeypatch.setenv("CREDENTIALS_PATH", str(temp_credentials_file))

    # 创建实例
    service = CredentialsService.get_instance()
    yield service

    # 清理
    CredentialsService.reset()


@pytest.fixture
def client(credentials_service):
    """创建测试客户端"""
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestBotConfigAPI:
    """测试 Bot 配置 API"""

    def test_update_role_only(self, client: TestClient, temp_credentials_file: Path):
        """测试只更新 role"""
        # 创建初始文件
        temp_credentials_file.write_text("TOKEN=abc\n")

        response = client.post("/api/bot/config", json={"role": "OWNER"})

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["role"] == "OWNER"
        assert data["message"] == "更新成功"

    def test_update_visibility_only(self, client: TestClient, temp_credentials_file: Path):
        """测试只更新 visibility"""
        temp_credentials_file.write_text("TOKEN=abc\n")

        response = client.post("/api/bot/config", json={"visibility": "PUBLIC"})

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["visibility"] == "PUBLIC"

    def test_update_both_fields(self, client: TestClient, temp_credentials_file: Path):
        """测试同时更新 role 和 visibility"""
        temp_credentials_file.write_text("TOKEN=abc\n")

        response = client.post("/api/bot/config", json={
            "role": "CALLER",
            "visibility": "PRIVATE"
        })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["role"] == "CALLER"
        assert data["data"]["visibility"] == "PRIVATE"

    def test_update_role_replaces_existing(self, client: TestClient, temp_credentials_file: Path):
        """测试更新已有 role 值"""
        temp_credentials_file.write_text("ROLE=OWNER\nVISIBILITY=PUBLIC\n")

        response = client.post("/api/bot/config", json={"role": "CALLER"})

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["role"] == "CALLER"
        assert data["data"]["visibility"] == "PUBLIC"  # 保持不变

    def test_no_fields_returns_400(self, client: TestClient):
        """测试不传任何字段返回 400"""
        response = client.post("/api/bot/config", json={})

        assert response.status_code == 400
        assert "至少需要传一个" in response.json()["detail"]

    def test_invalid_role_returns_400(self, client: TestClient):
        """测试无效 role 值返回 400"""
        response = client.post("/api/bot/config", json={"role": "INVALID"})

        assert response.status_code == 400
        assert "role 必须是 OWNER 或 CALLER" in response.json()["detail"]

    def test_invalid_visibility_returns_400(self, client: TestClient):
        """测试无效 visibility 值返回 400"""
        response = client.post("/api/bot/config", json={"visibility": "INVALID"})

        assert response.status_code == 400
        assert "visibility 必须是 PRIVATE 或 PUBLIC" in response.json()["detail"]

    def test_preserves_other_fields(self, client: TestClient, temp_credentials_file: Path):
        """测试更新时保留其他字段"""
        temp_credentials_file.write_text(
            "TOKEN=secret123\n"
            "CLIENT_ID=staff_001\n"
            "OWNER_ID=user_456\n"
        )

        response = client.post("/api/bot/config", json={"role": "OWNER"})

        assert response.status_code == 200

        # 验证文件中其他字段保留
        content = temp_credentials_file.read_text()
        assert "TOKEN=secret123" in content
        assert "CLIENT_ID=staff_001" in content
        assert "OWNER_ID=user_456" in content
        assert "ROLE=OWNER" in content
