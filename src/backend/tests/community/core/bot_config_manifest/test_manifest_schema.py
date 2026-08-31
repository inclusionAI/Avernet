"""Validation-matrix tests for manifest schema v1 (W1, issue #1469).

Every write-time rejection rule gets a negative test that pins its rule code
and the entry it names, plus golden documents that must stay valid — a matrix
this dense decays silently; the rule codes ARE the contract the 422 body
reports. The ``${OCB_*}`` placeholder list, the digested-skill rule and the
``skip`` withdrawal are pinned here because their issues demand each one.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.manifest_schema import (
    EMPTY_DOCUMENT,
    MAX_CATEGORY_ENTRIES,
    MAX_INLINE_CONTENT_BYTES,
    MAX_SCRIPT_BYTES,
    ManifestDocument,
    ManifestInvalidError,
    parse_document,
    validate_document,
)

DigEST = "sha256:" + "ab" * 32


def _rules(violations) -> list[str]:
    return [v.rule for v in violations]


def _at(violations, entry: str) -> list[str]:
    return [v.rule for v in violations if v.entry == entry]


@pytest.fixture
def valid_document() -> ManifestDocument:
    return parse_document(
        {
            "schema_version": 1,
            "sources": {
                "content": {
                    "git": "https://git.example/team/content.git",
                    "ref": "v1.2.0",
                    "auth": "corp-git",
                }
            },
            "manifest": {
                "identity": [
                    {
                        "type": "SOUL.md",
                        "from": "content",
                        "subpath": "bots/${OCB_BOT_ID}/soul.md",
                    }
                ],
                "skills": [
                    {"name": "quality-check", "from": "content", "subpath": "skills/qc/"},
                    {
                        "name": "order-lookup",
                        "source": "https://artifacts.example/zips/ol-1.4.0.zip",
                        "digest": DigEST,
                    },
                ],
                "resources": [
                    {"path": "data/sales.csv", "source": "https://svc.example/sales.csv"},
                    {
                        "path": "data/kb/",
                        "source": "https://svc.example/kb.zip",
                        "unpack": "zip",
                        "strip_components": 1,
                    },
                    {"path": "data/git-kb/", "from": "content", "subpath": "kb/"},
                ],
                "mcp": [{"server_code": "github"}],
            },
            "script": {"body": "#!/bin/bash\nset -euo pipefail\n"},
        }
    )


# --- golden path ------------------------------------------------------------


def test_the_reference_document_passes(valid_document):
    """A document exercising every category and both source forms is valid."""
    assert validate_document(valid_document) == []


def test_empty_document_is_valid():
    assert validate_document(EMPTY_DOCUMENT) == []


def test_script_body_round_trips_byte_exact():
    """#1469: 含引号、$(id)、{token} 的 script 正文逐字节往返一致。"""
    body = '#!/bin/bash\necho "$(id)" {token} \'quoted\'\n'
    doc = parse_document({"script": {"body": body}})
    assert doc.script.body == body
    again = parse_document(doc.model_dump_json(by_alias=True, exclude_none=True))
    assert again.script.body == body


# --- parse-time shape -------------------------------------------------------


def test_wrong_schema_version_is_rejected():
    with pytest.raises(ManifestInvalidError) as excinfo:
        parse_document({"schema_version": 2})
    assert _at(excinfo.value.violations, "schema_version") == ["schema"]


def test_apply_once_is_rejected_wherever_it_appears():
    """v1 保留字——出现在任何位置都拒绝（extra=forbid 落实,无需枚举位置）。"""
    with pytest.raises(ManifestInvalidError) as excinfo:
        parse_document(
            {"script": {"body": "x", "apply_once": True}}
        )
    assert "script" in str(excinfo.value.violations[0].entry)


def test_engine_ext_is_not_a_field():
    """design §3.4：engine_ext 从 manifest 不可达——它不是一个可写的字段。"""
    with pytest.raises(ManifestInvalidError):
        parse_document({"manifest": {"engine_config": {"config": {}, "engine_ext": 1}}})


def test_invalid_json_reports_document_position():
    with pytest.raises(ManifestInvalidError) as excinfo:
        parse_document("{not json")
    assert _at(excinfo.value.violations, "<document>") == ["document-json"]


# --- source-field placement -------------------------------------------------


def test_multiple_source_kinds_on_one_entry_is_rejected(valid_document):
    doc = parse_document(
        {
            "manifest": {
                "skills": [
                    {
                        "name": "x",
                        "from": "content",
                        "source": "https://svc.example/x.zip",
                    }
                ]
            },
            "sources": {"content": {"git": "https://g/x.git", "ref": "v1"}},
        }
    )
    assert _at(validate_document(doc), "skills[0]") == ["entry-multiple-source"]


def test_entry_without_any_source_is_rejected():
    doc = parse_document({"manifest": {"resources": [{"path": "data/x"}]}})
    assert _at(validate_document(doc), "resources[0]") == ["entry-no-source"]


