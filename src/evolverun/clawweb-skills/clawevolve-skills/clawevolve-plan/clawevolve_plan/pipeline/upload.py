from __future__ import annotations

import hashlib
import re
import stat
import zipfile
from collections import Counter
import urllib.parse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .. import logger
from ..constants import clawweb_base_url
from ..integration.clawweb import upload_templates, verify_published_domain
from ..runtime_identity import resolve_runtime_user_id

_SPLITS = ("train", "test")
_MAX_TEMPLATES_PER_DOMAIN = 500
_DOMAIN_ID_MAX_LENGTH = 128


@dataclass(frozen=True)
class BenchSplitPackage:
    split: str
    zip_path: Path
    zip_sha256: str
    template_names: tuple[str, ...]

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any], split: str) -> "BenchSplitPackage":
        raw = (manifest.get("split_packages") or {}).get(split) or {}
        names = tuple(
            str(value).strip()
            for value in raw.get("template_names") or []
            if str(value).strip()
        )
        return cls(
            split=split,
            zip_path=Path(str(raw.get("zip_path") or "")),
            zip_sha256=str(raw.get("zip_sha256") or "").strip().lower(),
            template_names=names,
        )

    def validation_error(self, *, allow_empty: bool = False) -> str:
        if not self.template_names:
            if not allow_empty:
                return f"no {self.split} templates generated"
        if len(self.template_names) != len(set(self.template_names)):
            return f"duplicate {self.split} template names in manifest"
        if len(self.template_names) > _MAX_TEMPLATES_PER_DOMAIN:
            return (
                f"{self.split} template count {len(self.template_names)} exceeds "
                f"ClawWeb batch publish limit {_MAX_TEMPLATES_PER_DOMAIN}"
            )
        if not str(self.zip_path) or not self.zip_path.is_file():
            return f"{self.split} template ZIP does not exist: {self.zip_path}"
        if not self.zip_sha256:
            return f"{self.split} template ZIP digest is missing"
        actual_digest = _file_sha256(self.zip_path)
        if actual_digest != self.zip_sha256:
            return (
                f"{self.split} template ZIP digest mismatch: "
                f"expected={self.zip_sha256}, actual={actual_digest}"
            )
        return self._archive_validation_error(allow_empty=allow_empty)

    def _archive_validation_error(self, *, allow_empty: bool = False) -> str:
        expected_prefix = "opt/" if self.split == "train" else "val/"
        try:
            with zipfile.ZipFile(self.zip_path) as archive:
                file_infos = [info for info in archive.infolist() if not info.is_dir()]
                files = sorted(info.filename for info in file_infos)
                encrypted = sorted(
                    info.filename for info in file_infos if info.flag_bits & 0x1
                )
                symlinks = sorted(
                    info.filename
                    for info in file_infos
                    if stat.S_ISLNK(info.external_attr >> 16)
                )
                if encrypted:
                    return (
                        f"{self.split} template ZIP contains encrypted entries: "
                        f"{encrypted}"
                    )
                if symlinks:
                    return (
                        f"{self.split} template ZIP contains symbolic links: "
                        f"{symlinks}"
                    )
                for info in file_infos:
                    archive.read(info)
        except (KeyError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
            return f"{self.split} template ZIP is unreadable: {exc}"
        if not files and not allow_empty:
            return f"{self.split} template ZIP contains no files"
        invalid_paths = [
            name
            for name in files
            if not _valid_split_archive_path(name, expected_prefix)
        ]
        if invalid_paths:
            return (
                f"{self.split} template ZIP contains files outside "
                f"{expected_prefix}*.md: {invalid_paths}"
            )
        duplicate_paths = sorted(
            name for name, count in Counter(files).items() if count > 1
        )
        if duplicate_paths:
            return (
                f"{self.split} template ZIP contains duplicate entries: "
                f"{duplicate_paths}"
            )
        archived_names = [PurePosixPath(name).stem for name in files]
        if sorted(archived_names) != sorted(self.template_names):
            return (
                f"{self.split} template ZIP names do not match manifest: "
                f"manifest={sorted(self.template_names)}, archive={sorted(archived_names)}"
            )
        return ""

    def template_hashes(self) -> dict[str, str]:
        """Return ClawWeb-compatible source hashes keyed by template name."""
        expected_prefix = "opt/" if self.split == "train" else "val/"
        hashes: dict[str, str] = {}
        try:
            with zipfile.ZipFile(self.zip_path) as archive:
                for entry in archive.namelist():
                    if entry.endswith("/") or not entry.startswith(expected_prefix):
                        continue
                    if not entry.endswith(".md"):
                        continue
                    name = PurePosixPath(entry).stem
                    hashes[name] = hashlib.sha256(archive.read(entry)).hexdigest()[:16]
        except (KeyError, OSError, RuntimeError, zipfile.BadZipFile):
            return {}
        return hashes

    def result_fields(self) -> dict[str, Any]:
        return {
            "zip_path": str(self.zip_path),
            "zip_sha256": self.zip_sha256,
            "expected_template_names": list(self.template_names),
            "expected_template_count": len(self.template_names),
            "expected_template_hashes": self.template_hashes()
            if self.zip_path.is_file()
            else {},
        }


def _valid_split_archive_path(name: str, expected_prefix: str) -> bool:
    if not name or "\\" in name:
        return False
    path = PurePosixPath(name)
    parts = name.split("/")
    return bool(
        not path.is_absolute()
        and len(parts) == 2
        and parts[0] == expected_prefix.rstrip("/")
        and all(part not in ("", ".", "..") for part in parts)
        and path.suffix == ".md"
    )


def skipped_dual_domain_upload_result(
    *, manifest: dict[str, Any], reason: str = "--skip-clawweb-report"
) -> dict[str, Any]:
    domains = {
        split: {
            "split": split,
            "enabled": False,
            "status": "skipped",
            "reason": reason,
            "published": False,
            "verified": False,
            **BenchSplitPackage.from_manifest(manifest, split).result_fields(),
        }
        for split in _SPLITS
    }
    return _aggregate_result(domains, enabled=False, reason=reason)


def upload_bench_domains(
    *, plan: dict[str, Any], manifest: dict[str, Any], task_id: str
) -> dict[str, Any]:
    packages = {
        split: BenchSplitPackage.from_manifest(manifest, split) for split in _SPLITS
    }
    shared_train_domain = _uses_shared_train_domain(packages)
    validation_errors = {
        split: error
        for split, package in packages.items()
        if (
            error := package.validation_error(
                allow_empty=shared_train_domain and split == "test"
            )
        )
    }
    overlap = sorted(
        set(packages["train"].template_names).intersection(
            packages["test"].template_names
        )
    )
    if overlap:
        reason = f"train/test template names overlap: {overlap}"
        validation_errors.setdefault("train", reason)
        validation_errors.setdefault("test", reason)
    if validation_errors:
        domains = {
            split: _invalid_package_result(
                package, validation_errors.get(split), validation_errors
            )
            for split, package in packages.items()
        }
        return _aggregate_result(
            domains,
            enabled=True,
            reason="train/test template package preflight failed",
        )

    user_id, user_meta = resolve_runtime_user_id(layout=plan.get("agent_context") or {})
    if not user_id:
        domains = {
            split: {
                "split": split,
                "enabled": True,
                "status": "failed",
                "reason": "clawweb user id not found",
                "published": False,
                "verified": False,
                **package.result_fields(),
            }
            for split, package in packages.items()
        }
        result = _aggregate_result(
            domains, enabled=True, reason="clawweb user id not found"
        )
        result["user_meta"] = user_meta
        return result

    domains: dict[str, dict[str, Any]] = {}
    upload_splits = ("train",) if shared_train_domain else _SPLITS
    for split in upload_splits:
        package = packages[split]
        domain_id = _domain_id(plan, task_id, split, package.zip_sha256)
        try:
            uploaded = upload_templates(
                domain_id,
                package.zip_path,
                user_id,
                expected_template_names=list(package.template_names),
                expected_template_hashes=package.template_hashes(),
            )
            uploaded.update(
                {"split": split, **package.result_fields(), "user_meta": user_meta}
            )
            domains[split] = uploaded
        except Exception as exc:  # noqa: BLE001 - preserve local plan artifacts.
            logger.warning(
                "clawweb split upload failed",
                split=split,
                domain_id=domain_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            domains[split] = {
                "split": split,
                "enabled": True,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "domain_id": domain_id,
                "user_id": user_id,
                "owner_user_id": user_id,
                "published": False,
                "verified": False,
                **package.result_fields(),
                "user_meta": user_meta,
            }
    if shared_train_domain:
        train_domain = domains["train"]
        domains["test"] = {
            **train_domain,
            "split": "test",
            "source_split": "train",
            "alias_of": "train",
            "independent": False,
            "validation_mode": "shared_train_domain",
        }
    result = _aggregate_result(domains, enabled=True)
    result["user_meta"] = user_meta
    return result


def _uses_shared_train_domain(
    packages: dict[str, BenchSplitPackage],
) -> bool:
    """Allow a train-only Plan to satisfy the existing dual-domain contract.

    A single replayable source cannot form an independent test split. We publish
    the train package once and pass the same immutable domain as the test execution
    entry, while retaining explicit metadata that validation is not independent.
    """
    return bool(packages["train"].template_names) and not bool(
        packages["test"].template_names
    )


def _invalid_package_result(
    package: BenchSplitPackage,
    split_error: str | None,
    all_errors: dict[str, str],
) -> dict[str, Any]:
    reason = split_error or (
        "not attempted because another split package failed preflight: "
        + "; ".join(f"{split}={error}" for split, error in all_errors.items())
    )
    return {
        "split": package.split,
        "enabled": True,
        "status": "failed" if split_error else "not_attempted",
        "reason": reason,
        "published": False,
        "verified": False,
        **package.result_fields(),
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _aggregate_result(
    domains: dict[str, dict[str, Any]], *, enabled: bool, reason: str = ""
) -> dict[str, Any]:
    complete = all(
        domains.get(split, {}).get("published") is True
        and domains.get(split, {}).get("verified") is True
        for split in _SPLITS
    )
    status = "published" if complete else ("skipped" if not enabled else "failed")
    result: dict[str, Any] = {
        "schema_version": "clawevolve.clawweb-dual-domain-upload.v1",
        "enabled": enabled,
        "status": status,
        "published": complete,
        "verified": complete,
        "domains": domains,
        "validation_independent": not bool(
            (domains.get("test") or {}).get("alias_of") == "train"
        ),
        "validation_mode": (
            "shared_train_domain"
            if (domains.get("test") or {}).get("alias_of") == "train"
            else "isolated_train_test"
        ),
    }
    if reason:
        result["reason"] = reason
    for split in _SPLITS:
        domain = domains.get(split) or {}
        result[f"{split}_domain_id"] = str(domain.get("domain_id") or "")
        result[f"{split}_domain_url"] = _domain_url(domain) if complete else ""
    return result


def _domain_id(plan: dict[str, Any], task_id: str, split: str, digest: str) -> str:
    raw_bot_id = str(plan.get("bot_id") or "current_bot")
    raw_task_id = task_id or "local_task"
    bot_id = _slug_identifier(raw_bot_id, 48)
    task = _slug_identifier(raw_task_id, 48)
    package_digest = (
        digest
        or hashlib.sha256(
            f"{raw_bot_id}\0{raw_task_id}\0{split}".encode()
        ).hexdigest()
    )[:12]
    identity_digest = hashlib.sha256(
        f"{raw_bot_id}\0{raw_task_id}".encode()
    ).hexdigest()[:10]
    fixed_suffix = f"_{split}_{package_digest}_{identity_digest}"
    prefix_budget = _DOMAIN_ID_MAX_LENGTH - len(fixed_suffix)
    prefix = f"bot_{bot_id}_{task}"[:prefix_budget].rstrip("_.-") or "bot_unknown"
    return f"{prefix}{fixed_suffix}"


def _slug_identifier(value: str, limit: int) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")[:limit] or "unknown"


def _domain_url(domain: dict[str, Any]) -> str:
    explicit = str(domain.get("domain_url") or "").strip()
    if explicit:
        return explicit
    owner = str(domain.get("owner_user_id") or domain.get("user_id") or "").strip()
    domain_id = str(domain.get("domain_id") or "").strip()
    base_url = str(domain.get("base_url") or clawweb_base_url()).rstrip("/")
    return (
        f"{base_url}/bench/domains/"
        f"{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(domain_id, safe='')}"
        if owner and domain_id
        else ""
    )


def revalidate_cached_bench_domains(
    result: dict[str, Any], manifest: dict[str, Any]
) -> dict[str, Any] | None:
    """Reuse a cached upload only when local and remote state still match."""
    if not upload_result_is_complete(result):
        return None
    packages = {
        split: BenchSplitPackage.from_manifest(manifest, split) for split in _SPLITS
    }
    shared_train_domain = _uses_shared_train_domain(packages)
    if any(
        package.validation_error(
            allow_empty=shared_train_domain and split == "test"
        )
        for split, package in packages.items()
    ):
        return None
    domains = result.get("domains") or {}
    remote_verification: dict[str, Any] = {}
    for split in _SPLITS:
        cached = domains.get(split) or {}
        alias = shared_train_domain and split == "test"
        if alias and cached.get("alias_of") != "train":
            return None
        package = packages["train"] if alias else packages[split]
        if str(cached.get("zip_sha256") or "") != package.zip_sha256:
            return None
        if sorted(cached.get("expected_template_names") or []) != sorted(
            package.template_names
        ):
            return None
        expected_hashes = package.template_hashes()
        cached_hashes = {
            str(name): str(value).lower()
            for name, value in (cached.get("expected_template_hashes") or {}).items()
        }
        if cached_hashes and cached_hashes != expected_hashes:
            return None
        domain_id = str(cached.get("domain_id") or "").strip()
        user_id = str(
            cached.get("user_id") or cached.get("owner_user_id") or ""
        ).strip()
        owner_user_id = str(cached.get("owner_user_id") or user_id).strip()
        if not domain_id or not user_id or not owner_user_id:
            return None
        try:
            verification = verify_published_domain(
                domain_id,
                user_id,
                owner_user_id=owner_user_id,
                expected_template_hashes=expected_hashes,
            )
        except Exception as exc:  # noqa: BLE001 - cache miss falls back to repair.
            logger.warning(
                "cached clawweb domain remote verification failed",
                split=split,
                domain_id=domain_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            return None
        if not verification.get("verified"):
            logger.warning(
                "cached clawweb domain is stale",
                split=split,
                domain_id=domain_id,
                verification=verification,
            )
            return None
        remote_verification[split] = verification
        cached["expected_template_hashes"] = expected_hashes
        cached["remote_revalidated"] = True
        cached["remote_verification"] = verification
    result["reused"] = True
    result["remote_revalidated"] = True
    result["remote_verification"] = remote_verification
    return result


def dual_domain_meta(upload_result: dict[str, Any]) -> dict[str, Any]:
    domains: dict[str, dict[str, Any]] = {}
    for split in _SPLITS:
        raw = (upload_result.get("domains") or {}).get(split) or {}
        valid = raw.get("published") is True and raw.get("verified") is True
        domains[split] = {
            "split": split,
            "enabled": bool(raw.get("enabled", upload_result.get("enabled", True))),
            "status": str(raw.get("status") or "unknown"),
            "domain_id": str(raw.get("domain_id") or "") if valid else "",
            "owner_user_id": str(raw.get("owner_user_id") or raw.get("user_id") or "")
            if valid
            else "",
            "base_url": str(raw.get("base_url") or clawweb_base_url()),
            "domain_url": _domain_url(raw) if valid else "",
            "zip_path": str(raw.get("zip_path") or ""),
            "published": bool(raw.get("published")),
            "verified": bool(raw.get("verified")),
            "independent": bool(
                raw.get("independent", split != "test" or not raw.get("alias_of"))
            ),
            "alias_of": str(raw.get("alias_of") or ""),
        }
    return {
        "schema_version": "clawevolve.clawweb-dual-domain.v1",
        "enabled": bool(upload_result.get("enabled", True)),
        "status": str(upload_result.get("status") or "unknown"),
        "published": bool(upload_result.get("published")),
        "verified": bool(upload_result.get("verified")),
        "validation_independent": bool(
            upload_result.get("validation_independent", True)
        ),
        "validation_mode": str(
            upload_result.get("validation_mode") or "isolated_train_test"
        ),
        "domains": domains,
    }


def upload_result_is_complete(result: dict[str, Any]) -> bool:
    return bool(result.get("published") and result.get("verified")) and all(
        ((result.get("domains") or {}).get(split) or {}).get("published") is True
        and ((result.get("domains") or {}).get(split) or {}).get("verified") is True
        for split in _SPLITS
    )
