"""The two compilers agree: a Manifest document and the revision it records compile to the same desired state.

``api-contracts/genome/v1/examples/manifest-v1/`` holds, per case, the
document (M) and the revision apply-then-capture yields from it. The
round-trip the contract promises (``manifest-v1-compatibility.md``) is that
delivering the revision gives the bot that applying M gave. Both roads now
end in one ``DesiredState``, so the promise is a comparison: the Manifest
compiler's state for M, over the fixture sources, must equal the genome
compiler's state for the revision, construct by construct.

The reading of the two totality rules is the comparison's own: a category M
leaves out is undeclared on the Manifest side (apply leaves the bot's state)
and declared empty on the genome side (the revision is total and was
captured from an empty bot), so an undeclared Manifest construct matches an
empty genome declaration; ``cli_tools`` and ``engine_config`` are never in
a revision and are not compared. Fetch provenance (``note``) is stripped,
and a category compares as a set, since a revision keeps its entries sorted by key
and a document keeps its author's order.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentclaw.community.core.apply_engine.capabilities import resolve_capabilities
from agentclaw.community.core.apply_engine.constructs import ApplyConstruct, ManifestCategory, ManifestSection
from agentclaw.community.core.apply_engine.desired_state import Declaration, DesiredState
from agentclaw.community.core.bot_config_manifest.compiler import ManifestCompiler
from agentclaw.community.core.bot_config_manifest.fetch.git_source import GitCheckout, GitSourceClient
from agentclaw.community.core.bot_config_manifest.schema.validator import validate_document
from agentclaw.community.core.bot_config_manifest.sources.source_resolver import DeclaredSourceResolver
from agentclaw.community.core.bot_genome.compile import compile_spec
from agentclaw.community.core.bot_genome.spec_models import GenomeSpec
from tests.community.core.apply_engine._fakes import (
    FakeManifestContent,
    FakeObjectCredentials,
    FakeObjectStore,
    OSS_ACCESS_KEY_ID,
    make_context,
    real_validator,
)
from tests.community.core.bot_genome.example_blobs import EXAMPLES, BlobDir

CASES_DIR = EXAMPLES / "manifest-v1"
SOURCES = CASES_DIR / "sources"
CASES = sorted(p for p in CASES_DIR.iterdir() if (p / "revision.json").exists())
#: The objects the example documents name, and the fixture file each serves.
OBJECTS = {
    ("artifacts", "tools/skills/order-lookup-1.4.0.zip"): "oss-artifacts-order-lookup-1.4.0.zip",
    ("artifacts", "tools/shopctl/2.3.0/shopctl-linux-amd64"): "oss-artifacts-shopctl-linux-amd64",
    ("cms-assets", "kb/knowledge-base.zip"): "oss-cms-assets-knowledge-base.zip",
}
COMPARED: tuple[ApplyConstruct, ...] = (
    ManifestSection.SCRIPT,
    ManifestCategory.IDENTITY,
    ManifestCategory.RESOURCES,
    ManifestCategory.SKILLS,
    ManifestCategory.MCP,
)


class _FixtureGit(GitSourceClient):
    """Serves a copy of the ``sources/git-content`` tree as the checkout of every ref.

    A copy, in a temporary directory: the source session owns a checkout's
    root and removes it when the compile closes, exactly as it removes a real
    shallow clone.
    """

    def fetch(self, spec, *, headers=None) -> GitCheckout:
        root = Path(tempfile.mkdtemp(prefix="equivalence-git-")) / "content"
        shutil.copytree(SOURCES / "git-content", root)
        members = tuple(
            ("100644", str(path.relative_to(root)), path.stat().st_size) for path in sorted(root.rglob("*")) if path.is_file()
        )
        return GitCheckout(root=root, sha="0" * 40, url=spec.url, ref=spec.ref, members=members, subpath=spec.subpath)


class _Manifests:
    def get(self, **kwargs):
        return None

    def validate(self, **kwargs):
        raise AssertionError("the document is validated by the test itself")


class _NoApplies:
    def recent(self, **kwargs):
        return []


def _manifest_compiler() -> ManifestCompiler:
    objects = FakeObjectStore()
    for (bucket, key), fixture in OBJECTS.items():
        objects.put(bucket, key, (SOURCES / fixture).read_bytes(), access_key_id=OSS_ACCESS_KEY_ID)
    content = FakeManifestContent()
    return ManifestCompiler(
        manifests=_Manifests(),
        content=content,
        resolver=DeclaredSourceResolver(content, FakeObjectCredentials(), objects),
        validator=real_validator(),
        git_client_provider=_FixtureGit,
        applies=_NoApplies(),
    )


def _comparable(declaration: Declaration[Any] | None) -> list[Any] | None:
    if declaration is None:
        return None
    assert not declaration.failures, [f.reason for f in declaration.failures]
    return [replace(e, note=None) if hasattr(e, "note") else e for e in declaration.entries]


def _as_set_where_order_is_free(construct: ApplyConstruct, entries: list[Any]) -> Any:
    """A revision keeps its categories sorted by key; a document keeps its author's order. The script is one entry."""
    return entries if construct is ManifestSection.SCRIPT else set(entries)


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_the_manifest_and_its_revision_compile_to_the_same_desired_state(case: Path) -> None:
    capabilities = resolve_capabilities(active_engine="openclaw", bot_type="personal", is_teclaw=lambda _: False)
    parsed = validate_document((case / "manifest.yaml").read_text(encoding="utf-8"), capabilities).parsed
    ctx = make_context(engine_type="openclaw", bot_type="personal")
    from_manifest: DesiredState = asyncio.run(_manifest_compiler().compile_document(ctx, parsed)).state

    revision = json.loads((case / "revision.json").read_text(encoding="utf-8"))
    from_revision = compile_spec(GenomeSpec.from_json(revision["spec"]), BlobDir())

    for construct in COMPARED:
        manifest_side = _comparable(from_manifest.declaration(construct))
        genome_side = _comparable(from_revision.declaration(construct))
        assert genome_side is not None, f"{construct.value}: a revision declares every category"
        if manifest_side is None:
            assert genome_side == [], f"{construct.value}: the document left it alone; the captured bot had none"
            continue
        assert _as_set_where_order_is_free(construct, manifest_side) == _as_set_where_order_is_free(
            construct, genome_side
        ), construct.value


def test_the_cases_cover_every_construct_the_two_compilers_share() -> None:
    """The comparison above is only as strong as what the cases declare."""
    declared: set[ApplyConstruct] = set()
    for case in CASES:
        parsed = yaml.safe_load((case / "manifest.yaml").read_text(encoding="utf-8")) or {}
        manifest = parsed.get("manifest") or {}
        declared.update(ManifestCategory(name) for name in manifest if name in {c.value for c in ManifestCategory})
        if "script" in parsed:
            declared.add(ManifestSection.SCRIPT)
    assert set(COMPARED) <= declared