def test_from_referencing_undclared_source_is_named():
    doc = parse_document({"manifest": {"skills": [{"name": "q", "from": "nobody"}]}})
    violations = validate_document(doc)
    assert _at(violations, "skills[0]") == ["from-undeclared", "skills-digest-required"]


def test_git_source_with_digest_is_rejected():
    doc = parse_document(
        {
            "manifest": {
                "resources": [
                    {
                        "path": "data/kb/",
                        "source": {"git": "https://g/x.git", "ref": "v1"},
                        "digest": DigEST,
                    }
                ]
            }
        }
    )
    assert _at(validate_document(doc), "resources[0]") == ["git-with-digest"]


def test_from_git_named_source_with_digest_is_rejected():
    doc = parse_document(
        {
            "sources": {"content": {"git": "https://g/x.git", "ref": "v1"}},
            "manifest": {
                "identity": [{"type": "SOUL.md", "from": "content", "digest": DigEST}]
            },
        }
    )
    assert _at(validate_document(doc), "identity[0]") == ["git-with-digest"]


def test_auth_on_a_from_entry_is_rejected():
    doc = parse_document(
        {
            "sources": {"content": {"git": "https://g/x.git", "ref": "v1"}},
            "manifest": {
                "identity": [
                    {"type": "SOUL.md", "from": "content", "auth": "corp-git"}
                ]
            },
        }
    )
    assert _at(validate_document(doc), "identity[0]") == ["auth-not-inline-source"]


@pytest.mark.parametrize("field", ["auth", "digest", "on_fetch_failure"])
def test_content_entries_reject_fetch_fields(field):
    entry = {"type": "RULES.md", "content": "# 规范\n"}
    entry[field] = "corp-git" if field == "auth" else (
        DigEST if field == "digest" else "keep_last"
    )
    doc = parse_document({"manifest": {"identity": [entry]}})
    assert _at(validate_document(doc), "identity[0]") == ["content-no-fetch-fields"]


def test_skip_on_fetch_failure_is_rejected():
    """D2 撤销值——覆盖语义下 skip 意为「删掉这一条」,与字面相反,写入即拒。"""
    doc = parse_document(
        {
            "manifest": {
                "resources": [
                    {
                        "path": "data/x",
                        "source": "https://svc.example/x",
                        "on_fetch_failure": "skip",
                    }
                ]
            }
        }
    )
    assert _at(validate_document(doc), "resources[0]") == ["on-failure-value"]


def test_malformed_digest_is_rejected():
    doc = parse_document(
        {"manifest": {"resources": [{"path": "data/x", "source": "https://s/x", "digest": "md5:zz"}]}}
    )
    assert _at(validate_document(doc), "resources[0]") == ["digest-format"]


def test_non_git_skill_without_digest_is_rejected():
    """skill 携带代码——非 git 源没有钉子等于每个 apply 点盲取当时那里有的东西。"""
    doc = parse_document(
        {"manifest": {"skills": [{"name": "q", "source": "https://artifacts.example/q.zip"}]}}
    )
    assert _at(validate_document(doc), "skills[0]") == ["skills-digest-required"]


# --- placeholders ------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "https://svc.example/${TENANT}/x.md"),
        ("subpath", "bots/${BOT_ID}/soul.md"),
    ],
)
def test_unknown_placeholder_is_rejected_wherever_it_lands(field, value):
    entry = {"type": "SOUL.md", "source": "https://svc.example/soul.md"}
    entry[field] = value
    doc = parse_document({"manifest": {"identity": [entry]}})
    assert _at(validate_document(doc), "identity[0]") == ["placeholder-unknown"]


def test_placeholder_in_named_source_is_named():
    doc = parse_document(
        {
            "sources": {"cdn": {"url": "https://cdn.example/${TEAM}/"}},
            "manifest": {
                "skills": [{"name": "q", "from": "cdn", "digest": DigEST}]
            },
        }
    )
    assert _at(validate_document(doc), "sources.cdn") == ["placeholder-unknown"]


def test_whitelisted_placeholders_pass():
    doc = parse_document(
        {
            "manifest": {
                "identity": [
                    {
                        "type": "SOUL.md",
                        "source": "https://svc.example/${OCB_TENANT}/${OCB_BOT_ID}/${OCB_ENV}/${OCB_ENGINE_TYPE}/soul.md",
                    }
                ]
            }
        }
    )
    assert validate_document(doc) == []


# --- resource paths and shapes ---------------------------------------------


@pytest.mark.parametrize(
    ("path", "rule"),
    [
        ("/abs/path", "resource-path-absolute"),
        ("data/../escape", "resource-dotdot"),
        ("", "resource-path-empty"),
    ],
)
def test_bad_resource_paths_are_rejected(path, rule):
    doc = parse_document(
        {"manifest": {"resources": [{"path": path, "source": "https://s/x"}]}}
    )
    assert _at(validate_document(doc), "resources[0]") == [rule]


def test_subpath_traversal_is_rejected():
    doc = parse_document(
        {
            "sources": {"content": {"git": "https://g/x.git", "ref": "v1"}},
            "manifest": {
                "identity": [{"type": "SOUL.md", "from": "content", "subpath": "../secrets/soul.md"}]
            },
        }
    )
    assert _at(validate_document(doc), "identity[0]") == ["subpath-traversal"]


