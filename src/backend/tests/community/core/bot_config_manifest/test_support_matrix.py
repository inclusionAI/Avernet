"""The support matrix's own guarantees — the ones every other rule leans on.

These do not test *behaviour*; they test that the table cannot rot. Every other
test in this feature asks "does the surface agree with the matrix", and that
question is only worth asking while the matrix is exhaustive, non-empty in its
reasons, and impossible to extend by accident.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest import support_matrix
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
)
from agentclaw.community.core.bot_config_manifest.support_matrix import (
    ARCHIVE_FIELDS,
    ARCHIVE_FIELDS_BY_KIND,
    DIGEST_REQUIRED,
    MATRIX,
    SourceKind,
    archive_field_refusal,
    digest_required,
    refusal_for,
    supports,
)

_ALL_CELLS = [(c, k) for c in ManifestCategory for k in SourceKind]


def test_every_cell_of_the_cartesian_product_has_a_verdict():
    """The point of the table: no combination resolves to "nobody said"."""
    assert set(MATRIX) == set(_ALL_CELLS)
    assert len(MATRIX) == len(ManifestCategory) * len(SourceKind)


def test_a_category_added_without_a_verdict_fails_at_import():
    """The guard that makes exhaustiveness self-maintaining.

    Simulated rather than described: drop a cell from the verdict table and
    rebuild, which is exactly what adding a category to the enum does to every
    protocol column at once.
    """
    verdicts = dict(support_matrix._VERDICTS)
    del verdicts[(ManifestCategory.SKILLS, SourceKind.GIT)]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(support_matrix, "_VERDICTS", verdicts)
        with pytest.raises(KeyError):
            support_matrix._build_matrix()


def test_a_verdict_for_a_cell_that_no_longer_exists_is_refused():
    """The other direction: a leftover row governing nothing is a table that
    has stopped describing the code, and reads as if it still does."""
    verdicts = dict(support_matrix._VERDICTS)
    verdicts[("retired_category", SourceKind.GIT)] = None
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(support_matrix, "_VERDICTS", verdicts)
        with pytest.raises(RuntimeError, match="no longer exist"):
            support_matrix._build_matrix()


@pytest.mark.parametrize(("category", "kind"), _ALL_CELLS)
def test_a_refusal_carries_a_code_and_a_reason(
    category: ManifestCategory, kind: SourceKind
):
    """A cell is supported, or it says why not in words a caller can act on.

    An empty reason is the failure this guards: the refusal still refuses, and
    the caller is told nothing about which of the two axes to change.
    """
    refusal = refusal_for(category, kind)
    assert supports(category, kind) is (refusal is None)
    if refusal is None:
        return
    assert refusal.code and refusal.code.strip() == refusal.code
    assert len(refusal.reason) > 30, refusal.reason
    # Both halves of the pair are nameable from the message, which is what
    # makes it actionable rather than merely present.
    assert category.value in refusal.reason or kind.value in refusal.reason


def test_the_matrix_is_the_contract_the_spec_publishes():
    """The table as the spec's own matrix section states it, cell for cell.

    Deliberately a second, literal transcription rather than a loop over the
    module's data: a test that re-derived the answer from ``MATRIX`` would pass
    for any table at all, and this is the one place the *intended* contract is
    written down independently of the implementation.
    """
    supported = {
        (ManifestCategory.IDENTITY, SourceKind.CONTENT),
        (ManifestCategory.IDENTITY, SourceKind.OSS),
        (ManifestCategory.IDENTITY, SourceKind.GIT),
        (ManifestCategory.RESOURCES, SourceKind.CONTENT),
        (ManifestCategory.RESOURCES, SourceKind.OSS),
        (ManifestCategory.RESOURCES, SourceKind.GIT),
        (ManifestCategory.SKILLS, SourceKind.OSS),
        (ManifestCategory.SKILLS, SourceKind.GIT),
        (ManifestCategory.CLI_TOOLS, SourceKind.OSS),
        (ManifestCategory.CLI_TOOLS, SourceKind.GIT),
    }
    assert {cell for cell in _ALL_CELLS if supports(*cell)} == supported


def test_digest_is_required_exactly_where_bytes_are_executable_and_unpinned():
    """skills and cli_tools over ``oss``, and nowhere else.

    Git's absence is the D5 fix expressed as data: a commit SHA is already a
    recorded content identity, and it is the same SHA whether the source was
    written inline or reached by ``from:``.
    """
    assert DIGEST_REQUIRED == {
        (ManifestCategory.SKILLS, SourceKind.OSS),
        (ManifestCategory.CLI_TOOLS, SourceKind.OSS),
    }
    assert not digest_required(ManifestCategory.CLI_TOOLS, SourceKind.GIT)
    assert not digest_required(ManifestCategory.SKILLS, SourceKind.GIT)
    # Every cell that requires a digest is a cell that is supported at all —
    # a mandatory field on a refused combination is unreachable vocabulary.
    for cell in DIGEST_REQUIRED:
        assert supports(*cell), cell


def test_archive_fields_belong_to_oss_and_are_refused_elsewhere():
    assert ARCHIVE_FIELDS_BY_KIND[SourceKind.OSS] == {"unpack", "strip_components"}
    assert ARCHIVE_FIELDS == {"unpack", "strip_components"}
    for field in ARCHIVE_FIELDS:
        assert archive_field_refusal(SourceKind.OSS, field) is None
        for kind in (SourceKind.GIT, SourceKind.CONTENT):
            refusal = archive_field_refusal(kind, field)
            assert refusal is not None
            # Names the field AND the protocol: without both, the caller reads
            # it as "your value is wrong" and edits the value.
            assert field in refusal
        assert "git source" in archive_field_refusal(SourceKind.GIT, field)


def test_the_matrix_cannot_be_edited_through_its_public_handle():
    """It is read by two subsystems on every request; a writable mapping is a
    module-global one of them could mutate for the lifetime of the process."""
    with pytest.raises(TypeError):
        MATRIX[(ManifestCategory.MCP, SourceKind.GIT)] = None  # type: ignore[index]
    with pytest.raises(TypeError):
        ARCHIVE_FIELDS_BY_KIND[SourceKind.GIT] = frozenset()  # type: ignore[index]
