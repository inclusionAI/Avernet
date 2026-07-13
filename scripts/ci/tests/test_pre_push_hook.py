from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK_PATH = REPO_ROOT / ".githooks/pre-push"
AGENTS_PATH = REPO_ROOT / "AGENTS.md"
CLAUDE_PATH = REPO_ROOT / "CLAUDE.md"
ZERO_SHA = "0" * 40


def _run(
    repository: Path,
    *command: str,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=repository,
        check=check,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _git(repository: Path, *args: str) -> str:
    return _run(repository, "git", *args).stdout.strip()


def _write(repository: Path, relative_path: str, content: str) -> None:
    path = repository / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _create_repositories(root: Path) -> tuple[Path, Path, Path]:
    remote = root / "remote.git"
    publisher = root / "publisher"
    developer = root / "developer"

    remote.mkdir()
    _git(remote, "init", "--bare")

    publisher.mkdir()
    _git(publisher, "init", "--initial-branch=dev")
    _git(publisher, "config", "user.name", "CI Test")
    _git(publisher, "config", "user.email", "ci-test@example.com")
    _write(publisher, "README.md", "baseline\n")
    _git(publisher, "add", ".")
    _git(publisher, "commit", "-m", "baseline")
    _git(publisher, "remote", "add", "origin", str(remote))
    _git(publisher, "push", "-u", "origin", "dev")

    _run(root, "git", "clone", "--branch", "dev", str(remote), str(developer))
    _git(developer, "config", "user.name", "CI Test")
    _git(developer, "config", "user.email", "ci-test@example.com")
    return remote, publisher, developer


def _install_test_hook(developer: Path) -> Path:
    hook = developer / ".githooks/pre-push"
    hook.parent.mkdir(parents=True)
    shutil.copy2(HOOK_PATH, hook)
    hook.chmod(0o755)
    dispatcher = developer / "scripts/ci/pre_push.sh"
    dispatcher.parent.mkdir(parents=True)
    dispatcher.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
base=""
head=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --base) base="$2"; shift 2 ;;
    --head) head="$2"; shift 2 ;;
    *) exit 2 ;;
  esac
