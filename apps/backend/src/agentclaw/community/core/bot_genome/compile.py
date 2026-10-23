"""Compile a revision into the complete desired-state document delivery consumes.

The mapping is ``api-contracts/genome/v1/manifest-v1-compatibility.md``:
every stored file becomes ``{protocol: content_store, digest}``, a Skill
Center skill ``{protocol: center, version}``, a shared-repository skill
``{protocol: repo, path}``, MCP entries go verbatim, the script's stored
body is inlined, and ``memory`` is omitted while ``null``. No ``cli_tools``
and no ``engine_config``: the document never touches them. The document's
digest is the digest of its canonical JSON; that is what a promotion
records and what RSI-04's apply report must repeat.

The document itself stays a plain JSON object: its shape is the Manifest's
(``bot_config_manifest/schema``), which owns and validates it.
"""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.bot_genome.canonical import canonicalize, sha256_digest
from agentclaw.community.core.bot_genome.models import CompiledManifest, ContentReader
from agentclaw.community.core.bot_genome.spec_models import CenterSkill, GenomeSpec, RepoSkill, SkillEntry

__all__ = ["compile_spec", "document_digest"]


def _store(digest: str) -> dict[str, str]:
    return {"protocol": "content_store", "digest": digest}


def _skill_source(entry: SkillEntry) -> dict[str, str]:
    if isinstance(entry, CenterSkill):
        return {"protocol": "center", "version": entry.center_version}
    if isinstance(entry, RepoSkill):
        return {"protocol": "repo", "path": entry.repo_path}
    return _store(entry.digest)


def document_digest(document: dict[str, Any]) -> str:
    return sha256_digest(canonicalize(document))


def compile_spec(spec: GenomeSpec, content: ContentReader, *, revision_id: str) -> CompiledManifest:
    """The compiled document of ``spec``; ``content`` supplies the script body."""
    script = None
    if spec.script is not None:
        script = {"body": content.read(spec.script.digest).decode("utf-8")}
    document = {
        "schema_version": 1,
        "manifest": {
            "identity": [{"type": e.type, "source": _store(e.digest)} for e in spec.persona],
            "skills": [{"name": e.name, "source": _skill_source(e)} for e in spec.skills],
            "resources": [{"path": e.path, "source": _store(e.digest)} for e in spec.resources],
            "mcp": [e.to_json() for e in spec.tools.mcp],
        },
        "script": script,
    }
    return CompiledManifest(revision_id=revision_id, document=document, document_digest=document_digest(document))
