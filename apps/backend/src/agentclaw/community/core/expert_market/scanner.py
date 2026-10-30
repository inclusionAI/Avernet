"""Validate author inputs and reconcile an independently governed catalogue."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from agentclaw.community.core.apply_engine.capabilities import (
    resolve_capabilities,
)
from agentclaw.community.core.bot_config_manifest.schema import validate_document
from agentclaw.community.core.expert_market.models import ExpertScope, ScanResult
from agentclaw.community.core.expert_market.protocols import (
    ExpertCatalogRepositoryProtocol,
    ExpertScannerProtocol,
)
from agentclaw.community.core.expert_market.source import (
    DirectoryExpertSource,
    ExpertContentSource,
    GitExpertSource,
    safe_path,
)
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser


class _UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ValueError("duplicate or non-string YAML key")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _yaml(data: bytes) -> dict:
    if len(data) > 65536:
        raise ValueError("expert configuration exceeds 64 KiB")
    value = yaml.load(data.decode("utf-8"), Loader=_UniqueLoader)
    if not isinstance(value, dict):
        raise ValueError("expected YAML object")
    return value


def _text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"invalid {name}")
    return value


class ExpertScanner(ExpertScannerProtocol):
    def __init__(self, catalog: ExpertCatalogRepositoryProtocol):
        self.catalog = catalog

    def scan(
        self, repository_root: Path, *, source_commit: str, scope: ExpertScope
    ) -> ScanResult:
        return self.scan_source(
            DirectoryExpertSource(repository_root),
            source_commit=source_commit,
            scope=scope,
        )

    def scan_git(
        self, bare_repo: Path, *, source_commit: str, scope: ExpertScope
    ) -> ScanResult:
        return self.scan_source(
            GitExpertSource(bare_repo, source_commit),
            source_commit=source_commit,
            scope=scope,
        )

    def scan_source(
        self, source: ExpertContentSource, *, source_commit: str, scope: ExpertScope
    ) -> ScanResult:
        if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
            raise ValueError("source_commit must be a complete Git commit SHA")
        paths = source.paths()  # complete enumeration must succeed before reconcile
        experts = sorted(
            p for p in paths if re.fullmatch(r"agents/[^/]+/expert\.yaml", p)
        )
        valid, errors, seen = {}, {}, set()
        for path in experts:
            expert_id = path.split("/")[1]
            seen.add(expert_id)
            try:
                valid[expert_id] = self._expert(source, paths, path, expert_id)
            except (ValueError, OSError, yaml.YAMLError, UnicodeError) as exc:
                errors[expert_id] = str(exc)[:500]
        self.catalog.reconcile(scope, source_commit, valid, errors, seen)
        return ScanResult(
            len(valid), len(errors), sum(not v["enabled"] for v in valid.values())
        )

    def _expert(
        self,
        source: ExpertContentSource,
        paths: tuple[str, ...],
        path: str,
        expert_id: str,
    ) -> dict:
        meta = _yaml(source.read(path))
        allowed = {
            "id",
            "name",
            "description",
            "manifest",
            "enabled",
            "category",
            "tags",
            "display_order",
        }
        if set(meta) - allowed:
            raise ValueError("unknown expert metadata field")
        if meta.get("id") != expert_id or not re.fullmatch(
            r"[a-z0-9][a-z0-9-]{0,63}", expert_id
        ):
            raise ValueError("expert id must match its directory")
        name = _text(meta.get("name"), "name", 40)
        description = _text(meta.get("description"), "description", 200)
        category = _text(meta.get("category", "未分类"), "category", 40)
        enabled = meta.get("enabled", True)
        if type(enabled) is not bool or type(meta.get("display_order", 0)) is not int:
            raise ValueError("enabled/display_order have incorrect types")
        tags = meta.get("tags", [])
        if not isinstance(tags, list) or len(tags) > 20:
            raise ValueError("invalid tags")
        tags = [_text(tag, "tag", 40) for tag in tags]
        if len(set(tags)) != len(tags):
            raise ValueError("duplicate tag")
        manifest_path = safe_path(_text(meta.get("manifest"), "manifest", 256))
        manifest_path = path.rsplit("/", 1)[0] + "/" + manifest_path
        template = _yaml(source.read(manifest_path))
        if "apply_policy" in template:
            raise ValueError("apply_policy is not supported for expert templates")
        if set(template) - {"schema_version", "manifest"}:
            raise ValueError("expert templates cannot declare sources or scripts")
        manifest = template.get("manifest")
        if isinstance(manifest, dict) and "cli_tools" in manifest:
            raise ValueError("cli_tools is not supported for expert templates")
        if not isinstance(manifest, dict) or set(manifest) - {
            "identity",
            "skills",
            "mcp",
            "resources",
        }:
            raise ValueError("invalid expert manifest categories")
        if not manifest.get("identity") and not manifest.get("skills"):
            raise ValueError("expert must declare identity or a professional Skill")
        references: dict[str, bytes] = {}
        identity, skills = [], []
        for category_name, entries in manifest.items():
            if not isinstance(entries, list):
                raise ValueError("manifest categories must be arrays")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("manifest entry must be an object")
                if category_name == "mcp":
                    continue
                if any(
                    key in entry
                    for key in (
                        "source",
                        "auth",
                        "digest",
                        "unpack",
                        "strip_components",
                    )
                ):
                    raise ValueError(
                        "expert content must come from the same repository"
                    )
                content = entry.get("content")
                selected = []
                if "from" in entry:
                    if entry["from"] != "aiworkbench":
                        raise ValueError("expert source must be aiworkbench")
                    subpath = safe_path(
                        _text(entry.get("subpath"), "subpath", 256).rstrip("/")
                    )
                    selected = [
                        p for p in paths if p == subpath or p.startswith(subpath + "/")
                    ]
                    if not selected:
                        raise ValueError("referenced content is missing")
                    for item in selected:
                        references[item] = source.read(item)
                    if category_name == "identity" and selected != [
                        subpath
                    ]:
                        raise ValueError(
                            "identity references must be a single file"
                        )
                    if category_name == "identity":
                        content = references[subpath].decode("utf-8")
                    elif category_name == "skills":
                        files = [
                            (p[len(subpath) + 1 :], references[p]) for p in selected
                        ]
                        package = SkillPackageValidator(SkillParser).validate_directory(
                            files
                        )
                        if (
                            package.name != entry.get("name")
                            or subpath.rsplit("/", 1)[-1] != package.name
                        ):
                            raise ValueError("Skill names must agree")
                        skills.append(
                            {"name": package.name, "description": package.description}
                        )
                if category_name == "identity":
                    if not isinstance(content, str):
                        raise ValueError("identity must have text content")
                    identity.append({"type": entry.get("type"), "content": content})
        if sum(len(v) for v in references.values()) > 50 * 1024 * 1024:
            raise ValueError("expert referenced files exceed 50 MiB")
        # Reuse the shipped schema/support matrix. Missing producer extensions
        # fail visibly; no apply_policy/identity declaration is silently removed.
        bound = dict(template)
        bound["sources"] = {
            "aiworkbench": {
                "protocol": "git",
                "url": "https://example.com/experts.git",
                "ref": "0" * 40,
                "mode": "strict",
            }
        }
        validate_document(
            yaml.safe_dump(bound, allow_unicode=True),
            resolve_capabilities(
                active_engine="openclaw", bot_type="personal", is_teclaw=lambda _: False
            ),
        )
        file_hashes = [
            {"path": p, "sha256": hashlib.sha256(data).hexdigest()}
            for p, data in sorted(references.items())
        ]
        digest = canonical_digest(
            {"expert": meta, "manifest": template, "files": file_hashes}
        )
        return {
            "expert_id": expert_id,
            "name": name,
            "description": description,
            "category": category,
            "tags": tags,
            "enabled": enabled,
            "expert_path": path.rsplit("/", 1)[0],
            "content_digest": digest,
            "digest_encoding": "sha256-json-v1",
            "metadata": meta,
            "manifest_template": template,
            "references": file_hashes,
            "identity": identity,
            "skills": skills,
        }
