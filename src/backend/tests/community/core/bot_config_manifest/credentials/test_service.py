"""Service tests for tenant source credentials (W3, #1471).

Backed by the real ORM repository on in-memory SQLite — the fail-closed
guarantee in particular ("nothing written") is a storage claim, and
mocks confirm those vacuously. The Raw-prefix family lives in the policy
tests; these pin the service's composition: validation order, redaction,
rotation semantics, and the binding's per-hop re-read.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine

from agentclaw.community.core.bot_config_manifest.credentials.errors import (
    CredentialError,
    CredentialNotFoundError,
    CredentialNotOwnedError,
    MasterKeyUnavailableError,
)
from agentclaw.community.core.bot_config_manifest.credentials.policy import (
    PrefixAuthorizationError,
)
from agentclaw.community.core.bot_config_manifest.credentials.service import (
    SourceCredentialService,
)
from agentclaw.community.core.bot_management.token_vault import (
    CIPHER_PREFIX,
    TokenVault,
)
from agentclaw.community.core.repository.implementations.bot.source_credential import (
    SourceCredentialRepository,
)
# Side effect: registers the model on Base.metadata for create_all.
from agentclaw.community.core.bot_config_manifest.credentials.models import (  # noqa: F401
    SourceCredentialModel,
)
from tests.community.core.bot_config_manifest.credentials.repo_helper import (
    InMemorySqliteDB,
)


PREFIXES = ["https://git.example/team/content"]
ROTATED = "Bearer rotated-token"
INITIAL = "Bearer initial-token"

OWNER_APP = 7
OTHER_APP = 8


@pytest.fixture
def service() -> SourceCredentialService:
    engine = create_engine("sqlite:///:memory:")
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return SourceCredentialService(
        SourceCredentialRepository(InMemorySqliteDB(engine)),
        TokenVault("master-key-material"),
    )


def _put(service, secret=INITIAL, name="corp-git", **overrides):
    kwargs = dict(
        name=name,
        header_name="PRIVATE-TOKEN",
        secret=secret,
        allowed_prefixes=PREFIXES,
        owner_app_id=OWNER_APP,
        modifier="alice",
    )
    kwargs.update(overrides)
    return service.put(**kwargs)


def test_put_stores_ciphered_and_answers_masked(service):
    public = _put(service)
    assert public.name == "corp-git"
    assert public.header_name == "PRIVATE-TOKEN"
    assert public.allowed_prefixes == PREFIXES
    assert public.has_secret is True
    # 值永不出现在公开形态;密文也不出现(那是存储形态,不是回答)。
    assert not hasattr(public, "secret_ciphertext")
    assert "initial-token" not in repr(public)
    # 存储侧是 enc:v1: 密文(真库断言,不是 mock 的应声)。
    row = service._repository.get(name="corp-git")
    assert row.secret_ciphertext.startswith(CIPHER_PREFIX)
    assert "initial-token" not in row.secret_ciphertext


def test_get_and_list_are_masked_and_gets_404_are_named(service):
    _put(service)
    got = service.get(name="corp-git")
    assert got.has_secret is True
    assert [item.name for item in service.list_credentials()] == ["corp-git"]
    with pytest.raises(CredentialNotFoundError, match="ghost"):
        service.get(name="ghost")


def test_rotation_is_a_reput_with_a_new_value(service):
    _put(service)
    rotated = _put(service, secret=ROTATED)
    rows = service._repository.list()
    assert len(rows) == 1  # same row replaced, no second row for the name
    binding = service.binding(name="corp-git")
    assert binding.headers_for("https://git.example/team/content")[
        "PRIVATE-TOKEN"
    ] == ROTATED
    assert rotated.name == "corp-git"


def test_binding_reads_per_hop_so_rotation_needs_no_signal(service):
    """绑定为每跳现读——轮换后的下一次 fetch 即生效,无需任何通知。"""
    _put(service)
    binding = service.binding(name="corp-git")
    first = binding.headers_for("https://git.example/team/content")["PRIVATE-TOKEN"]
    _put(service, secret=ROTATED)
    second = binding.headers_for("https://git.example/team/content")["PRIVATE-TOKEN"]
    assert first == INITIAL
    assert second == ROTATED


def test_binding_refuses_targets_outside_the_prefixes(service):
    _put(service)
    binding = service.binding(name="corp-git")
    with pytest.raises(PrefixAuthorizationError):
        binding.reauthorize("https://elsewhere.example/anything")
    # 段边界意见由 policy 测试钉;这里钉"binding 接的是同一判官"。
    binding.reauthorize("https://git.example/team/content/inner.md")


def test_deleted_credential_bindings_fail_by_name_only(service):
    _put(service)
    binding = service.binding(name="corp-git")
    service.delete(name="corp-git", caller_app_id=OWNER_APP)
    with pytest.raises(CredentialNotFoundError, match="corp-git"):
        binding.headers_for("https://git.example/team/content")
    with pytest.raises(CredentialNotFoundError, match="corp-git"):
        service.binding(name="corp-git")
    assert (
        service.delete(name="corp-git", caller_app_id=OWNER_APP) is False
    )  # idempotent


# --- ownership: rotation and delete are the creating application's --------


def test_the_creating_application_owns_the_name(service):
    public = _put(service)
    assert public.owner_app_id == OWNER_APP
    row = service._repository.get(name="corp-git")
    assert row.owner_app_id == OWNER_APP
    # 轮换不换归属:owner 在插入时钉死。
    _put(service, secret=ROTATED, modifier="bob")
    assert service._repository.get(name="corp-git").owner_app_id == OWNER_APP


def test_rotation_by_another_application_is_refused_before_storage(service):
    """整行替换的 re-PUT 是对名字所有引用的改写——非 owner 一律先拒后写。"""
    _put(service, secret=INITIAL)
    with pytest.raises(CredentialNotOwnedError, match="corp-git"):
        _put(service, secret="Bearer hijack", owner_app_id=OTHER_APP)
    # 存储未被触碰:值、审计行都还在原状。
    row = service._repository.get(name="corp-git")
    binding = service.binding(name="corp-git")
    assert (
        binding.headers_for("https://git.example/team/content")["PRIVATE-TOKEN"]
        == INITIAL
    )
    assert row.modifier == "alice"


def test_delete_by_another_application_is_refused(service):
    _put(service)
    with pytest.raises(CredentialNotOwnedError, match="corp-git"):
        service.delete(name="corp-git", caller_app_id=OTHER_APP)
    assert service._repository.get(name="corp-git") is not None
    assert service.delete(name="corp-git", caller_app_id=OWNER_APP) is True


def test_reads_belong_to_every_tenant_application(service):
    """名字是租户共享的引用命名空间:读取不设 owner 门。"""
    _put(service)
    assert service.get(name="corp-git").owner_app_id == OWNER_APP
    assert [item.name for item in service.list_credentials()] == ["corp-git"]


def test_error_messages_never_carry_the_secret(service):
    _put(service, secret="Bearer super-hush")
    try:
        service.get(name="ghost")
    except CredentialNotFoundError as exc:
        assert "super-hush" not in str(exc)
    try:
        service.binding(name="ghost")
    except CredentialNotFoundError as exc:
        assert "super-hush" not in str(exc)


# --- validation order and shape --------------------------------------------------


@pytest.mark.parametrize("bad_type", ["oss_aksk", "basic"])
def test_reserved_types_are_refused_at_write(service, bad_type):
    with pytest.raises(CredentialError, match="reserved"):
        _put(service, credential_type=bad_type)
    assert service._repository.get(name="corp-git") is None


def test_unknown_types_are_refused_too(service):
    with pytest.raises(CredentialError, match="only header"):
        _put(service, credential_type="digest")
    assert service._repository.get(name="corp-git") is None


@pytest.mark.parametrize(
    "overrides",
    [
        dict(name=""),  # empty
        dict(name="has space"),  # whitespace in identifier
        dict(name="x" * 129),  # over width
        dict(header_name="Not A Header"),  # illegal chars
        dict(header_name=""),
        dict(secret=""),
        dict(allowed_prefixes=[]),
        dict(allowed_prefixes=["http://host/repo"]),  # https pinned
        dict(allowed_prefixes=["git.example/repo"]),  # absolute only
    ],
)
def test_invalid_inputs_are_refused(service, overrides):
    with pytest.raises(CredentialError):
        _put(service, **overrides)
    assert service._repository.get(name=overrides.get("name", "corp-git")) is None


# --- fail-closed storage --------------------------------------------------


@pytest.fixture
def sqlite_engine():
    engine = create_engine("sqlite:///:memory:")
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return engine


def test_fail_closed_without_a_master_key_refuses_before_writing(sqlite_engine):
    svc = SourceCredentialService(
        SourceCredentialRepository(InMemorySqliteDB(sqlite_engine)),
        TokenVault(""),  # singlebox default resolves no key
        fail_closed=True,
    )
    with pytest.raises(MasterKeyUnavailableError):
        _put(svc)
    # 先拒后写:库被留下空手(空表),不是一行明文。
    assert svc._repository.list() == []


def test_fail_closed_with_a_master_key_writes_ciphered(sqlite_engine):
    svc = SourceCredentialService(
        SourceCredentialRepository(InMemorySqliteDB(sqlite_engine)),
        TokenVault("master-key-material"),
        fail_closed=True,
    )
    _put(svc)
    assert svc._repository.get(name="corp-git").secret_ciphertext.startswith(
        CIPHER_PREFIX
    )


def test_singlebox_without_a_master_key_writes_plaintext(sqlite_engine):
    """非 fail-closed profile 空主密钥 = TokenVault 既有 passthrough——
    本地联调成立靠它,生产写靠上面两测试的反面。"""
    svc = SourceCredentialService(
        SourceCredentialRepository(InMemorySqliteDB(sqlite_engine)),
        TokenVault(""),
    )
    _put(svc)
    row = svc._repository.get(name="corp-git")
    assert not row.secret_ciphertext.startswith(CIPHER_PREFIX)
    binding = svc.binding(name="corp-git")
    # passthrough 后仍可出示(同一 vault 解密路径)。
    assert (
        binding.headers_for("https://git.example/team/content")["PRIVATE-TOKEN"]
        == INITIAL
    )


def test_modifier_and_rotation_stamp_the_audit_row(service):
    _put(service, modifier="bob")
    aged = service._repository.get(name="corp-git").gmt_modified - timedelta(days=2)
    _put(service, secret=ROTATED, modifier="carol")
    row = service._repository.get(name="corp-git")
    assert row.modifier == "carol"
    assert row.gmt_modified > aged
