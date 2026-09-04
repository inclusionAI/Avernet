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

from agentclaw.community.core.bot_config_manifest.capabilities import (
    resolve_capabilities,
)
from agentclaw.community.core.bot_config_manifest.schema import (
    MAX_DOCUMENT_BYTES,
    MAX_INLINE_CONTENT_BYTES,
    MAX_SCRIPT_BYTES,
    ManifestNotEncodableError,
    ManifestTooLargeError,
    ManifestValidationError,
    validate_document,
)

_DIGEST = "sha256:" + "a" * 64


def _is_teclaw(engine: str | None) -> bool:
    return (engine or "").strip().lower() == "teclaw"


def _caps(engine="openclaw", bot_type="personal"):
    return resolve_capabilities(
        active_engine=engine, bot_type=bot_type, is_teclaw=_is_teclaw
    )


def _accept(document: str, **kwargs):
    return validate_document(document, _caps(**kwargs))


def _reject(document: str, **kwargs) -> list[tuple[str, str]]:
    """Return ``[(location, code), …]`` — the refusal, in the caller's terms."""
    with pytest.raises(ManifestValidationError) as excinfo:
        validate_document(document, _caps(**kwargs))
    return [(v.location, v.code) for v in excinfo.value.violations]


def _codes(document: str, **kwargs) -> set[str]:
    return {code for _, code in _reject(document, **kwargs)}


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
      source: https://cdn.example.com/bots/${{BOT_ENV}}/rules.md
  skills:
    - name: quality-check
      source: https://cdn.example.com/skills/qc.zip
      digest: "{_DIGEST}"
  resources:
    - path: data/kb/
      source: https://cdn.example.com/kb.zip
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
      source: https://cdn.example.com/qc.zip
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
    git: https://code.example.com/team/content.git
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
        git: https://code.example.com/team/content.git
        ref: v1.2.0
        subpath: soul.md
""")


def test_named_and_git_sources_are_refused_for_resources_entries():
    """The v1 (category, form) narrowing, at PUT: the resources materialiser
    is still on the URL road W6 shipped, so a resources entry naming a named
    or git source is refused with a reason that names the category — never
    a runtime accident the follow-up would have met as a misleading error."""
    document = """schema_version: 1
sources:
  content:
    git: https://code.example.com/team/content.git
    ref: v1.2.0
manifest:
  resources:
    - path: assets/logo.png
      from: content
"""
    assert ("manifest.resources[0]", "unsupported_source") in _reject(document)

    inline = """schema_version: 1
manifest:
  resources:
    - path: assets/logo.png
      source:
        git: https://code.example.com/team/content.git
        ref: v1.2.0
"""
    assert ("manifest.resources[0]", "unsupported_source") in _reject(inline)


def test_a_declared_but_unreferenced_source_is_a_warning_not_a_refusal():
    """Schema §2.3 allows declare-then-use, and promises the caller is told."""
    document = """schema_version: 1
sources:
  content:
    url: https://cdn.example.com/content/
manifest:
  skills: []
"""
    result = _accept(document)
    assert result.warnings and "content" in result.warnings[0]


# ── credentials, digests and fetch options ──────────────────────────────────


def test_a_source_url_with_userinfo_is_refused():
    """The one rule with real teeth: a token in a URL is a secret in a document
    that is stored as written and read back verbatim."""
    document = """schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      source: https://alice:t0ken@cdn.example.com/soul.md
"""
    assert (
        "manifest.identity[0].source",
        "source_url_has_userinfo",
    ) in _reject(document)


def test_auth_on_a_from_entry_is_refused():
    document = """schema_version: 1
sources:
  content:
    url: https://cdn.example.com/content/
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
        git: https://code.example.com/team/content.git
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
      source: https://cdn.example.com/qc.zip
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
      source: https://cdn.example.com/qc.zip
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
      source: https://cdn.example.com/qc.zip
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
    url: https://cdn.example.com/assets/
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
    url: https://cdn.example.com/assets/
    mode: {mode}
manifest:
  skills: []
"""
    )


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
    url: https://cdn.example.com/assets/
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
      source: https://cdn.example.com/${BOT_NAME}/soul.md
"""
    locations = _reject(document)
    assert ("manifest.identity[0].source", "unknown_placeholder") in locations


def test_the_old_prefix_is_no_longer_a_placeholder():
    """§2.9 renamed ``OCB_*`` to ``BOT_*``; the schema document was corrected in
    the same change, so a document written against the published contract cannot
    be the one that fails here."""
    document = """schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      source: https://cdn.example.com/${OCB_BOT_ID}/soul.md
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
      source: https://cdn.example.com/${{{name}}}/soul.md
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
      source: https://cdn.example.com/bots/${BOT_ID}/soul.md
"""
    assert "unknown_placeholder" in _codes(document)


