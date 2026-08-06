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

    def test_personalCoding_token_encrypted(self):
        svc, repo = _make_service()
        cfg = {"token": "abcdef0123456789"}
        svc.create_template(bot_id="B1", template_config=cfg, template_type="personalCoding")
        stored = repo.insert.call_args.args[0]["ext"]
        assert stored["token"].startswith(CIPHER_PREFIX)
        assert svc._vault.decrypt_or_passthrough(stored["token"]) == "abcdef0123456789"

    def test_non_coding_not_encrypted(self):
        svc, repo = _make_service()
        cfg = {"token": "abcdef0123456789"}
        svc.create_template(bot_id="B1", template_config=cfg, template_type="normalCC")
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


class TestTemplateTokenEncryptFailClosed:
    """reviewer must-fix: token encryption must fail-closed, never persist plaintext.

    When provisioning/strategy resolution raises, the encrypt-check seam must
    return True (conservative encrypt) so a plaintext token is never written to
    ``ac_templates.ext``. ``_encrypt_token_field`` skips when there is no token
    or the token is already ciphertext, so a conservative True only encrypts an
    actual plaintext token.
    """

    def test_encrypt_check_returns_true_on_provisioning_exception(self, monkeypatch):
        from agentclaw.community.core.bot_management.engines import registry

        def _boom(**_kwargs):
            raise RuntimeError("strategy resolution exploded")

        monkeypatch.setattr(registry, "resolve_provisioning", _boom)
        assert (
            registry.should_encrypt_template_token_fail_open(
                bot_id="B1",
                owner_id="",
                bot_type="",
                template_type="applicationCoding",
                template_config={"token": "plain-tok"},
            )
            is True
        )

    def test_encrypt_token_field_encrypts_plaintext_on_exception(self, monkeypatch):
        svc, repo = _make_service()
        from agentclaw.community.core.bot_management.engines import registry

        def _boom(**_kwargs):
            raise RuntimeError("strategy resolution exploded")

        monkeypatch.setattr(registry, "resolve_provisioning", _boom)
        cfg = {"token": "plain-tok-123", "other": "keep"}
        svc.create_template(bot_id="B1", template_config=cfg, template_type="applicationCoding")
        stored = repo.insert.call_args.args[0]["ext"]
        # plaintext token must NOT reach storage; conservative encrypt applied
        assert stored["token"].startswith(CIPHER_PREFIX)
        assert stored["token"] != "plain-tok-123"
        assert svc._vault.decrypt_or_passthrough(stored["token"]) == "plain-tok-123"
        assert stored["other"] == "keep"

    def test_encrypt_token_field_skips_when_no_token_even_on_exception(
        self, monkeypatch
    ):
        svc, repo = _make_service()
        from agentclaw.community.core.bot_management.engines import registry

        def _boom(**_kwargs):
            raise RuntimeError("strategy resolution exploded")

        monkeypatch.setattr(registry, "resolve_provisioning", _boom)
        cfg = {"other": "no-token-here"}
        svc.create_template(bot_id="B1", template_config=cfg, template_type="normalCC")
        stored = repo.insert.call_args.args[0]["ext"]
        # no token present -> nothing to encrypt; config preserved verbatim
        assert stored == {"other": "no-token-here"}
        assert "token" not in stored

    def test_encrypt_token_field_skips_when_token_already_ciphertext_on_exception(
        self, monkeypatch
    ):
        svc, repo = _make_service()
        from agentclaw.community.core.bot_management.engines import registry

        def _boom(**_kwargs):
            raise RuntimeError("strategy resolution exploded")

        monkeypatch.setattr(registry, "resolve_provisioning", _boom)
        already_cipher = svc._vault.encrypt("secret")
        cfg = {"token": already_cipher}
        svc.create_template(bot_id="B1", template_config=cfg, template_type="applicationCoding")
        stored = repo.insert.call_args.args[0]["ext"]
        # already-encrypted token is idempotent: not re-encrypted, stays ciphertext
        assert stored["token"] == already_cipher
