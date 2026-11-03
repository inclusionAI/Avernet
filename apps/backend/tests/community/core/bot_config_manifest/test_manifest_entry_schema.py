"""Manifest entry types, source selection, and target capability contracts."""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.schema import (
    MAX_INLINE_CONTENT_BYTES, MAX_SCRIPT_BYTES,
    ManifestTooLargeError,
)
from ._schema_helpers import _accept, _codes, _reject

_DIGEST = "sha256:" + "a" * 64


# ── identity ────────────────────────────────────────────────────────────────


def test_an_identity_type_outside_the_engine_set_is_refused():
    document = """schema_version: 1
manifest:
  identity:
    - type: NOTES.md
      content: "hi"
"""
    assert ("manifest.identity[0].type", "invalid_identity_type") in _reject(document)


def test_claude_code_accepts_only_its_own_identity_file():
    document = """schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      content: "hi"
"""
    assert "invalid_identity_type" in _codes(document, engine="claude_code")
    _accept(document.replace("SOUL.md", "CLAUDE.md"), engine="claude_code")


@pytest.mark.parametrize("reserved", ["MEMORY.md"])
def test_a_reserved_identity_file_is_refused_even_though_it_is_a_valid_type(reserved):
    """It passes the vocabulary check and would then never converge: apply is
    guaranteed never to write or remove it."""
    document = f"""schema_version: 1
manifest:
  identity:
    - type: {reserved}
      content: "hi"
"""
    assert (
        "manifest.identity[0].type",
        "reserved_identity_type",
    ) in _reject(document)


@pytest.mark.parametrize("engine", ["openclaw", "teclaw"])
def test_identity_profile_can_be_explicitly_declared_for_native_engines(engine):
    _accept("""schema_version: 1
manifest:
  identity:
    - type: IDENTITY.md
      content: "Expert profile"
""", engine=engine)


# ── cli_tools ───────────────────────────────────────────────────────────────


def test_two_tools_with_the_same_name_are_refused():
    """One entry is one command is one file (schema §3.7), and ``name`` *is* the
    command name — unique within a bot. Two entries claiming it means one shadows
    the other, and which wins depends on install order."""
    document = f"""schema_version: 1
manifest:
  cli_tools:
    - name: tk
      source:
        protocol: oss
        bucket: cdn
        key: toolkit.tar.gz
        auth: oss-cred
      subpath: bin/tk
      digest: "{_DIGEST}"
    - name: tk
      source:
        protocol: oss
        bucket: cdn
        key: other.tar.gz
        auth: oss-cred
      subpath: bin/tk
      digest: "{_DIGEST}"
"""
    assert ("manifest.cli_tools", "duplicate_command_name") in _reject(document)


def test_the_retired_entrypoints_field_is_refused_rather_than_ignored():
    """An earlier draft made an entry "a directory plus a list of files inside it
    to expose". That shape was flattened, and a document written against it is
    refused by name — a silently ignored key is a caller believing they
    configured something."""
    document = f"""schema_version: 1
manifest:
  cli_tools:
    - name: toolkit
      source:
        protocol: oss
        bucket: cdn
        key: toolkit.tar.gz
        auth: oss-cred
      unpack: tar.gz
      digest: "{_DIGEST}"
      entrypoints: [bin/tk, bin/tk-helper]
"""
    assert (
        "manifest.cli_tools[0].entrypoints",
        "unknown_field",
    ) in _reject(document)


def test_the_archive_form_selects_its_one_file_with_subpath():
    """The flattened shape's own spelling: `subpath` names the file in the
    package that becomes the command. Accepted since W9 materialised the
    category."""
    document = f"""schema_version: 1
manifest:
  cli_tools:
    - name: tk
      source:
        protocol: oss
        bucket: cdn
        key: toolkit.tar.gz
        auth: oss-cred
      subpath: bin/tk
      unpack: tar.gz
      digest: "{_DIGEST}"
      version: "0.9.0"
"""
    entry = _accept(document).parsed["manifest"]["cli_tools"][0]
    assert entry["subpath"] == "bin/tk" and entry["unpack"] == "tar.gz"


