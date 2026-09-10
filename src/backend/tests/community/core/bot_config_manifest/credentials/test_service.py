"""Service tests for tenant source credentials (W3, #1471).

Backed by the real ORM repository on in-memory SQLite — the fail-closed
guarantee in particular ("nothing written") is a storage claim, and
mocks confirm those vacuously. The Raw-prefix family lives in the policy
tests; these pin the service's composition: validation order, redaction,
rotation semantics, and the binding's per-hop re-read.
"""

from __future__ import annotations

from datetime import timedelta

import ipaddress
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
    CredentialType,
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


#: Scripted DNS for the endpoint guard. Real resolution in a unit test would
#: make these cases depend on the network, and the interesting ones — a host
#: that resolves somewhere private — are not expressible without it.
_ENDPOINT_DNS: dict[str, list[str]] = {
    "objects.example-corp.com": ["93.184.216.34"],
    "objects.example.test": ["93.184.216.35"],
    "localhost": ["127.0.0.1"],
    "metadata.internal": ["169.254.169.254"],
    "store.corp.internal": ["10.0.0.7"],
}


def _fake_dns(host: str) -> list[str]:
    """Scripted DNS that answers a numeric host with itself.

    Not decoration: ``getaddrinfo`` resolves a literal address from the
    string without touching the network, and the guard's "a literal metadata
    address is still refused" property rests entirely on that. A double that
    answered ``[]`` here would send the literal down the unresolvable road
    and report the guard passing a case the real resolver refuses.
    """
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return _ENDPOINT_DNS.get(host, [])
    return [host]


@pytest.fixture
def service() -> SourceCredentialService:
    engine = create_engine("sqlite:///:memory:")
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return SourceCredentialService(
        SourceCredentialRepository(InMemorySqliteDB(engine)),
        TokenVault("master-key-material"),
        endpoint_resolver=_fake_dns,
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


@pytest.mark.parametrize("bad_type", ["basic"])
def test_reserved_types_are_refused_at_write(service, bad_type):
    """``oss_aksk`` has left this list — it is the object store's real road
    now. ``basic`` stays: still a name in the vocabulary with nothing behind
    it, so a caller sending it is refused *by name* rather than as garbage."""
    with pytest.raises(CredentialError, match="reserved"):
        _put(service, credential_type=bad_type)
    assert service._repository.get(name="corp-git") is None


def test_unknown_types_are_refused_too(service):
    with pytest.raises(CredentialError, match="unknown credential type"):
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


# --- the oss_aksk mechanism (defect D4) --------------------------------------
#
# The vocabulary named this type from day one and the write path refused it, so
# a private object store was unreachable: every non-git source was an HTTPS GET
# with an optional header, and a bucket requiring request signing had no road at
# all. These pin the mechanism as a whole — storage, redaction, refusal of
# mismatched shapes, and what actually goes on the wire.

#: Empty, and that is the mechanism's shape rather than an omission: the
#: endpoint comes off the row, so there is no tenant-supplied host for a
#: prefix to constrain (see ``test_prefixes_are_refused_on_a_signing_credential``).
_AKSK_PREFIXES: list[str] = []
_AK = "LTAI5tExampleKeyId"
_SK = "an-object-store-secret-key"
_ENDPOINT = "https://objects.example-corp.com"
_REGION = "cn-hangzhou"


def _put_aksk(service, name="oss-artifacts", **overrides):
    kwargs = dict(
        name=name,
        credential_type="oss_aksk",
        access_key_id=_AK,
        endpoint=_ENDPOINT,
        region=_REGION,
        secret=_SK,
        allowed_prefixes=_AKSK_PREFIXES,
        owner_app_id=OWNER_APP,
        modifier="alice",
        header_name=None,
    )
    kwargs.update(overrides)
    return service.put(**kwargs)


def test_an_aksk_credential_registers_with_both_values(service):
    public = _put_aksk(service)
    assert public.credential_type == "oss_aksk"
    assert public.access_key_id == _AK
    assert public.has_secret is True
    # No header is presented by this mechanism, and the read says so rather
    # than answering with the empty string storage happens to hold.
    assert public.header_name is None


def test_the_secret_key_is_stored_ciphered_and_never_read_back(service):
    """The half of the pair that must never come back, in every read path."""
    _put_aksk(service)
    row = service._repository.get(name="oss-artifacts")
    assert row.secret_ciphertext.startswith(CIPHER_PREFIX)
    assert _SK not in row.secret_ciphertext

    for record in (service.get(name="oss-artifacts"), *service.list_credentials()):
        assert _SK not in repr(record)
        assert not hasattr(record, "secret")
        # The key id is the deliberate exception: an identifier, not a secret,
        # and rotation cannot be verified without seeing which one is installed.
        assert record.access_key_id == _AK


def test_a_region_is_required_and_rides_the_record(service):
    """The store's signature scheme scopes every signature to a region and its
    client refuses to sign without one — so a credential stored without a
    region would be accepted here and fail every apply, which is the one thing
    this surface must never do. Refused at PUT, where refusing costs nobody an
    apply."""
    assert _put_aksk(service).region == _REGION
    for absent in (None, ""):
        with pytest.raises(CredentialError, match="requires 'region'"):
            _put_aksk(service, name="regionless", region=absent)
        assert service._repository.get(name="regionless") is None


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        # Each is one mechanism's field on the other mechanism, or a required
        # field missing. The second kind fails loudly on first use anyway; the
        # FIRST kind is the one that matters — silently dropping it would leave
        # a caller believing they had configured signing.
        (dict(access_key_id=None), "requires 'access_key_id'"),
        (dict(access_key_id=""), "requires 'access_key_id'"),
        (dict(region=None), "requires 'region'"),
        (dict(secret=""), "secret must not be empty"),
        (dict(header_name="PRIVATE-TOKEN"), "belongs to a 'header' credential"),
        (dict(access_key_id="x" * 257), "access_key_id over"),
        (dict(region="r" * 65), "region over"),
    ],
)
def test_a_mismatched_aksk_shape_is_refused_not_ignored(service, overrides, match):
    with pytest.raises(CredentialError, match=match):
        _put_aksk(service, **overrides)
    assert service._repository.get(name="oss-artifacts") is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("access_key_id", _AK), ("region", "cn-hangzhou")],
)
def test_a_signing_field_on_a_header_credential_is_refused(service, field, value):
    """The other direction of the same rule. A header credential that quietly
    accepted an access key id would report it back on every read, describing a
    mechanism it does not use."""
    with pytest.raises(CredentialError, match="belongs to a 'oss_aksk' credential"):
        _put(service, **{field: value})
    assert service._repository.get(name="corp-git") is None


