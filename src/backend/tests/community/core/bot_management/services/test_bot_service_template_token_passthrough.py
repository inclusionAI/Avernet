"""验证 token 脱敏：get_template_config 原样返回 ext（含 enc:v1: 密文 token），
不触发解密 —— get-bot API 响应里的 token 永远是密文。

解密只发生在 DeviceService.apply_device（唯一明文消费者），见 Task 5。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from agentclaw.community.core.bot_management.token_vault import (
    CIPHER_PREFIX,
    TokenVault,
)
from agentclaw.community.core.bot_management.services.template_service import TemplateService


def test_get_template_config_returns_encrypted_token_as_is():
    """ext 里存的 enc:v1: 密文，get_template_config 原样返回，不解密。"""
    repo = MagicMock()
    cipher = CIPHER_PREFIX + "someciphertext=="
    repo.get_by_bot_id.return_value = {"bot_id": "B1", "ext": {"token": cipher}}

    svc = TemplateService(repository=repo, vault=TokenVault(master_key="real-key-123"))

    config = svc.get_template_config("B1")
    # 关键断言：返回的 token 仍是密文（带前缀），未被解密成明文
    assert config["token"] == cipher
    assert config["token"].startswith(CIPHER_PREFIX)


def test_get_template_config_passthrough_plaintext():
    """存量明文 token（无前缀）原样返回（兼容）。"""
    repo = MagicMock()
    repo.get_by_bot_id.return_value = {"bot_id": "B1", "ext": {"token": "abcdef0123456789"}}

    svc = TemplateService(repository=repo, vault=TokenVault(master_key="real-key-123"))

    config = svc.get_template_config("B1")
    assert config["token"] == "abcdef0123456789"
