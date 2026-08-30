"""Production adapter for immutable external Space Skill source bytes."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import signal
import socket
import subprocess
import time
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import requests

from agentclaw.community.core.skill_center.git_snapshot import (
    select_skill_source_subdir,
)
from agentclaw.community.core.skill_center.skill_package import (
    MAX_COMPRESSED_BYTES,
    MAX_EXPANDED_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
)
from agentclaw.community.plugin_api.impl_registry import Mode, plugin_impl
from agentclaw.community.plugin_api.space_skill_source import (
    ExactSkillPackageFetchError,
    GitSkillSnapshot,
    GitSnapshotError,
    GitSnapshotInvalidError,
    SpaceSkillSourcePlugin,
)


MAX_GIT_ACQUISITION_BYTES = 2 * MAX_EXPANDED_BYTES + MAX_COMPRESSED_BYTES


@plugin_impl(mode=Mode.PROD, rationale="credential-free Git and exact HTTPS reads")
class CommunitySpaceSkillSource(SpaceSkillSourcePlugin):
    """Clone without ambient credentials and freeze one selected Skill tree."""

    def fetch_git_snapshot(
        self, *, git_url: str, branch: str | None, subdir: str | None
    ) -> GitSkillSnapshot:
        url, curl_resolve = self._validated_url(git_url)
        branch = self._validated_branch(branch)
        with TemporaryDirectory(prefix="space-skill-git-") as temp:
            checkout = Path(temp) / "repo"
            environment = {
                "HOME": temp,
                "PATH": os.defpath,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_ASKPASS": "",
            }
            command = [
                "git",
                "-c",
                "credential.helper=",
                "-c",
                "http.followRedirects=false",
                "-c",
                f"http.curloptResolve={curl_resolve}",
                "clone",
                "--depth",
                "1",
            ]
            if branch is not None:
                command.extend(("--branch", branch))
            command.extend(("--", url, str(checkout)))
            self._run(
                command,
                cwd=None,
                environment=environment,
                resource_root=checkout,
                max_bytes=MAX_GIT_ACQUISITION_BYTES,
            )
            resolved_branch = self._run(
                ["git", "branch", "--show-current"],
                cwd=checkout,
                environment=environment,
            ).strip()
            if not resolved_branch:
                resolved_branch = branch or "HEAD"
            commit_sha = self._run(
                ["git", "rev-parse", "HEAD"], cwd=checkout, environment=environment
            ).strip()
            manifests = tuple(
                path.relative_to(checkout).as_posix()
                for path in checkout.rglob("SKILL.md")
                if ".git" not in path.relative_to(checkout).parts
            )
            selected = select_skill_source_subdir(manifests, requested_subdir=subdir)
            root = checkout / selected if selected else checkout
            excluded = self._nested_skill_roots(
                manifests=manifests, selected_subdir=selected
            )
            return GitSkillSnapshot(
                repo_url=url,
                resolved_branch=resolved_branch,
                commit_sha=commit_sha,
                source_subdir=selected,
                files=self._read_tree(root, excluded_roots=excluded),
            )

    def fetch_exact_package(self, *, url: str, expected_sha256: str) -> bytes:
        try:
            response = requests.get(url, timeout=60)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ExactSkillPackageFetchError(
                "exact Skill package download failed"
            ) from exc
        content = response.content
        if hashlib.sha256(content).hexdigest().lower() != expected_sha256.lower():
            raise ExactSkillPackageFetchError("exact Skill package checksum mismatch")
        return content

    @staticmethod
    def _nested_skill_roots(
        *, manifests: tuple[str, ...], selected_subdir: str
    ) -> tuple[PurePosixPath, ...]:
        selected = (
            PurePosixPath(selected_subdir) if selected_subdir else PurePosixPath(".")
        )
        roots: set[PurePosixPath] = set()
        for manifest in manifests:
            parent = PurePosixPath(manifest).parent
            try:
                relative = parent.relative_to(selected)
            except ValueError:
                continue
            if relative != PurePosixPath("."):
                roots.add(relative)
        return tuple(sorted(roots, key=lambda item: item.as_posix().encode("utf-8")))

    def _validated_url(self, value: str) -> tuple[str, str]:
        normalized = value.strip() if isinstance(value, str) else ""
        parsed = urlsplit(normalized)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.hostname.lower() in {"localhost", "localhost.localdomain"}
        ):
            raise GitSnapshotInvalidError("Git URL must be a credential-free HTTPS URL")
        try:
            port = parsed.port or 443
        except ValueError as exc:
            raise GitSnapshotInvalidError("Git URL port is invalid") from exc
        addresses = self._resolved_addresses(parsed.hostname)
        if not addresses or any(
            not ipaddress.ip_address(address).is_global for address in addresses
        ):
            raise GitSnapshotInvalidError("Git URL must resolve only to public hosts")
        pinned = addresses[0]
        if ipaddress.ip_address(pinned).version == 6:
            pinned = f"[{pinned}]"
        return normalized, f"{parsed.hostname}:{port}:{pinned}"

    @staticmethod
    def _resolved_addresses(hostname: str) -> tuple[str, ...]:
        try:
            resolved = socket.getaddrinfo(
                hostname,
                443,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise GitSnapshotInvalidError("Git URL host cannot be resolved") from exc
        return tuple(sorted({entry[4][0] for entry in resolved}))

    @staticmethod
    def _validated_branch(value: str | None) -> str | None:
        if value is None:
            return None
        branch = value.strip()
        if (
            not branch
            or len(branch) > 512
            or branch.startswith("-")
            or ".." in branch
            or any(character.isspace() or ord(character) < 32 for character in branch)
        ):
            raise GitSnapshotInvalidError("Git branch is invalid")
        return branch

    @staticmethod
    def _run(
        command: list[str],
        *,
        cwd: Path | None,
        environment: dict[str, str],
        resource_root: Path | None = None,
        max_bytes: int | None = None,
    ) -> str:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            raise GitSnapshotError("Git snapshot command failed") from exc
        deadline = time.monotonic() + 60
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                CommunitySpaceSkillSource._kill(process)
                raise GitSnapshotError("Git snapshot command timed out")
            try:
                stdout, _stderr = process.communicate(timeout=min(0.1, remaining))
                break
            except subprocess.TimeoutExpired:
                if (
                    resource_root is not None
                    and max_bytes is not None
                    and CommunitySpaceSkillSource._tree_size(resource_root) > max_bytes
                ):
                    CommunitySpaceSkillSource._kill(process)
                    raise GitSnapshotInvalidError(
                        "Git repository exceeds acquisition limit"
                    )
        if (
            resource_root is not None
            and max_bytes is not None
            and CommunitySpaceSkillSource._tree_size(resource_root) > max_bytes
        ):
            raise GitSnapshotInvalidError("Git repository exceeds acquisition limit")
        if process.returncode != 0:
            raise GitSnapshotError("Git snapshot command failed")
        return stdout

    @staticmethod
    def _tree_size(root: Path) -> int:
        if not root.exists():
            return 0
        try:
            return sum(
                path.stat().st_size for path in root.rglob("*") if path.is_file()
            )
        except OSError as exc:
            raise GitSnapshotError("Git acquisition size cannot be measured") from exc

    @staticmethod
    def _kill(process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()

    @staticmethod
    def _read_tree(
        root: Path, *, excluded_roots: tuple[PurePosixPath, ...]
    ) -> tuple[tuple[str, bytes], ...]:
        entries: list[tuple[str, bytes]] = []
        total = 0
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().encode()):
            relative = path.relative_to(root)
            relative_posix = PurePosixPath(relative.as_posix())
            if ".git" in relative.parts or any(
                relative_posix == excluded or excluded in relative_posix.parents
                for excluded in excluded_roots
            ):
                continue
            if path.is_dir():
                continue
            if path.is_symlink() or not path.is_file():
                raise GitSnapshotInvalidError("Git Skill tree contains a special file")
            size = path.stat().st_size
            total += size
            if size > MAX_FILE_BYTES or total > MAX_EXPANDED_BYTES:
                raise GitSnapshotInvalidError("Git Skill tree exceeds package limits")
            entries.append((relative.as_posix(), path.read_bytes()))
            if len(entries) > MAX_FILES:
                raise GitSnapshotInvalidError("Git Skill tree exceeds package limits")
        return tuple(entries)