def test_a_header_credential_still_requires_its_header_name(service):
    with pytest.raises(CredentialError, match="requires 'header_name'"):
        _put(service, header_name=None)


def test_an_aksk_binding_hands_over_a_target_and_never_a_header(service):
    """What actually reaches the wire, now that nothing is signed here.

    A header credential puts its secret on the wire under a name the caller
    chose. This one puts nothing there: it hands the object-store client an
    address and an identity, and the client owns the request. ``headers_for``
    is not merely unused on this road — there is no header a bucket read
    presents, and asking for one is a category error.
    """
    _put_aksk(service)
    target = service.binding(name="oss-artifacts").object_store_target("bkt")

    assert target.endpoint == _ENDPOINT
    assert target.bucket == "bkt"
    assert target.access_key_id == _AK
    assert target.secret_access_key == _SK  # reaches the client, nothing else


def test_a_row_stored_before_region_was_required_is_refused_at_binding(service):
    """A legacy row with no region cannot be used: signature version 4 cannot
    sign without one. It is refused here, before any store client is built,
    so the entry fails naming the credential to rotate rather than reaching
    the SDK and being reported as a store-side refusal. Written straight to
    the repository because the service itself no longer accepts such a row."""
    service._repository.upsert(
        name="legacy-aksk",
        credential_type=CredentialType.OSS_AKSK,
        header_name="",
        access_key_id=_AK,
        endpoint=_ENDPOINT,
        region=None,
        allowed_prefixes=[],
        secret_ciphertext=_SK,
        owner_app_id=OWNER_APP,
        modifier="alice",
    )
    with pytest.raises(CredentialError, match="has no region"):
        service.binding(name="legacy-aksk").object_store_target("bkt")