def test_cli_tools_requires_a_digest():
    document = """schema_version: 1
manifest:
  cli_tools:
    - name: mycli
      source:
        protocol: oss
        bucket: cdn
        key: mycli
        auth: oss-cred
"""
    assert ("manifest.cli_tools[0]", "missing_digest") in _reject(document)


# ── capability gating on the write path ─────────────────────────────────────


@pytest.mark.parametrize("category", ["engine_config"])
def test_a_category_nothing_can_apply_is_refused_with_its_reason(category):
    """``cli_tools`` left this list when W9 materialised it; ``engine_config``
    is what remains of the first-wave gate table."""
    document = f"schema_version: 1\nmanifest:\n  {category}:\n    config:\n      model: m\n"
    assert (f"manifest.{category}", "unsupported_category") in _reject(document)


def test_a_well_formed_cli_tools_document_is_accepted_since_w9():
    """The gate flip: the surface never accepts what nothing can apply, and
    since W9 something can."""
    document = f"""schema_version: 1
manifest:
  cli_tools:
    - name: mycli
      source:
        protocol: oss
        bucket: cdn
        key: mycli
        auth: oss-cred
      digest: "{_DIGEST}"
"""
    assert _accept(document).parsed["manifest"]["cli_tools"][0]["name"] == "mycli"


def test_an_unknown_category_is_refused_rather_than_ignored():
    document = "schema_version: 1\nmanifest:\n  cron: []\n"
    assert ("manifest.cron", "unknown_category") in _reject(document)


def test_engine_ext_can_never_be_written_through_a_manifest():
    document = """schema_version: 1
manifest:
  engine_config:
    config:
      engine_ext: {"anything": 1}
"""
    assert "engine_ext_not_writable" in _codes(document)


def test_a_script_is_refused_for_a_bot_that_cannot_run_one():
    document = "schema_version: 1\nscript:\n  body: |\n    echo hi\n"
    assert ("script", "unsupported_script") in _reject(document, engine="teclaw")


def test_a_script_over_the_startup_script_cap_is_refused():
    body = "x" * (MAX_SCRIPT_BYTES + 1)
    document = f'schema_version: 1\nscript:\n  body: "{body}"\n'
    assert ("script.body", "script_too_large") in _reject(document)


def test_an_oversize_inline_content_block_is_refused_by_the_document_cap():
    """With schema §5's numbers the two caps are the same 64 KiB, so a ``content``
    block big enough to break its own limit has already broken the document's.

    Asserted rather than left implicit: the per-entry check is still there and is
    still right — the two are independent knobs and either could move — but today
    the document cap is what a caller actually meets, and a test claiming
    otherwise would be describing a path no request takes.
    """
    body = "x" * (MAX_INLINE_CONTENT_BYTES + 1)
    document = f'schema_version: 1\nmanifest:\n  identity:\n    - type: SOUL.md\n      content: "{body}"\n'
    with pytest.raises(ManifestTooLargeError):
        _accept(document)


def test_the_per_entry_inline_content_cap_is_enforced_on_its_own_terms():
    """The rule itself, exercised through the validator with a smaller ceiling
    so it is not shadowed by the document cap above."""
    from agentclaw.community.core.bot_config_manifest.schema import entries

    body = "x" * 64
    document = f'schema_version: 1\nmanifest:\n  identity:\n    - type: SOUL.md\n      content: "{body}"\n'
    original = entries.MAX_INLINE_CONTENT_BYTES
    entries.MAX_INLINE_CONTENT_BYTES = 8
    try:
        assert (
            "manifest.identity[0].content",
            "content_too_large",
        ) in _reject(document)
    finally:
        entries.MAX_INLINE_CONTENT_BYTES = original


def test_too_many_entries_in_one_category_is_refused():
    entries = "".join(
        f"    - type: SOUL.md\n      content: \"{i}\"\n" for i in range(51)
    )
    document = f"schema_version: 1\nmanifest:\n  identity:\n{entries}"
    assert ("manifest.identity", "too_many_entries") in _reject(document)


# ── all-or-nothing ──────────────────────────────────────────────────────────