done
printf 'dispatch-base: %s\\n' "$base"
printf 'dispatch-head: %s\\n' "$head"
git diff --name-only "$base" "$head"
""",
        encoding="utf-8",
    )
    dispatcher.chmod(0o755)
    return hook


def _invoke_hook(
    developer: Path,
    remote: Path,
    hook: Path,
    feature_sha: str,
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    effective_env = os.environ.copy() if env is None else env.copy()
    if env is None:
        effective_env.pop("AVERNET_PRE_PUSH_MERGE_TARGET", None)
    push_record = (
        f"refs/heads/feature {feature_sha} refs/heads/feature {ZERO_SHA}\n"
    )
    return _run(
        developer,
        str(hook),
        "origin",
        str(remote),
        input_text=push_record,
        env=effective_env,
        check=check,
    )


def _create_feature_on_remote_target(
    publisher: Path,
    developer: Path,
    *,
    target_branch: str,
    target_path: str,
) -> str:
    _git(publisher, "switch", "-c", target_branch, "dev")
    _write(publisher, target_path, f"target-only change on {target_branch}\n")
    _git(publisher, "add", ".")
    _git(publisher, "commit", "-m", f"advance {target_branch}")
    target_sha = _git(publisher, "rev-parse", "HEAD")
    _git(publisher, "push", "origin", target_branch)

    _git(developer, "fetch", "origin", target_sha)
    _git(developer, "switch", "-c", "feature", target_sha)
    _write(developer, "src/bcs/feature.txt", "intended BCS change\n")
    _git(developer, "add", ".")
    _git(developer, "commit", "-m", "change only BCS")
    return _git(developer, "rev-parse", "HEAD")


class PrePushHookTest(unittest.TestCase):
    def test_refreshes_stale_default_target_before_selecting_modules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            remote, publisher, developer = _create_repositories(root)
            stale_target_sha = _git(developer, "rev-parse", "origin/dev")

            _write(publisher, "src/engine/target.txt", "target-only engine change\n")
            _git(publisher, "add", ".")
            _git(publisher, "commit", "-m", "advance dev with engine")
            fresh_target_sha = _git(publisher, "rev-parse", "HEAD")
            _git(publisher, "push", "origin", "dev")

            _git(
                developer,
                "fetch",
                "origin",
                fresh_target_sha,
            )
            _git(developer, "branch", "rebase-target", fresh_target_sha)
            self.assertEqual(_git(developer, "rev-parse", "origin/dev"), stale_target_sha)
            self.assertEqual(
                _git(developer, "rev-parse", "rebase-target"), fresh_target_sha
            )

            _git(developer, "switch", "-c", "feature", "rebase-target")
            _write(developer, "src/bcs/feature.txt", "intended BCS change\n")
            _git(developer, "add", ".")
            _git(developer, "commit", "-m", "change only BCS")
            feature_sha = _git(developer, "rev-parse", "HEAD")

            hook = _install_test_hook(developer)
            result = _invoke_hook(developer, remote, hook, feature_sha)

            self.assertIn("src/bcs/feature.txt", result.stdout)
            self.assertNotIn("src/engine/target.txt", result.stdout)

    def test_uses_git_config_merge_target_when_environment_is_unset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            remote, publisher, developer = _create_repositories(root)
            feature_sha = _create_feature_on_remote_target(
                publisher,
                developer,
                target_branch="release",
                target_path="src/baas/target.txt",
            )
            _git(
                developer,
                "config",
                "avernet.prePush.mergeTarget",
                "origin/release",
            )

            hook = _install_test_hook(developer)
            result = _invoke_hook(developer, remote, hook, feature_sha)

            self.assertIn("merge target: origin/release", result.stdout)
            self.assertIn("src/bcs/feature.txt", result.stdout)
            self.assertNotIn("src/baas/target.txt", result.stdout)

    def test_environment_merge_target_overrides_git_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            remote, publisher, developer = _create_repositories(root)
            feature_sha = _create_feature_on_remote_target(
                publisher,
                developer,
                target_branch="release",
                target_path="src/engine/target.txt",
            )
            _git(
                developer,
                "config",
                "avernet.prePush.mergeTarget",
                "origin/does-not-exist",
            )
            env = os.environ.copy()
            env["AVERNET_PRE_PUSH_MERGE_TARGET"] = "origin/release"

            hook = _install_test_hook(developer)
            result = _invoke_hook(
                developer,
                remote,
                hook,
                feature_sha,
                env=env,
            )

            self.assertIn("merge target: origin/release", result.stdout)
            self.assertIn("src/bcs/feature.txt", result.stdout)
            self.assertNotIn("src/engine/target.txt", result.stdout)

    def test_missing_configured_target_rejects_push_without_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            remote, _, developer = _create_repositories(root)
            _git(developer, "switch", "-c", "feature")
            _write(developer, "src/bcs/feature.txt", "intended BCS change\n")
            _git(developer, "add", ".")
            _git(developer, "commit", "-m", "change only BCS")
            feature_sha = _git(developer, "rev-parse", "HEAD")
            env = os.environ.copy()
            env["AVERNET_PRE_PUSH_MERGE_TARGET"] = "origin/does-not-exist"

            hook = _install_test_hook(developer)
            result = _invoke_hook(
                developer,
                remote,
                hook,
                feature_sha,
                env=env,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("failed to refresh", result.stderr)
            self.assertNotIn("dispatch-base:", result.stdout)

    def test_invalid_target_format_rejects_push_without_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            remote, _, developer = _create_repositories(root)
            _git(developer, "switch", "-c", "feature")
            _write(developer, "src/bcs/feature.txt", "intended BCS change\n")
            _git(developer, "add", ".")
            _git(developer, "commit", "-m", "change only BCS")
            feature_sha = _git(developer, "rev-parse", "HEAD")
            env = os.environ.copy()
            env["AVERNET_PRE_PUSH_MERGE_TARGET"] = "dev"

            hook = _install_test_hook(developer)
            result = _invoke_hook(
                developer,
                remote,
                hook,
                feature_sha,
                env=env,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must use <remote>/<branch>", result.stderr)
            self.assertNotIn("dispatch-base:", result.stdout)

    def test_unrelated_configured_target_rejects_push_without_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            remote, publisher, developer = _create_repositories(root)
            _git(publisher, "switch", "--orphan", "unrelated")
            _write(publisher, "UNRELATED.md", "unrelated history\n")
            _git(publisher, "add", ".")
            _git(publisher, "commit", "-m", "unrelated target")
            _git(publisher, "push", "origin", "unrelated")

            _git(developer, "switch", "-c", "feature")
            _write(developer, "src/bcs/feature.txt", "intended BCS change\n")
            _git(developer, "add", ".")
            _git(developer, "commit", "-m", "change only BCS")
            feature_sha = _git(developer, "rev-parse", "HEAD")
            env = os.environ.copy()
            env["AVERNET_PRE_PUSH_MERGE_TARGET"] = "origin/unrelated"

            hook = _install_test_hook(developer)
            result = _invoke_hook(
                developer,
                remote,
                hook,
                feature_sha,
                env=env,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no merge base", result.stderr)
            self.assertNotIn("dispatch-base:", result.stdout)

    def test_deletion_only_push_skips_target_fetch_and_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            remote, _, developer = _create_repositories(root)
            hook = _install_test_hook(developer)
            _git(developer, "remote", "remove", "origin")
            delete_record = (
                f"(delete) {ZERO_SHA} refs/heads/obsolete "
                f"{_git(developer, 'rev-parse', 'HEAD')}\n"
            )

            result = _run(
                developer,
                str(hook),
                "origin",
                str(remote),
                input_text=delete_record,
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key != "AVERNET_PRE_PUSH_MERGE_TARGET"
                },
            )

            self.assertNotIn("merge target sha:", result.stdout)
            self.assertNotIn("dispatch-base:", result.stdout)

    def test_agent_docs_define_the_pre_push_target_contract(self) -> None:
        agents = AGENTS_PATH.read_text(encoding="utf-8")
        claude = CLAUDE_PATH.read_text(encoding="utf-8")

        for expected in (
            "origin/dev",
            "AVERNET_PRE_PUSH_MERGE_TARGET",
            "avernet.prePush.mergeTarget",
            "merge-base",
            "src/backend/",
            "src/baas/",
            "src/engine/",
            "src/bcs/",
            "src/frontend/",
        ):
            self.assertIn(expected, agents)
        for expected in (
            "origin/dev",
            "AVERNET_PRE_PUSH_MERGE_TARGET",
            "avernet.prePush.mergeTarget",
            "AGENTS.md",
        ):
            self.assertIn(expected, claude)


if __name__ == "__main__":
    unittest.main()