# ── resources ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/etc/passwd", "absolute_path"),
        ("../../etc/passwd", "path_traversal"),
        ("data/../../etc/passwd", "path_traversal"),
    ],
)
def test_an_escaping_resource_path_is_refused(path, code):
    document = f"""schema_version: 1
manifest:
  resources:
    - path: {path}
      source: https://cdn.example.com/x
"""
    assert ("manifest.resources[0].path", code) in _reject(document)


def test_a_dotdot_prefixed_directory_name_is_not_a_traversal():
    """The check is per segment: ``..config`` is a legitimate directory name."""
    _accept(
        """schema_version: 1
manifest:
  resources:
    - path: data/..config
      source: https://cdn.example.com/x
"""
    )


def test_a_resource_nested_under_a_directory_entry_is_refused():
    """A directory entry owns its whole subtree, so an entry under it has two
    owners and no defined precedence (schema §3.2)."""
    document = """schema_version: 1
manifest:
  resources:
    - path: data/kb/
      source: https://cdn.example.com/kb.zip
      unpack: zip
    - path: data/kb/extra.csv
      source: https://cdn.example.com/extra.csv
"""
    assert ("manifest.resources", "nested_resource_path") in _reject(document)


def test_a_directory_entry_from_a_url_must_declare_unpack():
    document = """schema_version: 1
manifest:
  resources:
    - path: data/kb/
      source: https://cdn.example.com/kb.zip
"""
    assert ("manifest.resources[0]", "missing_unpack") in _reject(document)


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


@pytest.mark.parametrize("reserved", ["MEMORY.md", "IDENTITY.md"])
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


# ── cli_tools ───────────────────────────────────────────────────────────────


def test_two_tools_with_the_same_name_are_refused():
    """One entry is one command is one file (schema §3.7), and ``name`` *is* the
    command name — unique within a bot. Two entries claiming it means one shadows
    the other, and which wins depends on install order."""
    document = f"""schema_version: 1
manifest:
  cli_tools:
    - name: tk
      source: https://cdn.example.com/toolkit.tar.gz
      subpath: bin/tk
      digest: "{_DIGEST}"
    - name: tk
      source: https://cdn.example.com/other.tar.gz
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
      source: https://cdn.example.com/toolkit.tar.gz
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
      source: https://cdn.example.com/toolkit.tar.gz
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
      source: https://cdn.example.com/mycli
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
      source: https://cdn.example.com/mycli
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
      source: https://cdn.example.com/mycli
  engine_config:
    config:
      model: m
  identity:
    - type: MEMORY.md
      content: "hi"
    - type: SOUL.md
      source: https://user:token@cdn.example.com/soul.md
"""
    codes = _codes(document)
    assert {
        "missing_digest",
        "unsupported_category",
        "reserved_identity_type",
        "source_url_has_userinfo",
    } <= codes


def test_the_retired_mcp_config_field_is_refused_rather_than_ignored():
    """``mcp[].config`` was defined as per-bot configuration "the same shape as
    the existing MCP config API" — but that API writes ``ac_user_mcp_config``,
    keyed ``(user_id, server_code)``, and its write fans out via
    ``sync_mcp_detail_to_all_bots``. Applying one bot's manifest would have
    changed MCP configuration for every bot its owner has; its payload is also
    api_key and custom_headers, which design §4.5 keeps out of a manifest
    entirely. A v1 entry is a bare ``server_code``, and the field is refused by
    name rather than silently ignored — same treatment as the retired
    ``cli_tools.entrypoints`` above, for the same reason."""
    document = """schema_version: 1
manifest:
  mcp:
    - server_code: github
      config:
        endpoint_env: PROD
"""
    assert (
        "manifest.mcp[0].config",
        "unknown_field",
    ) in _reject(document)


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
    long_url = "https://content.example/" + "a" * 3000 + ".bin"
    document = f"""schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      source: "{long_url}"
"""
    assert ("manifest.identity[0].source", "source_url_too_long") in _reject(
        document
    )


def test_a_source_url_at_just_under_the_limit_is_accepted():
    prefix, suffix = "https://content.example/", ".bin"
    boundary = prefix + "b" * (2048 - len(prefix) - len(suffix)) + suffix
    assert len(boundary) == 2048
    _accept(
        f"""schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      source: "{boundary}"
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
    for value in ("data/..config", "data/a.md", "top/sub/b.txt", "${BASE}/k.md"):
        assert relative_path_refusal(value) is None, value
