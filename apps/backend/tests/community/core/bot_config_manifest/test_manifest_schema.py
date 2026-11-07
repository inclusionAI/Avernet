"""Schema v1 validation — every refusal in the acceptance criteria, by code.

Two properties run through all of it:

* a refusal **names the offending entry** — asserted structurally by
  :func:`_codes` returning locations alongside codes, and by the assertions on
  ``location`` where the location is the point of the rule;
* ``PUT`` is **all-or-nothing** and reports *every* reason it could determine,
  which is why the multi-fault cases assert a set rather than a first failure.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.schema import (
    MAX_DOCUMENT_BYTES, ManifestNotEncodableError, ManifestTooLargeError,
)
from ._schema_helpers import _accept, _codes, _reject

_DIGEST = "sha256:" + "a" * 64


# ── the shapes that are accepted ────────────────────────────────────────────


def test_a_document_with_only_a_version_is_valid():
    """All three sections are optional (schema §1); declaring nothing is legal."""
    assert _accept("schema_version: 1\n").schema_version == 1


def test_a_full_first_wave_document_is_accepted():
    document = f"""schema_version: 1
manifest:
  mcp:
    - server_code: github
  identity:
    - type: SOUL.md
      content: |
        # Who I am
        Reads ${{HOME}} literally — inline content is not scanned.
    - type: RULES.md
      source:
        protocol: oss
        bucket: cdn
        key: bots/${{BOT_ENV}}/rules.md
        auth: oss-cred
  skills:
    - name: quality-check
      source:
        protocol: oss
        bucket: cdn
        key: skills/qc.zip
        auth: oss-cred
      digest: "{_DIGEST}"
  resources:
    - path: data/kb/
      source:
        protocol: oss
        bucket: cdn
        key: kb.zip
        auth: oss-cred
      unpack: zip
      strip_components: 1
script:
  body: |
    #!/bin/bash
    echo '$(id)' {{token}}
"""
    result = _accept(document)
    assert result.schema_version == 1
    assert result.warnings == ()


def test_an_empty_category_list_is_a_declaration_not_an_omission():
    """``skills: []`` means "no managed skills", which is a thing to accept."""
    _accept("schema_version: 1\nmanifest:\n  skills: []\n")


# ── the document envelope ───────────────────────────────────────────────────


def test_an_unknown_schema_version_is_refused():
    assert "unsupported_schema_version" in _codes("schema_version: 2\n")


def test_a_missing_schema_version_is_refused():
    assert "missing_schema_version" in _codes("manifest:\n  skills: []\n")


def test_malformed_yaml_is_refused_with_the_parser_reason():
    codes = _codes("schema_version: 1\n  bad: [indent\n")
    assert "yaml_parse_error" in codes


def test_an_unknown_top_level_key_is_refused():
    assert "unknown_field" in _codes("schema_version: 1\nmanifsest: {}\n")


def test_a_document_over_the_size_cap_is_refused_before_anything_else():
    """Its own error, not a violation: there is no location to name, and a
    document too large to accept is not one to go on parsing."""
    body = "x" * (MAX_DOCUMENT_BYTES + 1)
    with pytest.raises(ManifestTooLargeError) as excinfo:
        _accept(f"schema_version: 1\n# {body}\n")
    assert excinfo.value.limit_bytes == MAX_DOCUMENT_BYTES


def test_a_document_that_is_not_encodable_utf8_is_refused_as_a_bad_request():
    """A lone surrogate survives JSON and Pydantic's ``str`` and only fails on
    encode; unmapped it would be a 500 for input the caller controls."""
    with pytest.raises(ManifestNotEncodableError):
        _accept("schema_version: 1\n# \ud800\n")


# ── source selection ────────────────────────────────────────────────────────


def test_two_sources_on_one_entry_are_refused():
    document = f"""schema_version: 1
manifest:
  skills:
    - name: qc
      source:
        protocol: oss
        bucket: cdn
        key: qc.zip
        auth: oss-cred
      content: "inline"
      digest: "{_DIGEST}"
"""
    locations = _reject(document)
    assert ("manifest.skills[0]", "multiple_sources") in locations


def test_an_entry_with_no_source_is_refused():
    document = "schema_version: 1\nmanifest:\n  skills:\n    - name: qc\n"
    assert ("manifest.skills[0]", "missing_source") in _reject(document)


def test_from_pointing_at_an_undeclared_source_is_refused():
    document = """schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      from: content
      subpath: soul.md
"""
    assert ("manifest.identity[0].from", "undeclared_source") in _reject(document)


def test_named_and_git_sources_are_accepted_for_the_w7_categories():
    """The flip this fix delivers: a ``from`` reference and an inline git
    source validate for the categories whose entries consume them (skills
    and identity) — the admission gate was the one thing the W7 delivery
    left closed, leaving the whole delivered runtime unreachable."""
    _accept("""schema_version: 1