def test_every_reason_is_reported_at_once():
    """Not the first one. A caller fixes their document in one pass, not in a
    queue of resubmissions."""
    document = """schema_version: 1
manifest:
  cli_tools:
    - name: mycli
      source:
        protocol: oss
        bucket: cdn
        key: mycli
        auth: oss-cred
  engine_config:
    config:
      model: m
  identity:
    - type: MEMORY.md
      content: "hi"
    - type: SOUL.md
      source:
        protocol: git
        url: https://user:token@code.example.com/team/x.git
        ref: v1
        subpath: soul.md
"""
    codes = _codes(document)
    assert {
        "missing_digest",
        "unsupported_category",
        "reserved_identity_type",
        "source_url_has_userinfo",
    } <= codes


def test_mcp_config_accepts_the_closed_bot_override_shape():
    document = """schema_version: 1
manifest:
  mcp:
    - server_code: github
      config:
        url: http://mcp.example.test/mcp
        headers:
          X-Project: project-a
        endpoint_env: PRE
        transport_protocol: streamable_http
"""
    accepted = _accept(document)
    assert accepted.parsed["manifest"]["mcp"][0]["config"][
        "transport_protocol"
    ] == "STREAMABLE_HTTP"


def test_mcp_config_refuses_unknown_fields_and_invalid_remote_values():
    document = """schema_version: 1
manifest:
  mcp:
    - server_code: github
      config:
        api_key: secret
        url: relative/mcp
        endpoint_env: DEV
        transport_protocol: WEBSOCKET
        headers:
          X-Trace: |
            ok
            Injected: value
"""
    rejected = set(_reject(document))
    assert ("manifest.mcp[0].config.api_key", "unknown_field") in rejected
    assert ("manifest.mcp[0].config.url", "invalid_mcp_url") in rejected
    assert ("manifest.mcp[0].config.endpoint_env", "invalid_endpoint_env") in rejected
    assert (
        "manifest.mcp[0].config.transport_protocol",
        "invalid_transport_protocol",
    ) in rejected
    assert (
        "manifest.mcp[0].config.headers.X-Trace",
        "invalid_header_value",
    ) in rejected


def test_mcp_config_rejects_newline_in_header_name():
    document = """schema_version: 1
manifest:
  mcp:
    - server_code: github
      config:
        headers:
          "X-Bad\\nInjected": value
"""

    assert "invalid_header_name" in _codes(document)


@pytest.mark.parametrize(
    "url",
    [
        "http://[",
        "https://mcp.example.test/path with space",
    ],
)
def test_mcp_config_rejects_malformed_urls_without_crashing(url):
    document = f"""schema_version: 1
manifest:
  mcp:
    - server_code: github
      config:
        url: {url!r}
"""

    assert "invalid_mcp_url" in _codes(document)


def test_mcp_config_rejects_blank_header_name():
    document = """schema_version: 1
manifest:
  mcp:
    - server_code: github
      config:
        headers:
          "   ": value
"""

    assert "invalid_header_name" in _codes(document)


def test_mcp_config_distinguishes_missing_headers_from_an_explicit_empty_map():
    accepted = _accept(
        "schema_version: 1\nmanifest:\n  mcp:\n"
        "    - server_code: github\n      config:\n        headers: {}\n"
    )
    assert accepted.parsed["manifest"]["mcp"][0]["config"]["headers"] == {}


def test_an_mcp_entry_is_accepted_as_a_bare_server_code():
    """The narrowing removed a field; it did not narrow the category itself."""
    assert _accept("schema_version: 1\nmanifest:\n  mcp:\n    - server_code: github\n")


def test_a_refusal_always_names_a_location():
    document = "schema_version: 1\nmanifest:\n  identity:\n    - type: NOPE.md\n"
    for location, _ in _reject(document):
        assert location