def test_the_target_redacts_the_secret_when_it_is_printed(service):
    """A target reaches a log or a traceback the moment something raises while
    holding one, and a dataclass's default repr would print the key."""
    _put_aksk(service)
    text = repr(service.binding(name="oss-artifacts").object_store_target("bkt"))
    assert _SK not in text
    assert "<redacted>" in text
    assert _AK in text  # the id is public by design; the secret key is not


def test_the_document_chooses_the_bucket_and_never_the_endpoint(service):
    """The security property the signing road had to enforce with a policy.

    ``bucket`` is the caller's argument — a manifest names it. ``endpoint``
    comes off the row and no argument can move it, so a tenant's document
    cannot point its own credential at a host of its choosing. Held by
    construction here, where ``allowed_prefixes`` used to hold it by rule.
    """
    _put_aksk(service)
    binding = service.binding(name="oss-artifacts")
    first = binding.object_store_target("bucket-one")
    second = binding.object_store_target("bucket-two")
    assert (first.bucket, second.bucket) == ("bucket-one", "bucket-two")
    assert first.endpoint == second.endpoint == _ENDPOINT


def test_rotation_lands_on_the_next_target(service):
    """Rotation has no signal — the binding re-reads the row per call, which is
    the observable contract the header mechanism already has."""
    _put_aksk(service)
    binding = service.binding(name="oss-artifacts")
    assert binding.object_store_target("b").access_key_id == _AK
    _put_aksk(service, access_key_id="LTAI5tRotated")
    assert binding.object_store_target("b").access_key_id == "LTAI5tRotated"


def test_a_header_credential_has_no_object_store_target(service):
    """Asked of the wrong mechanism, this refuses by name rather than handing
    back a target with an empty endpoint that would fail later as a transport
    error nobody could trace back to the credential's type."""
    service.put(
        name="hdr",
        secret="tok",
        header_name="Authorization",
        allowed_prefixes=["https://artifacts.example-corp.com/tools"],
        owner_app_id=OWNER_APP,
        modifier="alice",
    )
    with pytest.raises(CredentialError, match="oss_aksk"):
        service.binding(name="hdr").object_store_target("bkt")


def test_a_signing_credential_presents_no_headers(service):
    """The symmetry ``object_store_target`` already had, in the other
    direction — and it is the one that was missing.

    Nothing ties a source's ``protocol`` to its credential's type, so a
    ``protocol: git`` source may legally name an ``oss_aksk`` credential. The
    git road then asks that binding for headers. Without this guard it
    answered ``{"": <the object store's secret key>}`` — the empty string is
    the stored "no header" sentinel — and sent it to whatever git host the
    document named. A refusal by name is the only safe answer.
    """
    _put_aksk(service)
    with pytest.raises(CredentialError, match="header"):
        service.binding(name="oss-artifacts").headers_for(
            "https://code.example.com/team/x.git"
        )


def test_reauthorizing_a_credential_with_no_prefixes_refuses_in_family(service):
    """A stored empty prefix list must not raise a bare ``ValueError``.

    ``oss_aksk`` rows legitimately store ``[]`` now, and the git road calls
    ``reauthorize`` before it fetches. ``PrefixAuthorizationPolicy`` answers an
    empty list with a plain ``ValueError``, which none of the fetcher's except
    clauses catch — it escaped ``resolve`` and crashed the whole apply, the
    one failure mode that module's docstring says must never happen. The
    reason has to arrive in a family the caller already handles.
    """
    _put_aksk(service)
    with pytest.raises((CredentialError, PrefixAuthorizationError)):
        service.binding(name="oss-artifacts").reauthorize(
            "https://code.example.com/team/x.git"
        )


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("http://localhost:9000", "scheme"),
        ("https://localhost", "non-public"),
        ("https://metadata.internal/latest/meta-data/", "non-public"),
        ("https://store.corp.internal", "non-public"),
        ("https://user:pw@objects.example-corp.com", "userinfo"),
        ("not-a-url", "absolute"),
        ("https://169.254.169.254/latest/meta-data/", "non-public"),
    ],
)
def test_an_unsafe_endpoint_is_refused_before_it_is_stored(
    service, endpoint, expected
):
    """The hole the object-store road opened, closed at the write.

    That road drops the guarded fetcher's SSRF machinery on the grounds that
    its endpoint comes off a credential rather than out of a tenant's
    document. But the credential is written by an authenticated tenant
    application through this very method — "not from the document" was never
    "not from the tenant". Without this check
    ``http://169.254.169.254/`` is a storable endpoint and the platform
    connects to it on the next apply.

    Refused at write, not at read: a bad endpoint that reaches storage fails
    every apply citing it, and the caller who can fix it is the one here.
    """
    with pytest.raises(CredentialError, match=expected):
        _put_aksk(service, endpoint=endpoint)
    assert service.list_credentials() == []  # nothing persisted


