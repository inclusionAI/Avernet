from __future__ import annotations

from unittest.mock import MagicMock

from agentclaw.community.core.bot_management.token_vault import (
    CIPHER_PREFIX,
    TokenVault,
)
from agentclaw.community.core.bot_management.services.template_service import TemplateService


def _make_service(master_key: str = "real-key-123"):
    repo = MagicMock()
    repo.insert.return_value = {"bot_id": "B1", "ext": {}}
    repo.update_by_bot_id.return_value = {"bot_id": "B1", "ext": {}}
    repo.get_by_bot_id.return_value = None
    svc = TemplateService(
        repository=repo,
        vault=TokenVault(master_key=master_key),
    )
    return svc, repo


class TestCreateTemplateEncryptsToken:
    def test_applicationCoding_token_encrypted(self):
        svc, repo = _make_service()
        cfg = {"token": "abcdef0123456789", "other": "x"}
        svc.create_template(bot_id="B1", template_config=cfg, template_type="applicationCoding")
        stored = repo.insert.call_args.args[0]["ext"]
        assert stored["token"].startswith(CIPHER_PREFIX)
        assert svc._vault.decrypt_or_passthrough(stored["token"]) == "abcdef0123456789"
        assert stored["other"] == "x"

    def test_non_applicationCoding_not_encrypted(self):
        svc, repo = _make_service()
        cfg = {"token": "abcdef0123456789"}
        svc.create_template(bot_id="B1", template_config=cfg, template_type="personalCoding")
        stored = repo.insert.call_args.args[0]["ext"]
        assert stored["token"] == "abcdef0123456789"

    def test_no_template_type_not_encrypted(self):
        svc, repo = _make_service()
        cfg = {"token": "abcdef0123456789"}
        svc.create_template(bot_id="B1", template_config=cfg)
        stored = repo.insert.call_args.args[0]["ext"]
        assert stored["token"] == "abcdef0123456789"

    def test_already_encrypted_not_re_encrypted(self):
        svc, repo = _make_service()
        already = CIPHER_PREFIX + "someciphertext=="
        cfg = {"token": already}
        svc.create_template(bot_id="B1", template_config=cfg, template_type="applicationCoding")
        stored = repo.insert.call_args.args[0]["ext"]
        assert stored["token"] == already

    def test_empty_key_degrades_to_plaintext(self):
        svc, repo = _make_service(master_key="")
        cfg = {"token": "abcdef0123456789"}
        svc.create_template(bot_id="B1", template_config=cfg, template_type="applicationCoding")
        stored = repo.insert.call_args.args[0]["ext"]
        assert stored["token"] == "abcdef0123456789"


class TestUpdateTemplateEncryptsToken:
    def test_applicationCoding_token_encrypted_on_update(self):
        svc, repo = _make_service()
        cfg = {"token": "abcdef0123456789"}
        # update_template 内部调 exists_template -> get_by_bot_id; mock exists_template
        svc.exists_template = MagicMock(return_value=True)
        svc.update_template(bot_id="B1", template_config=cfg, template_type="applicationCoding")
        stored = repo.update_by_bot_id.call_args.args[1]["ext"]
        assert stored["token"].startswith(CIPHER_PREFIX)
        assert svc._vault.decrypt_or_passthrough(stored["token"]) == "abcdef0123456789"


class TestCreateOrUpdateTemplateEncryptsToken:
    def test_exists_branch_calls_update_with_encryption(self):
        svc, repo = _make_service()
        cfg = {"token": "abcdef0123456789"}
        svc.exists_template = MagicMock(return_value=True)
        svc.create_or_update_template(
            bot_id="B1", template_config=cfg, template_type="applicationCoding"
        )
        stored = repo.update_by_bot_id.call_args.args[1]["ext"]
        assert stored["token"].startswith(CIPHER_PREFIX)
        assert svc._vault.decrypt_or_passthrough(stored["token"]) == "abcdef0123456789"
        repo.insert.assert_not_called()

    def test_not_exists_branch_calls_create_with_encryption(self):
        svc, repo = _make_service()
        cfg = {"token": "abcdef0123456789"}
        svc.exists_template = MagicMock(return_value=False)
        svc.create_or_update_template(
            bot_id="B1", template_config=cfg, template_type="applicationCoding"
        )
        stored = repo.insert.call_args.args[0]["ext"]
        assert stored["token"].startswith(CIPHER_PREFIX)
        assert svc._vault.decrypt_or_passthrough(stored["token"]) == "abcdef0123456789"
        repo.update_by_bot_id.assert_not_called()


class TestGetDecryptedCodefuseToken:
    """get_decrypted_codefuse_token 应从落库密文还原明文 auth_code。"""

    def test_decrypts_stored_ciphertext(self):
        svc, repo = _make_service()
        plaintext = "abcdef0123456789"
        ciphertext = svc._vault.encrypt(plaintext)
        repo.get_by_bot_id.return_value = {"bot_id": "B1", "ext": {"token": ciphertext}}
        assert svc.get_decrypted_codefuse_token("B1") == plaintext

    def test_passthrough_when_plaintext_stored(self):
        """历史明文（无 enc 前缀）应原样透传。"""
        svc, repo = _make_service()
        repo.get_by_bot_id.return_value = {"bot_id": "B1", "ext": {"token": "rawtoken1234"}}
        assert svc.get_decrypted_codefuse_token("B1") == "rawtoken1234"

    def test_returns_none_when_no_template(self):
        svc, repo = _make_service()
        repo.get_by_bot_id.return_value = None
        assert svc.get_decrypted_codefuse_token("B1") is None

    def test_returns_none_when_no_token_field(self):
        svc, repo = _make_service()
        repo.get_by_bot_id.return_value = {"bot_id": "B1", "ext": {"other": "x"}}
        assert svc.get_decrypted_codefuse_token("B1") is None

    def test_returns_none_when_token_empty(self):
        svc, repo = _make_service()
        repo.get_by_bot_id.return_value = {"bot_id": "B1", "ext": {"token": ""}}
        assert svc.get_decrypted_codefuse_token("B1") is None

    def test_returns_none_when_template_config_not_dict(self):
        svc, repo = _make_service()
        repo.get_by_bot_id.return_value = {"bot_id": "B1", "ext": "not-a-dict"}
        assert svc.get_decrypted_codefuse_token("B1") is None

    def test_empty_key_passthrough_round_trip(self):
        """singlebox 空 master_key:加密退化为原样，解密也原样，闭环自洽。"""
        svc, repo = _make_service(master_key="")
        repo.get_by_bot_id.return_value = {"bot_id": "B1", "ext": {"token": "plain-tok-1234"}}
        assert svc.get_decrypted_codefuse_token("B1") == "plain-tok-1234"

    def test_decrypt_failure_returns_none_not_raise(self):
        """密文损坏/密钥错配时应吞异常返回 None，不向调用方抛出。"""
        svc, repo = _make_service()
        repo.get_by_bot_id.return_value = {
            "bot_id": "B1",
            "ext": {"token": CIPHER_PREFIX + "!!corrupt!!"},
        }
        assert svc.get_decrypted_codefuse_token("B1") is None