def test_an_oversized_source_url_is_refused_at_put_not_after_a_fetch():
    """The admission-side length cap (schema §5, 2048 chars — the width the
    provenance column stores). Without it a 3000-char source would be
    accepted, fetched in full up to the per-entry cap, and refused only at
    the store: the expensive order, and a document every apply point rejects
    — the exact shape "this surface never accepts something it cannot apply"
    forbids."""
    long_url = "https://code.example.com/" + "a" * 3000 + ".git"
    document = f"""schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      source:
        protocol: git
        url: "{long_url}"
        ref: v1
        subpath: soul.md
"""
    assert (
        "manifest.identity[0].source.url",
        "source_url_too_long",
    ) in _reject(document)


def test_a_source_url_at_just_under_the_limit_is_accepted():
    """The boundary is inclusive — 2048 is the provenance column's width, and
    a URL exactly that long stores without truncation."""
    prefix, suffix = "https://code.example.com/", ".git"
    boundary = prefix + "b" * (2048 - len(prefix) - len(suffix)) + suffix
    assert len(boundary) == 2048
    _accept(
        f"""schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      source:
        protocol: git
        url: "{boundary}"
        ref: v1
        subpath: soul.md
"""
    )


def test_relative_path_refusal_is_the_one_rule_both_sides_ask():
    """The PUT-time predicate, pure, exposed for the apply-time belt.

    The resources materialiser re-asks this rule at apply time (a stored
    document can predate the validator); sharing one function is what
    keeps the belt from being a weaker copy of the schema's rule.
    """
    from agentclaw.community.core.bot_config_manifest.schema._support import (
        relative_path_refusal,
    )

    refused = [
        ("/etc/passwd", "absolute_path"),
        ("~/.ssh/id", "absolute_path"),
        ("C:evil.md", "absolute_path"),
        ("../../etc/passwd", "path_traversal"),
        ("data/../../etc", "path_traversal"),
        ("a\\b.md", "invalid_path"),
        ("a b.md", "invalid_path"),
        ("", "invalid_path"),
    ]
    for value, code in refused:
        refusal = relative_path_refusal(value)
        assert refusal is not None, value
        assert refusal[0] == code, (value, refusal)
        assert refusal[1]
    # A non-string is the belt's own business (declared by the index) — the
    # predicate refuses it as invalid rather than assuming str.
    assert relative_path_refusal(123)[0] == "invalid_path"
    # The legitimate shapes, including the per-segment '..' reading.
    for value in ("data/..config", "data/a.md", "top/sub/b.txt", "${BASE}/k.md", "data/kb/"):
        assert relative_path_refusal(value) is None, value
    # Normalisation is not this rule's: a fetch key or subpath is an address in
    # another namespace (an object-store key may contain ``//``).
    for value in ("a//b", "./skills/foo"):
        assert relative_path_refusal(value) is None, value


def test_resource_path_refusal_adds_normalisation_for_the_paths_the_engine_writes():
    """The resources schema check, the compiler and the desired-state model ask this one, so a path the
    schema accepts never fails the model: an accepted document cannot crash the compile."""
    from agentclaw.community.core.apply_engine.paths import resource_path_refusal

    for value in ("data//faq.csv", "data/./x", "data/kb//", "."):
        refusal = resource_path_refusal(value)
        assert refusal is not None and refusal[0] == "invalid_path", value
        assert "not normalised" in refusal[1]
    assert resource_path_refusal("../x")[0] == "path_traversal"
    for value in ("data/a.md", "data/kb/", "data/..config"):
        assert resource_path_refusal(value) is None, value


# ── the rules that must survive the protocol axis ──────────────────────────
#
# The source declaration is parsed by a new module now, and these are the rules
# that would be silently lost if the wrapper stopped contributing them: the pure
# parser knows nothing about https, userinfo, URL length or workspace paths.


def test_userinfo_is_still_refused_on_a_declared_source():
    """The rule with teeth (schema §2.1). A document is stored as written, read
    back verbatim by `GET`, and recorded as provenance — which is exactly what
    the encrypted credential store exists to avoid. Refused, never redacted:
    redaction cannot un-store what was already accepted somewhere else."""
    document = """schema_version: 1
sources:
  s:
    protocol: git
    url: https://user:token@code.example.com/team/content.git
manifest:
  identity:
    - type: SOUL.md
      from: s
      subpath: soul.md
"""
    assert ("sources.s.url", "source_url_has_userinfo") in _reject(document)