sources:
  content:
    protocol: git
    url: https://code.example.com/team/content.git
    ref: v1.2.0
manifest:
  identity:
    - type: SOUL.md
      from: content
""")
    _accept("""schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      source:
        protocol: git
        url: https://code.example.com/team/content.git
        ref: v1.2.0
        subpath: soul.md
""")


def test_resources_accepts_named_and_git_sources():
    """Defect D1, closed. ``resources`` was the one fetching category locked to
    inline sources: a ``from:`` was refused even when it named a plain URL
    source, so the declare-once-reference-many mechanism — the stated point of
    named sources — silently excluded workspace files. Every category that
    fetches now reads the same table, and ``resources`` is open on both
    protocols."""
    _accept("""schema_version: 1
sources:
  content:
    protocol: git
    url: https://code.example.com/team/content.git
    ref: v1.2.0
manifest:
  resources:
    - path: data/assets/logo.png
      from: content
      subpath: brand/logo.png
""")

    _accept("""schema_version: 1
manifest:
  resources:
    - path: data/assets/logo.png
      source:
        protocol: git
        url: https://code.example.com/team/content.git
        ref: v1.2.0
        subpath: brand/logo.png
""")


def test_a_resources_directory_over_git_needs_no_unpack():
    """The half of D1 that makes it worth having: a tree arrives as a tree.

    ``unpack`` exists because one HTTPS GET fetches one object, so a directory
    has to travel inside an archive. Git hands the platform a real tree, and
    ``subpath`` selects within it — asking for a packaging step there would be
    asking the caller to configure something that does not happen.
    """
    _accept("""schema_version: 1
sources:
  content:
    protocol: git
    url: https://code.example.com/team/content.git
    ref: v1.2.0
manifest:
  resources:
    - path: data/kb/
      from: content
      subpath: kb/
""")


def test_a_resources_directory_over_oss_still_needs_unpack():
    """The other protocol keeps the rule, for the reason the rule exists."""
    document = """schema_version: 1
manifest:
  resources:
    - path: data/kb/
      source:
        protocol: oss
        bucket: cdn
        key: kb.zip
        auth: oss-cred
"""
    assert ("manifest.resources[0]", "missing_unpack") in _reject(document)


def test_a_declared_but_unreferenced_source_is_a_warning_not_a_refusal():
    """Schema §2.3 allows declare-then-use, and promises the caller is told."""
    document = """schema_version: 1
sources:
  content:
    protocol: oss
    bucket: cdn
    key: content/
    auth: oss-cred
manifest:
  skills: []
"""
    result = _accept(document)
    assert result.warnings and "content" in result.warnings[0]


# ── credentials, digests and fetch options ──────────────────────────────────


def test_a_source_url_with_userinfo_is_refused():
    """The one rule with real teeth: a token in a URL is a secret in a document
    that is stored as written and read back verbatim.

    Asked of a **git** source now — after the bare-URL spelling went away, a
    repository address is the only URL a manifest still writes, and it is as
    capable of carrying userinfo as any other.
    """
    document = """schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      source:
        protocol: git
        url: https://alice:t0ken@code.example.com/team/x.git
        ref: v1
        subpath: soul.md
"""
    assert (
        "manifest.identity[0].source.url",
        "source_url_has_userinfo",
    ) in _reject(document)


def test_auth_on_a_from_entry_is_refused():
    document = """schema_version: 1
sources:
  content:
    protocol: oss
    bucket: cdn
    key: content/
    auth: oss-cred
manifest:
  identity:
    - type: SOUL.md
      from: content
      subpath: soul.md
      auth: corp-token
"""
    assert (
        "manifest.identity[0].auth",
        "auth_on_named_source_entry",
    ) in _reject(document)


def test_auth_on_an_inline_content_entry_is_refused():
    document = """schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      content: "hi"
      auth: corp-token
"""
    assert (
        "manifest.identity[0].auth",
        "auth_on_inline_content",
    ) in _reject(document)


def test_a_digest_on_a_git_source_is_refused():
    """The commit SHA is the digest (schema §2.2); a second pin can disagree."""
    document = f"""schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      source:
        protocol: git
        url: https://code.example.com/team/content.git
        ref: v1.2.0
      digest: "{_DIGEST}"
"""
    assert (
        "manifest.identity[0].digest",
        "digest_on_git_source",
    ) in _reject(document)


def test_a_fetch_option_on_an_inline_content_entry_is_refused():
    document = f"""schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      content: "hi"
      digest: "{_DIGEST}"
      on_fetch_failure: fail
