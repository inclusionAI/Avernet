"""``splice_script_section``: the section changes, nothing else does (W8 Task 18)."""
from __future__ import annotations

import pytest
import yaml

from agentclaw.community.core.bot_config_manifest.schema.splice import splice_script_section
from agentclaw.community.core.bot_config_manifest.schema.violations import (
    ManifestValidationError,
)

_DOC = (
    "# my bot\n"
    "schema_version: 1\n"
    "sources:\n"
    "  kb:\n"
    "    url: https://example.test/kb  # the wiki\n"
    "\n"
    "script:\n"
    "  body: |\n"
    "    #!/bin/bash\n"
    "    echo old\n"
    "\n"
    "manifest:\n"
    "  identity: []\n"
)
_WITHOUT = (
    "# my bot\n"
    "schema_version: 1\n"
    "sources:\n"
    "  kb:\n"
    "    url: https://example.test/kb  # the wiki\n"
    "\n"
    "manifest:\n"
    "  identity: []\n"
)


def _body_of(document: str):
    return yaml.safe_load(document).get("script", {}).get("body")


def _outside_script(document: str) -> str:
    return splice_script_section(document, None)


@pytest.mark.parametrize(
    "body",
    [
        "echo 'hi' \"there\"\n",
        "echo '$(id)' \"EOF\" {token}\n",
        "  leading spaces\nsecond\n",
        "no trailing newline",
        "two trailing\n\n",
        "\ttabbed\n",
        "crlf line\r\nnext\r\n",
        "",
        "#!/bin/bash\nset -e\n\nexport X=${HOME}/bin\n",
    ],
    ids=["quotes", "shell", "leading-spaces", "no-newline", "two-newlines", "tab", "crlf", "empty", "blank-line"],
)
def test_the_body_round_trips_and_the_rest_is_byte_identical(body: str) -> None:
    replaced = splice_script_section(_DOC, body)
    assert _body_of(replaced) == body
    assert _outside_script(replaced) == _outside_script(_DOC)

    appended = splice_script_section(_WITHOUT, body)
    assert _body_of(appended) == body
    assert appended.startswith(_WITHOUT)
    assert _outside_script(appended) == _WITHOUT


def test_replace_keeps_every_byte_outside_the_section() -> None:
    replaced = splice_script_section(_DOC, "echo new\n")
    assert replaced == (
        "# my bot\n"
        "schema_version: 1\n"
        "sources:\n"
        "  kb:\n"
        "    url: https://example.test/kb  # the wiki\n"
        "\n"
        "script:\n"
        "  body: |\n"
        "    echo new\n"
        "\n"
        "manifest:\n"
        "  identity: []\n"
    )


def test_remove_drops_the_section_and_keeps_the_spacing() -> None:
    assert splice_script_section(_DOC, None) == _WITHOUT
    assert splice_script_section(_WITHOUT, None) == _WITHOUT


def test_a_section_at_the_end_of_the_document() -> None:
    doc = "schema_version: 1\nscript:\n  body: old\n"
    assert splice_script_section(doc, None) == "schema_version: 1\n"
    assert _body_of(splice_script_section(doc, "new\n")) == "new\n"


def test_a_column_zero_comment_ends_the_section() -> None:
    doc = "schema_version: 1\nscript:\n  body: old\n# trailing comment\n"
    assert splice_script_section(doc, None) == "schema_version: 1\n# trailing comment\n"


def test_an_unparseable_document_is_refused() -> None:
    with pytest.raises(ManifestValidationError) as caught:
        splice_script_section("script: [unclosed\n", "x")
    assert caught.value.violations[0].code == "invalid_yaml"


def test_the_quoted_fallback_is_used_when_a_block_cannot_read_back() -> None:
    # A CRLF body cannot survive a literal block (YAML folds line breaks), so
    # the JSON-quoted rendering carries it.
    spliced = splice_script_section(_WITHOUT, "a\r\nb")
    assert 'body: "a\\r\\nb"' in spliced
    assert _body_of(spliced) == "a\r\nb"


@pytest.mark.parametrize(
    "document",
    [
        'schema_version: 1\n"script":\n  body: old\n',
        "schema_version: 1\n'script':\n  body: old\n",
        "\ufeffscript:\n  body: old\nschema_version: 1\n",
    ],
    ids=["double-quoted", "single-quoted", "bom"],
)
def test_a_quoted_or_bom_prefixed_key_is_the_same_section(document: str) -> None:
    replaced = splice_script_section(document, "new\n")
    parsed = yaml.safe_load(replaced)
    assert parsed["script"]["body"] == "new\n"
    assert replaced.count("script") == 1, replaced
    assert "script" not in yaml.safe_load(splice_script_section(document, None))


def test_a_script_key_the_splice_cannot_locate_is_refused_not_duplicated() -> None:
    explicit = "schema_version: 1\n? script\n:\n  body: old\n"
    with pytest.raises(ManifestValidationError) as caught:
        splice_script_section(explicit, "new\n")
    assert caught.value.violations[0].code == "invalid_script"
    with pytest.raises(ManifestValidationError):
        splice_script_section(explicit, None)


def test_removal_refuses_when_a_second_declaration_would_survive() -> None:
    duplicate = "schema_version: 1\nscript:\n  body: a\nscript:\n  body: b\n"
    with pytest.raises(ManifestValidationError):
        splice_script_section(duplicate, None)
