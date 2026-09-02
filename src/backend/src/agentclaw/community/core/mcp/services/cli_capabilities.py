"""Validated Default-CLI policy used by Passport scope writers.

The manifest is deployment-owned configuration.  It is deliberately parsed as
data only: no manifest field is ever passed to a shell in this backend.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from typing import Any

import yaml

from agentclaw.community.plugin_api.passport import CliItem


_IDENTITY_MODES = {"owner", "caller"}
_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_DEFAULT_MANIFEST_PATH = (
    Path(__file__).resolve().parents[3] / "configs" / "cli-capabilities.yaml"
)


@dataclass(frozen=True, slots=True)
class CliCapabilityManifest:
    """A normalized, non-executable representation of the CLI manifest."""

    version: int
    manifest_version: str
    digest: str
    profiles: tuple[dict[str, Any], ...]
    catalog: Mapping[str, CliItem]


class CliCapabilityManifestResolver:
    """Load the fixed deployment manifest and resolve exact engine profiles."""

    def __init__(self, manifest_path: Path | str | None = None) -> None:
        self._path = Path(manifest_path) if manifest_path is not None else _DEFAULT_MANIFEST_PATH
        self._manifest = self._load()

    @property
    def manifest_version(self) -> str:
        return self._manifest.manifest_version

    @property
    def manifest_digest(self) -> str:
        return self._manifest.digest

    def required_cli_items(
        self,
        engine_type: str | None,
        template_type: str | None,
    ) -> list[CliItem]:
        """Return the default items for one exact logical-engine profile."""
        for profile in self._manifest.profiles:
            match = profile["match"]
            if match["engine_type"] != engine_type:
                continue
            if "template_type" in match and match["template_type"] != template_type:
                continue
            return [dict(self._manifest.catalog[code]) for code in profile["default_cli_codes"]]
        return []

    def is_supported_profile(
        self, engine_type: str | None, template_type: str | None
    ) -> bool:
        return bool(self.required_cli_items(engine_type, template_type))

    def _load(self) -> CliCapabilityManifest:
        try:
            raw = self._path.read_bytes()
        except OSError as exc:
            raise ValueError(f"CLI capability manifest is unreadable: {self._path}") from exc
        try:
            document = yaml.safe_load(raw) or {}
        except yaml.YAMLError as exc:
            raise ValueError("CLI capability manifest is invalid YAML") from exc
        if not isinstance(document, Mapping):
            raise ValueError("CLI capability manifest must be a mapping")
        version = document.get("version")
        manifest_version = document.get("manifest_version")
        if version != 1 or not isinstance(manifest_version, str) or not manifest_version.strip():
            raise ValueError("CLI capability manifest version is invalid")
        catalog = _parse_catalog(document.get("catalog"))
        profiles = _parse_profiles(document.get("profiles"), catalog)
        return CliCapabilityManifest(
            version=version,
            manifest_version=manifest_version,
            digest=sha256(raw).hexdigest(),
            profiles=tuple(profiles),
            catalog=catalog,
        )


def merge_cli_scope(
    historical: list[CliItem] | None,
    required: list[CliItem] | None,
    sparse_overrides: Mapping[str, object] | None = None,
) -> list[CliItem]:
    """Merge history-first CLI scope and apply only valid sparse overrides."""
    merged: list[CliItem] = []
    seen: set[str] = set()
    for item in (historical or []) + (required or []):
        normalized = _normalize_cli_item(item)
        cli_code = normalized["cli_code"]
        if cli_code in seen:
            continue
        seen.add(cli_code)
        merged.append(normalized)
    for item in merged:
        override = (sparse_overrides or {}).get(str(item["cli_code"]))
        if override is not None:
            item["identity_mode"] = _normalize_identity_mode(override)
    return merged


def _parse_catalog(raw: object) -> dict[str, CliItem]:
    if not isinstance(raw, Mapping) or not raw:
        raise ValueError("CLI capability catalog must be a non-empty mapping")
    catalog: dict[str, CliItem] = {}
    for code, definition in raw.items():
        if not isinstance(code, str) or not _CODE_RE.fullmatch(code):
            raise ValueError("CLI catalog code is invalid")
        if not isinstance(definition, Mapping):
            raise ValueError(f"CLI catalog entry {code} is invalid")
        cli_name = definition.get("cli_name")
        cli_desc = definition.get("cli_desc")
        if not isinstance(cli_name, str) or not isinstance(cli_desc, str):
            raise ValueError(f"CLI catalog entry {code} lacks display metadata")
        _normalize_identity_mode(definition.get("default_identity_mode"))
        _validate_install_definition(code, definition)
        catalog[code] = {
            "cli_code": code,
            "cli_name": cli_name,
            "cli_desc": cli_desc,
            "identity_mode": _normalize_identity_mode(definition.get("default_identity_mode")),
        }
    return catalog


def _parse_profiles(raw: object, catalog: Mapping[str, CliItem]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("CLI capability profiles must be a list")
    profiles: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for profile in raw:
        if not isinstance(profile, Mapping):
            raise ValueError("CLI capability profile is invalid")
        profile_id = profile.get("id")
        match = profile.get("match")
        codes = profile.get("default_cli_codes")
        if not isinstance(profile_id, str) or not profile_id or profile_id in seen_ids:
            raise ValueError("CLI capability profile id is invalid")
        if not isinstance(match, Mapping) or not isinstance(match.get("engine_type"), str):
            raise ValueError(f"CLI capability profile {profile_id} has invalid match")
        if "template_type" in match and not isinstance(match["template_type"], str):
            raise ValueError(f"CLI capability profile {profile_id} has invalid template")
        if not isinstance(codes, list) or not codes or len(codes) != len(set(codes)):
            raise ValueError(f"CLI capability profile {profile_id} has invalid codes")
        if any(not isinstance(code, str) or code not in catalog for code in codes):
            raise ValueError(f"CLI capability profile {profile_id} references unknown CLI")
        seen_ids.add(profile_id)
        profiles.append({"id": profile_id, "match": dict(match), "default_cli_codes": list(codes)})
    return profiles


def _validate_install_definition(code: str, definition: Mapping[str, object]) -> None:
    executable = definition.get("executable")
    install = definition.get("install")
    probe_argv = definition.get("probe_argv")
    if not isinstance(executable, str) or not _CODE_RE.fullmatch(executable):
        raise ValueError(f"CLI catalog entry {code} has invalid executable")
    if not isinstance(install, Mapping) or not isinstance(install.get("installer"), str):
        raise ValueError(f"CLI catalog entry {code} has invalid installer")
    _validate_argv(install.get("argv"), f"CLI catalog entry {code} install argv")
    _validate_argv(probe_argv, f"CLI catalog entry {code} probe argv")


def _validate_argv(raw: object, label: str) -> None:
    if not isinstance(raw, list) or not raw or any(
        not isinstance(value, str) or not value or not _CODE_RE.fullmatch(value.lstrip("-"))
        for value in raw
    ):
        raise ValueError(f"{label} is invalid")


def _normalize_cli_item(item: object) -> CliItem:
    if not isinstance(item, Mapping):
        raise ValueError("CLI scope item is invalid")
    code = item.get("cli_code")
    if not isinstance(code, str) or not _CODE_RE.fullmatch(code):
        raise ValueError("CLI scope item code is invalid")
    result: CliItem = {"cli_code": code, "identity_mode": _normalize_identity_mode(item.get("identity_mode"))}
    if "cli_name" in item:
        result["cli_name"] = item.get("cli_name") if isinstance(item.get("cli_name"), str) else None
    if "cli_desc" in item:
        result["cli_desc"] = item.get("cli_desc") if isinstance(item.get("cli_desc"), str) else None
    return result


def _normalize_identity_mode(raw: object) -> str:
    if raw is None:
        return "owner"
    value = getattr(raw, "value", raw)
    normalized = str(value).strip().lower()
    if normalized not in _IDENTITY_MODES:
        raise ValueError("identity mode must be owner or caller")
    return normalized


__all__ = ["CliCapabilityManifestResolver", "merge_cli_scope"]