"""
    codes = _codes(document)
    assert codes == {"fetch_field_on_inline_content"}


def test_a_url_skill_without_a_digest_is_refused():
    """A skill is executable content; an unpinned fetch takes whatever is there."""
    document = """schema_version: 1
manifest:
  skills:
    - name: qc
      source:
        protocol: oss
        bucket: cdn
        key: qc.zip
        auth: oss-cred
"""
    assert ("manifest.skills[0]", "missing_digest") in _reject(document)


def test_inline_content_is_accepted_for_identity_but_not_for_skills():
    """Both categories reach the validator through the same source machinery,
    and only identity can mean something by inline text: an identity file IS
    one text body, while a skill is a package — SKILL.md plus the scripts it
    names. Accepting a content-form skill would violate W1's rule (the
    surface never accepts a construct nothing can apply) with W5 the wave
    that has to catch it: nothing materialises a package from inline text."""
    identity = """schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      content: |
        # Who I am
"""
    assert _accept(identity).schema_version == 1

    skill = """schema_version: 1
manifest:
  skills:
    - name: qc
      content: |
        # not a package
"""
    assert ("manifest.skills[0].content", "content_not_a_skill_package") in _reject(skill)


def test_the_dropped_on_fetch_failure_value_is_refused():
    """``skip`` meant "leave this one alone" under per-entry diffing; under
    category overwrite it would mean "delete this one"."""
    document = f"""schema_version: 1
manifest:
  skills:
    - name: qc
      source:
        protocol: oss
        bucket: cdn
        key: qc.zip
        auth: oss-cred
      digest: "{_DIGEST}"
      on_fetch_failure: skip
"""
    assert (
        "manifest.skills[0].on_fetch_failure",
        "invalid_on_fetch_failure",
    ) in _reject(document)


@pytest.mark.parametrize("value", ["keep_last", "fail"])
def test_the_surviving_on_fetch_failure_values_are_accepted(value):
    _accept(
        f"""schema_version: 1
manifest:
  skills:
    - name: qc
      source:
        protocol: oss
        bucket: cdn
        key: qc.zip
        auth: oss-cred
      digest: "{_DIGEST}"
      on_fetch_failure: {value}
"""
    )


# ── the moving-ref selector ─────────────────────────────────────────────────


def test_an_unknown_source_mode_is_refused():
    """A misspelled mode would otherwise land silently on the default, and the
    caller would believe they had pinned something."""
    document = """schema_version: 1
sources:
  assets:
    protocol: git
    url: https://code.example.com/team/assets.git
    mode: strictt
manifest:
  skills: []
"""
    assert ("sources.assets.mode", "invalid_mode") in _reject(document)


@pytest.mark.parametrize("mode", ["strict", "non_strict"])
def test_the_two_source_modes_are_accepted(mode):
    _accept(
        f"""schema_version: 1
sources:
  assets:
    protocol: git
    url: https://code.example.com/team/assets.git
    mode: {mode}
manifest:
  skills: []
"""
    )


def test_mode_is_refused_on_an_object_store_source():
    """``mode`` rules on whether a **ref** may move, and an object store has no
    ref. Accepting it there would be a field that reads as configuration and
    governs nothing — which is the class of defect this change exists to close,
    not a nicety. Refused rather than ignored, and the shape of the value is
    not then also complained about: one mistake, one violation."""
    document = """schema_version: 1
sources:
  assets:
    protocol: oss
    bucket: cdn
    key: assets/
    auth: oss-cred
    mode: strict
manifest:
  skills: []
"""
    codes = _codes(document)
    assert codes == {"field_not_valid_for_protocol"}


def test_ref_is_refused_on_an_object_store_source():
    """The same rule, the field that names the ref rather than ruling on it."""
    document = """schema_version: 1
sources:
  assets:
    protocol: oss
    bucket: cdn
    key: assets/
    auth: oss-cred
    ref: v1.2.0
manifest:
  skills: []
"""
    assert (
        "sources.assets.ref",
        "field_not_valid_for_protocol",
    ) in _reject(document)


# ── the reserved word ───────────────────────────────────────────────────────


def test_apply_once_is_refused_wherever_it_appears():
    document = """schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      content: "hi"
      apply_once: true
"""
    assert (
        "manifest.identity[0].apply_once",
        "reserved_field",
    ) in _reject(document)


def test_apply_once_is_refused_at_any_depth():
    document = """schema_version: 1
sources:
  assets:
    protocol: oss
    bucket: cdn
    key: assets/
    auth: oss-cred
    apply_once: true
manifest:
  skills: []