def test_a_non_https_source_is_still_refused():
    document = """schema_version: 1
sources:
  s:
    protocol: git
    url: http://code.example.com/team/x.git
    ref: v1
    subpath: a.md
manifest:
  identity:
    - type: SOUL.md
      from: s
"""
    assert ("sources.s.url", "invalid_source") in _reject(document)


@pytest.mark.parametrize(
    ("where", "document"),
    [
        (
            "sources.s.subpath",
            """schema_version: 1
sources:
  s:
    protocol: git
    url: https://code.example.com/team/content.git
    subpath: ../../etc
manifest:
  identity:
    - type: SOUL.md
      from: s
""",
        ),
        (
            "manifest.identity[0].subpath",
            """schema_version: 1
sources:
  s:
    protocol: git
    url: https://code.example.com/team/content.git
manifest:
  identity:
    - type: SOUL.md
      from: s
      subpath: ../../etc/shadow
""",
        ),
    ],
)
def test_traversal_is_refused_on_both_halves_of_a_subpath(where, document):
    """Both halves compose into one path at fetch time, so both are checked
    here and the composition is checked again there — by the same predicate,
    never a second weaker one."""
    assert (where, "path_traversal") in _reject(document)


def test_a_source_that_fails_to_parse_is_one_problem_not_two():
    """An entry naming a source whose declaration was refused must not ALSO be
    reported as naming an undeclared source. One mistake, one violation — the
    caller fixes the source, not the reference."""
    document = """schema_version: 1
sources:
  s:
    url: https://cdn.example.com/a.md
manifest:
  identity:
    - type: SOUL.md
      from: s
"""
    assert _codes(document) == {"missing_protocol"}


def test_the_reserved_word_is_still_swept_inside_a_source():
    document = """schema_version: 1
sources:
  s:
    protocol: git
    url: https://code.example.com/team/content.git
    apply_once: true
manifest:
  identity:
    - type: SOUL.md
      from: s
      subpath: soul.md
"""
    assert ("sources.s.apply_once", "reserved_field") in _reject(document)


def test_an_unknown_key_on_a_source_is_refused_not_ignored():
    document = """schema_version: 1
sources:
  s:
    protocol: oss
    bucket: cdn
    key: a.md
    auth: oss-cred
    nonsense: 1
manifest:
  identity:
    - type: SOUL.md
      from: s
"""
    assert ("sources.s.nonsense", "unknown_field") in _reject(document)


# ── subpath must have something to select within ───────────────────────────
#
# Found by review. On `git` a subpath always means something — the source
# delivers a tree. On `oss` it selects a member of an ARCHIVE, so where no
# archive is unpacked it configures nothing, which is the exact failure this
# change exists to remove. Refused at PUT rather than ignored at apply.

_OSS_SOURCE = """schema_version: 1
sources:
  artifacts:
    protocol: oss
    bucket: artifacts
    key: tools/pkg.zip
    auth: oss-cred
manifest:
"""


def test_subpath_on_an_oss_resources_file_entry_is_refused():
    """A file entry unpacks nothing, so the whole object would land at `path`
    and the subpath would silently do nothing."""
    document = _OSS_SOURCE + """  resources:
    - path: data/pricing.csv
      from: artifacts
      subpath: reference/pricing.csv
"""
    assert (
        "manifest.resources[0].subpath",
        "subpath_without_archive",
    ) in _reject(document)


def test_subpath_on_an_oss_identity_entry_is_refused():
    """An identity file is one text body and is never an archive."""
    document = _OSS_SOURCE + """  identity:
    - type: SOUL.md
      from: artifacts
      subpath: bots/soul.md
"""
    assert (
        "manifest.identity[0].subpath",
        "subpath_without_archive",
    ) in _reject(document)


def test_subpath_on_an_oss_cli_tool_without_unpack_is_refused_at_put():
    """The acquisition refused this already — but only once the apply was
    running, which is the surface accepting what apply rejects."""
    document = _OSS_SOURCE + f"""  cli_tools:
    - name: rg
      from: artifacts
      subpath: bin/rg
      digest: "{_DIGEST}"
"""
    assert (
        "manifest.cli_tools[0].subpath",
        "subpath_without_archive",
    ) in _reject(document)


