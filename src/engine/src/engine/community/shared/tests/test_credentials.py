"""
测试 CredentialsService 的 update_fields 功能
"""
import pytest
from pathlib import Path

from engine.community.shared.credentials import CredentialsService, Credentials


class TestCredentialsServiceUpdateFields:
    """测试 update_fields 方法"""

    @pytest.fixture
    def temp_credentials_file(self, tmp_path: Path) -> Path:
        """创建临时凭证文件"""
        return tmp_path / ".credentials"

    @pytest.fixture
    def service(self, temp_credentials_file: Path) -> CredentialsService:
        """创建使用临时文件的 CredentialsService 实例"""
        service = CredentialsService(path=temp_credentials_file)
        return service

    def test_update_fields_creates_new_file(self, service: CredentialsService, temp_credentials_file: Path):
        """测试文件不存在时创建新文件"""
        assert not temp_credentials_file.exists()

        service.update_fields({"ROLE": "OWNER", "VISIBILITY": "PUBLIC"})

        assert temp_credentials_file.exists()
        creds = service.get_all()
        assert creds.role == "OWNER"
        assert creds.visibility == "PUBLIC"

    def test_update_fields_appends_to_existing_file(self, service: CredentialsService, temp_credentials_file: Path):
        """测试追加新字段到已有文件"""
        # 创建已有文件
        temp_credentials_file.write_text("TOKEN=abc123\nCLIENT_ID=staff_001\n")

        service.update_fields({"ROLE": "CALLER"})

        content = temp_credentials_file.read_text()
        assert "TOKEN=abc123" in content
        assert "CLIENT_ID=staff_001" in content
        assert "ROLE=CALLER" in content

        creds = service.get_all()
        assert creds.token == "abc123"
        assert creds.client_id == "staff_001"
        assert creds.role == "CALLER"

    def test_update_fields_replaces_existing_key(self, service: CredentialsService, temp_credentials_file: Path):
        """测试替换已有字段的值"""
        temp_credentials_file.write_text("ROLE=OWNER\nVISIBILITY=PRIVATE\n")

        service.update_fields({"ROLE": "CALLER"})

        content = temp_credentials_file.read_text()
        # ROLE 应该被更新
        assert "ROLE=CALLER" in content
        assert "ROLE=OWNER" not in content
        # VISIBILITY 应该保留
        assert "VISIBILITY=PRIVATE" in content

        creds = service.get_all()
        assert creds.role == "CALLER"
        assert creds.visibility == "PRIVATE"

    def test_update_fields_preserves_other_content(self, service: CredentialsService, temp_credentials_file: Path):
        """测试保留其他字段、空行和注释"""
        temp_credentials_file.write_text(
            "# 这是注释\n"
            "TOKEN=secret_token\n"
            "\n"
            "CLIENT_ID=staff_002\n"
            "# 另一个注释\n"
            "OWNER_ID=user_123\n"
        )

        service.update_fields({"ROLE": "OWNER"})

        content = temp_credentials_file.read_text()
        # 原有内容应该保留
        assert "# 这是注释" in content
        assert "TOKEN=secret_token" in content
        assert "CLIENT_ID=staff_002" in content
        assert "# 另一个注释" in content
        assert "OWNER_ID=user_123" in content
        # 新字段应该追加
        assert "ROLE=OWNER" in content

    def test_update_fields_multiple_fields(self, service: CredentialsService, temp_credentials_file: Path):
        """测试同时更新多个字段"""
        temp_credentials_file.write_text("TOKEN=abc\n")

        service.update_fields({"ROLE": "OWNER", "VISIBILITY": "PUBLIC"})

        creds = service.get_all()
        assert creds.token == "abc"
        assert creds.role == "OWNER"
        assert creds.visibility == "PUBLIC"

    def test_update_fields_case_insensitive_key(self, service: CredentialsService, temp_credentials_file: Path):
        """测试 key 大小写不敏感（文件中 key 统一转大写处理）"""
        temp_credentials_file.write_text("role=OWNER\n")

        service.update_fields({"ROLE": "CALLER"})

        # 应该能识别并更新已有的 role 字段
        creds = service.get_all()
        assert creds.role == "CALLER"

    def test_update_fields_does_not_modify_caller_dict(self, service: CredentialsService, temp_credentials_file: Path):
        """测试不修改调用者传入的字典"""
        updates = {"ROLE": "OWNER", "VISIBILITY": "PUBLIC"}
        original_updates = dict(updates)

        service.update_fields(updates)

        # 调用者字典应该保持不变
        assert updates == original_updates

    def test_loads_agent_code(self, service: CredentialsService, temp_credentials_file: Path):
        """测试读取 AGENT_CODE 字段"""
        temp_credentials_file.write_text("AGENT_CODE=agent_001\n")

        creds = service.get_all()

        assert creds.agent_code == "agent_001"
        assert service.get_agent_code() == "agent_001"

    def test_agent_code_fallback_to_env(self, service: CredentialsService, temp_credentials_file: Path, monkeypatch):
        """credentials 文件无 AGENT_CODE 时 fallback 到环境变量"""
        temp_credentials_file.write_text("TOKEN=abc\n")

        monkeypatch.setenv("AGENT_CODE", "env_agent_001")
        service.reload()

        assert service.get_agent_code() == "env_agent_001"

    def test_agent_code_env_case_insensitive(self, service: CredentialsService, temp_credentials_file: Path, monkeypatch):
        """环境变量忽略大小写"""
        temp_credentials_file.write_text("TOKEN=abc\n")

        monkeypatch.setenv("agent_code", "env_agent_lower")
        service.reload()

        assert service.get_agent_code() == "env_agent_lower"

    def test_agent_code_file_takes_priority_over_env(self, service: CredentialsService, temp_credentials_file: Path, monkeypatch):
        """credentials 文件中的 AGENT_CODE 优先于环境变量"""
        temp_credentials_file.write_text("AGENT_CODE=file_agent\n")
        monkeypatch.setenv("AGENT_CODE", "env_agent")
        service.reload()

        assert service.get_agent_code() == "file_agent"


class TestCredentialsDataclass:
    """测试 Credentials dataclass"""

    def test_credentials_default_values(self):
        """测试默认值为 None"""
        creds = Credentials()
        assert creds.token is None
        assert creds.agent_code is None
        assert creds.client_id is None
        assert creds.owner_id is None
        assert creds.bot_id is None
        assert creds.role is None
        assert creds.visibility is None

    def test_credentials_with_values(self):
        """测试设置值"""
        creds = Credentials(
            token="token123",
            client_id="client456",
            owner_id="owner789",
            bot_id="bot001",
            role="OWNER",
            visibility="PUBLIC",
        )
        assert creds.token == "token123"
        assert creds.client_id == "client456"
        assert creds.owner_id == "owner789"
        assert creds.bot_id == "bot001"
        assert creds.role == "OWNER"
        assert creds.visibility == "PUBLIC"