"""
    assert ("sources.assets.apply_once", "reserved_field") in _reject(document)


# ── substitution ────────────────────────────────────────────────────────────


def test_an_unknown_placeholder_is_refused():
    document = """schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      source:
        protocol: git
        url: https://code.example.com/${BOT_NAME}/x.git
        ref: v1
        subpath: soul.md
"""
    locations = _reject(document)
    assert (
        "manifest.identity[0].source.url",
        "unknown_placeholder",
    ) in locations


def test_the_old_prefix_is_no_longer_a_placeholder():
    """§2.9 renamed ``OCB_*`` to ``BOT_*``; the schema document was corrected in
    the same change, so a document written against the published contract cannot
    be the one that fails here."""
    document = """schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      source:
        protocol: oss
        bucket: cdn
        key: ${OCB_BOT_ID}/soul.md
        auth: oss-cred
"""
    assert "unknown_placeholder" in _codes(document)


@pytest.mark.parametrize(
    "name", ["BOT_ENGINE_TYPE", "BOT_ENV", "BOT_TENANT", "BOT_ARCH"]
)
def test_every_whitelisted_placeholder_is_accepted(name):
    _accept(
        f"""schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      source:
        protocol: oss
        bucket: cdn
        key: ${{{name}}}/soul.md
        auth: oss-cred
"""
    )


def test_inline_content_is_not_scanned_for_placeholders():
    """A knowledge file may legitimately contain ``${...}`` meant for whoever
    reads the file; scanning it would refuse valid documents."""
    _accept(
        """schema_version: 1
manifest:
  identity:
    - type: RULES.md
      content: |
        Use ${WHATEVER_THE_TEAM_MEANS} freely.
"""
    )


def test_bot_arch_resolves_rather_than_being_merely_reserved():
    from agentclaw.community.core.bot_config_manifest.schema import (
        resolve_placeholders,
    )

    resolved = resolve_placeholders(
        "https://x/${BOT_ARCH}/${BOT_ENV}",
        engine_type="openclaw",
        env="dev",
        tenant="t1",
    )
    assert resolved == "https://x/amd64/dev"


def test_bot_id_is_not_a_placeholder():
    """A bot id is minted at creation (a date plus eight random characters), so
    an author preparing content in a git repository cannot know it. Interpolating
    one would mean writing the document after the bot existed and only ever for
    that bot — so ``${BOT_ID}`` is refused like any other unknown name, rather
    than being quietly accepted and substituted."""
    document = """schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      source:
        protocol: oss
        bucket: cdn
        key: bots/${BOT_ID}/soul.md
        auth: oss-cred
"""
    assert "unknown_placeholder" in _codes(document)


# ── resources ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/etc/passwd", "absolute_path"),
        ("../../etc/passwd", "path_traversal"),
        ("data/../../etc/passwd", "path_traversal"),
        ("data//faq.csv", "invalid_path"),
        ("data/./faq.csv", "invalid_path"),
    ],
)
def test_an_escaping_resource_path_is_refused(path, code):
    document = f"""schema_version: 1
manifest:
  resources:
    - path: {path}
      source:
        protocol: oss
        bucket: cdn
        key: x
        auth: oss-cred
"""
    assert ("manifest.resources[0].path", code) in _reject(document)


def test_a_dotdot_prefixed_directory_name_is_not_a_traversal():
    """The traversal check is per segment: ``..config`` is a name, not ``..``. It is still refused, as
    every dot-prefixed name is — a dotfile is the workspace's own and never a resource — and that one
    reason is the only one the validator gives."""
    rejected = _reject(
        """schema_version: 1
manifest:
  resources:
    - path: data/..config
      source:
        protocol: oss
        bucket: cdn
        key: x
        auth: oss-cred
"""
    )
    assert rejected == [("manifest.resources[0].path", "resources_protected_entry")]


def test_a_resource_nested_under_a_directory_entry_is_refused():
    """A directory entry owns its whole subtree, so an entry under it has two
    owners and no defined precedence (schema §3.2)."""
    document = """schema_version: 1
manifest:
  resources:
    - path: data/kb/
      source:
        protocol: oss
        bucket: cdn
        key: kb.zip
        auth: oss-cred
      unpack: zip
    - path: data/kb/extra.csv
      source:
        protocol: oss
        bucket: cdn
        key: extra.csv
        auth: oss-cred
"""
    assert ("manifest.resources", "nested_resource_path") in _reject(document)


def test_a_directory_entry_from_a_url_must_declare_unpack():
    document = """schema_version: 1
manifest:
  resources:
    - path: data/kb/
      source:
        protocol: oss
        bucket: cdn
        key: kb.zip
        auth: oss-cred
"""
    assert ("manifest.resources[0]", "missing_unpack") in _reject(document)