def test_nested_resource_path_is_rejected():
    """目录归 manifest、其内部文件又单独声明的所有权无法定义（#1469）。"""
    doc = parse_document(
        {
            "manifest": {
                "resources": [
                    {"path": "data/kb/", "source": "https://s/kb.zip", "unpack": "zip"},
                    {"path": "data/kb/inner.md", "source": "https://s/inner.md"},
                ]
            }
        }
    )
    assert _at(validate_document(doc), "resources[1]") == ["resource-nested"]


def test_directory_entry_nesting_under_directory_is_rejected():
    doc = parse_document(
        {
            "manifest": {
                "resources": [
                    {"path": "data/kb/", "source": "https://s/kb.zip"},
                    {"path": "data/kb/sub/", "source": "https://s/sub.zip"},
                ]
            }
        }
    )
    assert _at(validate_document(doc), "resources[1]") == ["resource-nested"]


def test_unrelated_resource_paths_do_not_nest():
    """``content-secret``——段边界:同前缀不同目录不是嵌套。"""
    doc = parse_document(
        {
            "manifest": {
                "resources": [
                    {"path": "data/kb/", "source": "https://s/kb.zip"},
                    {"path": "data/kb-secret/x.md", "source": "https://s/x.md"},
                ]
            }
        }
    )
    assert validate_document(doc) == []


def test_unpack_on_file_entry_is_rejected():
    doc = parse_document(
        {"manifest": {"resources": [{"path": "data/x.md", "source": "https://s/x.md", "unpack": "zip"}]}}
    )
    assert _at(validate_document(doc), "resources[0]") == ["unpack-on-file-entry"]


def test_git_directory_entry_rejects_unpack():
    doc = parse_document(
        {
            "sources": {"content": {"git": "https://g/x.git", "ref": "v1"}},
            "manifest": {
                "resources": [
                    {"path": "data/kb/", "from": "content", "subpath": "kb/", "unpack": "zip"}
                ]
            },
        }
    )
    assert _at(validate_document(doc), "resources[0]") == ["git-dir-no-unpack"]


def test_unknown_unpack_kind_is_rejected():
    doc = parse_document(
        {"manifest": {"resources": [{"path": "data/kb/", "source": "https://s/kb.7z", "unpack": "7z"}]}}
    )
    assert _at(validate_document(doc), "resources[0]") == ["unpack-kind"]


def test_directory_entry_cannot_be_inline_content():
    doc = parse_document({"manifest": {"resources": [{"path": "data/kb/", "content": "x"}]}})
    assert _at(validate_document(doc), "resources[0]") == ["resource-dir-content"]


# --- named sources ----------------------------------------------------------


def test_named_source_needs_exactly_one_kind():
    doc = parse_document({"sources": {"orphan": {}}})
    assert _at(validate_document(doc), "sources.orphan") == ["sources-no-kind"]


def test_named_source_with_two_kinds_is_rejected():
    doc = parse_document(
        {"sources": {"x": {"git": "https://g/x.git", "url": "https://u/x", "ref": "v1"}}}
    )
    assert _at(validate_document(doc), "sources.x") == ["sources-multiple-kind"]


def test_named_git_source_requires_ref():
    doc = parse_document({"sources": {"x": {"git": "https://g/x.git"}}})
    assert _at(validate_document(doc), "sources.x") == ["sources-ref-required"]


# --- limits -----------------------------------------------------------------


def test_category_entry_limit_is_enforced():
    skills = [
        {"name": f"s{i}", "source": "https://a.example/s.zip", "digest": DigEST}
        for i in range(MAX_CATEGORY_ENTRIES + 1)
    ]
    doc = parse_document({"manifest": {"skills": skills}})
    assert validate_document(doc)[0].rule == "limit-category-entries"


def test_inline_content_limit_is_enforced():
    doc = parse_document(
        {
            "manifest": {
                "identity": [
                    {"type": "RULES.md", "content": "x" * (MAX_INLINE_CONTENT_BYTES + 1)}
                ]
            }
        }
    )
    assert validate_document(doc)[0].rule == "limit-inline-content"


def test_script_size_shares_the_935_limit():
    body = "x" * (MAX_SCRIPT_BYTES + 1)
    doc = parse_document({"script": {"body": body}})
    assert validate_document(doc)[0].rule == "script-too-large"


# --- full-list behaviour ----------------------------------------------------


def test_every_broken_rule_is_reported_not_just_the_first():
    """PUT 要一份逐条原因列表;第一条错就返回会把它砍成一错一报。"""
    doc = parse_document(
        {
            "sources": {"x": {}},
            "manifest": {
                "skills": [{"name": "q", "from": "nobody"}],
                "resources": [{"path": "/abs", "source": "https://s/x"}],
            },
        }
    )
    rules = _rules(validate_document(doc))
    assert "from-undeclared" in rules
    assert "resource-path-absolute" in rules
    assert "sources-no-kind" in rules