def test_subpath_is_accepted_wherever_an_archive_is_actually_unpacked():
    """The other half of the rule, so it narrows nothing it should not."""
    # cli_tools with an explicit unpack
    _accept(_OSS_SOURCE + f"""  cli_tools:
    - name: rg
      from: artifacts
      subpath: bin/rg
      unpack: zip
      digest: "{_DIGEST}"
""")
    # skills always unpack — a skill IS a package
    _accept(_OSS_SOURCE + f"""  skills:
    - name: qc
      from: artifacts
      subpath: quality-check/
      digest: "{_DIGEST}"
""")
    # a resources directory entry over oss requires unpack, so it has one
    _accept(_OSS_SOURCE + """  resources:
    - path: data/kb/
      from: artifacts
      subpath: kb/
      unpack: zip
""")


@pytest.mark.parametrize(
    ("category", "entry"),
    [
        ("identity", "    - type: SOUL.md\n      from: repo\n      subpath: soul.md\n"),
        (
            "resources",
            "    - path: data/faq.csv\n      from: repo\n      subpath: kb/faq.csv\n",
        ),
        ("skills", "    - name: qc\n      from: repo\n      subpath: skills/qc/\n"),
        ("cli_tools", "    - name: rg\n      from: repo\n      subpath: bin/rg\n"),
    ],
)
def test_subpath_is_always_meaningful_on_a_git_source(category, entry):
    """Git delivers a tree, so there is always something to select within —
    in every category, with no archive and no `unpack` anywhere."""
    _accept(
        "schema_version: 1\n"
        "sources:\n"
        "  repo:\n"
        "    protocol: git\n"
        "    url: https://code.example.com/team/content.git\n"
        "    ref: v1.2.0\n"
        f"manifest:\n  {category}:\n{entry}"
    )


def test_one_mistake_on_a_source_field_is_one_violation():
    """Absence and malformedness are different mistakes with different fixes,
    and each gets exactly one message.

    The required-field check used to fire alongside the per-field one, so an
    empty ``url`` produced both "must declare 'url'" and "'url' must be a
    non-empty string" — and for a wrong-typed value the first was actively
    false, since the field *is* declared. Same anti-duplication rule the
    misplaced-key path already followed.
    """
    from agentclaw.community.core.bot_config_manifest.schema.sources import (
        parse_source,
    )

    for raw, expected in [
        ({"protocol": "git", "url": ""}, "non-empty"),
        ({"protocol": "git", "url": 123}, "non-empty"),
        ({"protocol": "git"}, "must declare"),
        # ``auth`` has its own wording; what is pinned is the count.
        ({"protocol": "oss", "bucket": "b", "auth": ""}, "credential"),
        ({"protocol": "oss", "bucket": "b"}, "must declare"),
    ]:
        decl, violations = parse_source(raw)
        assert decl is None
        assert len(violations) == 1, (raw, [v.message for v in violations])
        assert expected in violations[0].message, (raw, violations[0].message)


def test_resources_may_not_name_a_protected_workspace_entry():
    """The workspace root is the resources root; a path naming a protected entry (an identity file, the
    skills tree, a dotfile) is refused at PUT, because converging resources clears the root and those
    entries belong to other constructs. Any other workspace-relative path is a resource."""
    outside = """schema_version: 1
manifest:
  resources:
    - path: SOUL.md
      content: hi
    - path: skills/tidy/SKILL.md
      content: hi
    - path: notes/.env
      content: hi
    - path: notes/hello.txt
      content: hi
"""
    rejected = _reject(outside)
    assert ("manifest.resources[0].path", "resources_protected_entry") in rejected
    assert ("manifest.resources[1].path", "resources_protected_entry") in rejected
    assert ("manifest.resources[2].path", "resources_protected_entry") in rejected
    assert not any(code == "resources_protected_entry" and "[3]" in loc for loc, code in rejected)