def test_an_endpoint_that_does_not_resolve_here_is_stored_anyway(service):
    """The one concession, pinned so nobody tightens it back by accident.

    Storing a credential must not depend on this pod's DNS. The pod that
    writes one is not the pod that later reads with it, so "does not resolve
    here" is a prediction rather than a fact — split-horizon DNS, a private
    zone and a minute's outage all produce it for a perfectly good endpoint,
    and refusing would turn a rotation into a 4xx nobody can act on.

    The concession costs nothing against what the guard is for: whoever
    controls a name can answer with a public address at write time and a
    link-local one at read time, so refusing here was never the thing
    stopping them. What still bites — the literal metadata address, a name
    that resolves somewhere private, every shape rule — is asserted above.
    """
    record = _put_aksk(service, endpoint="https://nowhere.invalid")
    assert record.endpoint == "https://nowhere.invalid"


def test_one_unparseable_answer_does_not_hide_a_private_one(sqlite_engine):
    """A malformed entry in a DNS answer must not excuse the rest of it.

    The first version parsed the answer as one comprehension, so a single
    unparseable entry raised and threw the whole list away — including the
    10.x address that was the reason to refuse — and the endpoint stored.
    Refusal is per-address, and an entry that is not an address is skipped
    rather than fatal.
    """
    svc = SourceCredentialService(
        SourceCredentialRepository(InMemorySqliteDB(sqlite_engine)),
        TokenVault("master-key-material"),
        endpoint_resolver=lambda host: ["not-an-address", "10.0.0.7"],
    )
    with pytest.raises(CredentialError, match="non-public"):
        _put_aksk(svc, endpoint="https://mixed.example-corp.com")
    assert svc.list_credentials() == []


def test_a_declared_internal_store_is_allowed_by_the_deployment(sqlite_engine):
    """The escape hatch is the deployment's, not the document's.

    An internal object store is a legitimate deployment choice, and it is
    declared in the same transport allowlist the fetch road reads — one place,
    so the two cannot disagree about which internal hosts exist.
    """
    svc = SourceCredentialService(
        SourceCredentialRepository(InMemorySqliteDB(sqlite_engine)),
        TokenVault("master-key-material"),
        endpoint_allow_hosts=("store.corp.internal",),
        endpoint_resolver=_fake_dns,
    )
    record = _put_aksk(svc, endpoint="https://store.corp.internal")
    assert record.endpoint == "https://store.corp.internal"


def test_prefixes_are_refused_on_a_signing_credential(service):
    """Not ignored — refused.

    ``allowed_prefixes`` exists to bound the URLs a secret may be presented
    to. An ``oss_aksk`` credential presents its secret to nothing and reads
    its endpoint from its own row, so a prefix here would constrain nothing
    while looking like it constrained something. Silence would let a caller
    believe they had drawn a boundary.
    """
    with pytest.raises(CredentialError, match="allowed_prefixes"):
        _put_aksk(service, allowed_prefixes=["https://objects.example-corp.com/x"])


def test_rotating_from_one_mechanism_to_the_other_clears_the_old_fields(service):
    """A whole-row replace, in both directions: a row that kept the previous
    mechanism's fields would half-describe two mechanisms, and every read would
    report configuration that governs nothing."""
    _put(service)  # header
    assert service.get(name="corp-git").header_name == "PRIVATE-TOKEN"
    _put_aksk(service, name="corp-git")
    rotated = service.get(name="corp-git")
    assert rotated.credential_type == "oss_aksk"
    assert rotated.header_name is None
    assert rotated.access_key_id == _AK

    _put(service, name="corp-git")  # back to header
    back = service.get(name="corp-git")
    assert back.access_key_id is None
    assert back.header_name == "PRIVATE-TOKEN"


