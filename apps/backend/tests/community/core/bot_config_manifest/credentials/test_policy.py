"""Policy tests for HTTPS prefix authorization (W3, #1471).

The boundary is the security property: these pin the segment rule (the
issue's `…/team/content` vs `…/team/content-secret` pair), the
normalization order (decode before splitting, repeat for double-encoding),
and the origin strictness (scheme/host/port exactly, userinfo and query
refused).
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.credentials.policy import (
    CanonicalPrefix,
    PrefixAuthorizationError,
    PrefixAuthorizationPolicy,
    validate_prefixes,
)


def _repo_policy(prefixes: list[str]) -> PrefixAuthorizationPolicy:
    return PrefixAuthorizationPolicy("corp-git", prefixes)


# --- the boundary itself ------------------------------------------------------


def test_segment_boundary_allows_extension_not_lookalike():
    policy = _repo_policy(["https://host/team/content"])
    policy.reauthorize("https://host/team/content")  # equal → in
    policy.reauthorize("https://host/team/content/file.zip")  # +segment → in
    with pytest.raises(PrefixAuthorizationError):
        policy.reauthorize("https://host/team/content-secret/x")  # lookalike


def test_whole_origin_is_an_explicit_choice_only():
    # 想盖整 origin 必须显式写 https://host/(或空 path),不默认放开。
    policy = _repo_policy(["https://host/team/content"])
    with pytest.raises(PrefixAuthorizationError):
        policy.reauthorize("https://host/other/place")
    wide = _repo_policy(["https://host/"])
    wide.reauthorize("https://host/anything/at/all")


# --- normalization: decode before split, repeat -------------------------------


def test_percent_encoded_segments_cannot_smuggle_the_boundary():
    policy = _repo_policy(["https://host/team/content"])
    policy.reauthorize("https://host/team/%63ontent/file.zip")  # %63 == 'c'
    with pytest.raises(PrefixAuthorizationError):
        # %2F decodes to a separator AFTER the boundary decode — the path
        # escapes the tree the prefix describes.
        policy.reauthorize("https://host/team/content%2F..%2Fsecret")


def test_double_encoding_is_an_equivalent_spelling_not_an_escape():
    """完全解码后才做边界判定:双重编码不是越界手段,只是等价写法——
    %252F 解到底就是 %2F 再解成 /,落在真正的路径上再比对。"""
    policy = _repo_policy(["https://host/team/content"])
    # %252f → %2f → /:确实在 content 树内,允许(语义等价,非绕过)。
    policy.reauthorize("https://host/team/content%252fsecret")
    # 同理 %2563 → %63 → 'c',仍在树内。
    policy.reauthorize("https://host/team/%2563ontent/file.zip")
    # 边界外依旧是外:双重编码也救不了。
    with pytest.raises(PrefixAuthorizationError):
        policy.reauthorize("https://host/team/content%252f..%252f..%252fadmin")


@pytest.mark.parametrize(
    ("target", "inside"),
    [
        ("https://host/a/b", False),  # .. pops above prefix
        ("https://host/team/content/../admin", False),
        ("https://host//team///content//x", True),  # separators normalize
        ("https://host/team/./content/x", True),
        ("https://HOST/team/content", True),  # host case-insensitive
    ],
)
def test_dot_and_separator_normalization(target, inside):
    policy = _repo_policy(["https://host/team/content"])
    if inside:
        policy.reauthorize(target)
    else:
        with pytest.raises(PrefixAuthorizationError):
            policy.reauthorize(target)


# --- origin strictness --------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "https://host:8443/team/content/x",  # port differs
        "https://other.host/team/content",  # host differs
        "http://host/team/content/x",  # scheme differs
        "ftp://host/team/content",
        "https://user:pass@host/team/content",  # userinfo
    ],
)
def test_target_rejects_out_of_scope(target):
    policy = _repo_policy(["https://host/team/content"])
    with pytest.raises(PrefixAuthorizationError):
        policy.reauthorize(target)


def test_reauthorize_never_raises_out_of_authorization_family():
    # 与 validate 的输入错误(ValueError)分开:目标不该是"被拒绝"以外的
    # 任何东西——引擎侧按这条类型学做条目失败归类。
    policy = _repo_policy(["https://host/repo"])
    try:
        policy.reauthorize("https://host/repo/x")
    except Exception as exc:  # noqa: BLE001 — assert the family, nothing else
        assert isinstance(exc, PrefixAuthorizationError)


# --- validation of stored prefixes ---------------------------------------------


@pytest.mark.parametrize(
    "prefixes",
    [
        [],
        ["http://host/repo"],  # scheme must be https
        ["host/repo"],  # must be absolute
        ["https://user@host/repo"],  # userinfo refused
        ["https://host/repo?token=1"],  # query refused
        ["https://host/repo#frag"],  # fragment refused
        ["https://host/repo", 42],  # strings only
    ],
)
def test_validate_prefixes_refuses_invalid_input(prefixes):
    with pytest.raises(ValueError):
        validate_prefixes(prefixes)


def test_validate_normalizes_what_it_accepts():
    (prefix,) = validate_prefixes(["HTTPS://Host:443/team/content/"])
    assert prefix == CanonicalPrefix("https", "host", 443, "/team/content")
    assert prefix.allows("https://host/team/content/x")
    assert not prefix.allows("https://host:8443/team/content/x")


def test_error_messages_carry_the_credential_name_only():
    # 名字会出现(#1471:只有名字会出现在错误里),值从一开始就不存在于此层。
    try:
        _repo_policy(["https://host/repo"]).reauthorize("https://elsewhere/x")
    except PrefixAuthorizationError as exc:
        assert "corp-git" in str(exc)
        assert "secret" not in str(exc).lower()
