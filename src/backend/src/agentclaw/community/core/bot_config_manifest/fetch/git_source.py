"""Git sources — a shallow single-ref fetch through the git CLI (W7, #1475).

The contract is **git over HTTPS with header-injected credentials** — W3's
``header`` credential type — and it is deliberately host-agnostic (work-items
§W7: any HTTPS-reachable git service satisfies it). The implementation shells
out to ``git`` the way ``skill_center/services/git_sync.py`` already does,
rather than adding a pure-Python client dependency the corp dependency mirror
would have to carry too.

Three properties the shape of this module guarantees:

* **Refusal before the wire** — an https-only scheme check happens before any
  subprocess, so a refused scheme looks like W2's ``FetchRefusedError``, the
  class ``keep_last`` may not answer.
* **Read-only by construction** — ``fetch`` never evaluates server-controlled
  input: no hooks run on a fetch, no filters are requested, and the tree that
  comes back is enumerated *before* it is read, so a symlink or submodule
  member is refused before any platform code could follow it.
* **The credential stays in argv config** — ``-c http.extraHeader=...`` on the
  fetch invocation only. Never the URL (it would persist in receipts), never
  the environment (it would leak to child processes that read the whole env).

A failed or refused fetch removes its temporary directory before raising; a
successful one keeps it alive until the apply's ``SourceSession`` closes, and
the caller owns that lifetime.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from types import MappingProxyType
from typing import Callable, Mapping, Optional, Protocol, Sequence, Tuple
from urllib.parse import urlparse

from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
    FetchRefusedError,
)
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    GIT_CHECKOUT_MEMBER_LIMIT,
    GIT_FETCH_TIMEOUT_S,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: A full commit id, exactly as ``rev-parse FETCH_HEAD`` answers it.
_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")

#: The index modes a checkout may contain: plain files only. ``120000`` is a
#: symlink (``payload -> /etc`` is exactly the W2 hazard), ``160000`` is a
#: gitlink/submodule — both refused before any byte is read.
_ALLOWED_MODES = frozenset({"100644", "100755"})


@dataclass(frozen=True)
class GitSourceSpec:
    """One declared git source, wherever it is written (inline or ``sources``).

    ``auth`` is a credential **name** (W3's identifier), never a value.
    """

    url: str
    ref: str = "HEAD"
    subpath: Optional[str] = None
    mode: str = "non_strict"
    auth: Optional[str] = None


def git_receipt_url(url: str, sha: str, subpath: Optional[str]) -> str:
    """The canonical pseudo-URL a git-sourced entry's W11 receipt is filed under.

    Keyed on the *resolved* SHA: a ref that moved is a different address, so
    ``keep_last`` cannot be fooled into reusing the old record for a new
    commit, and two entries reading different paths out of one commit do not
    collide.
    """
    return f"git+{url}@{sha}:{subpath or ''}"


class _GitCommandError(Exception):
    """One git invocation failed; the CLI's stderr text is kept for the message."""

    def __init__(self, step: str, stderr: str) -> None:
        self.step = step
        self.stderr = stderr
        super().__init__(f"{step} failed: {stderr}")


def _real_run_git(argv: Sequence[str], *, cwd: Path, timeout: float) -> str:
    """One git invocation: argv as a list (no shell), stdout back, or the
    CLI's own failure as ``_GitCommandError``.

    ``GIT_TERMINAL_PROMPT=0`` and the two ``GIT_CONFIG_*`` overrides keep the
    invocation hermetic: a git prompt would hang an apply thread to its
    timeout, and a user-authored ``insteadOf`` rewrite in ~/.gitconfig would
    silently redirect this fetch somewhere the document never named — a
    credential-leaking hole, not a convenience.
    """
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/true",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    try:
        completed = subprocess.run(  # noqa: S603 - argv is list-built in this module
            list(argv), cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=env, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise _GitCommandError(argv[1], "timed out") from exc
    if completed.returncode != 0:
        raise _GitCommandError(argv[1], completed.stderr.strip() or "failed")
    return completed.stdout


@dataclass
class GitCheckout:
    """One fetched commit's tree, on disk, already proven safe to read.

    ``members`` is the tree enumerated by ``git ls-tree`` at fetch time —
    every member a plain file — and the readers below walk **only** those
    paths, so nothing outside the enumeration is ever touched.
    """

    root: Path
    sha: str
    url: str
    ref: str
    # (mode, path) pairs; the guarded readers land with the next wave over
    # this module and are the only sanctioned way to reach these.
    members: Tuple[Tuple[str, str], ...] = ()


class GitSourceClient(Protocol):
    """The seam the apply pipeline speaks to; ``SubprocessGitClient`` is v1's
    only implementation, and tests duck-type it."""

    def fetch(
        self, spec: GitSourceSpec, *, headers: Mapping[str, str] = ...
    ) -> GitCheckout: ...


class SubprocessGitClient:
    """Shallow single-ref fetch through the git CLI.

    ``_run`` is the test seam (argv-shape tests script it; behaviour tests
    use the default, which really spawns git). ``allowed_schemes`` is the
    deployment's git-transport dialect — https in production, widened by the
    local-fixture tests to ``file`` so real bare repos on disk can serve a
    fetch; it admits *destinations*, never behaviors.
    """

    def __init__(
        self,
        _run: Callable[..., str] = _real_run_git,
        *,
        allowed_schemes: frozenset[str] = frozenset({"https"}),
    ) -> None:
        self._run = _run
        self._allowed_schemes = allowed_schemes

    def fetch(
        self, spec: GitSourceSpec, *, headers: Mapping[str, str] = MappingProxyType({})
    ) -> GitCheckout:
        if urlparse(spec.url).scheme not in self._allowed_schemes:
            # Before any subprocess: a refused scheme is configuration, the
            # class of failure keep_last must not mask (W5's ruling).
            raise FetchRefusedError(
                f"git source url must be https, got {spec.url!r}"
            )
        if not spec.ref:
            raise FetchRefusedError("'ref' must be a non-empty string")

        root = Path(mkdtemp(prefix="manifest-git-"))
        config: list[str] = []
        for key, value in headers.items():
            config += ["-c", f"http.extraHeader={key}: {value}"]
        try:
            self._git(["init", "--quiet"], cwd=root)
            self._git(["remote", "add", "origin", spec.url], cwd=root)
            self._git(
                [*config, "fetch", "--quiet", "--depth=1", "origin", spec.ref],
                cwd=root,
            )
            sha = self._git(["rev-parse", "FETCH_HEAD"], cwd=root).strip()
            if not _SHA_RE.match(sha):
                raise FetchFailedError(
                    f"git fetch of ref {spec.ref!r} produced no commit id"
                )
            tree_out = self._git(["ls-tree", "-r", "FETCH_HEAD"], cwd=root)
            members = self._enumerate(tree_out)
            self._git(
                ["checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=root
            )
            logger.info(
                "[manifest.git] fetched host=%s ref=%s sha=%s files=%s",
                urlparse(spec.url).netloc, spec.ref, sha, len(members),
            )
            return GitCheckout(
                root=root, sha=sha, url=spec.url, ref=spec.ref, members=members
            )
        except FetchRefusedError:
            self._discard(root)
            raise
        except _GitCommandError as exc:
            self._discard(root)
            raise FetchFailedError(f"git {exc}") from exc
        except Exception:
            # Anything else (disk, os) still must not leak the temp dir.
            self._discard(root)
            raise

    def _git(self, argv: Sequence[str], *, cwd: Path) -> str:
        return self._run(["git", *argv], cwd=cwd, timeout=GIT_FETCH_TIMEOUT_S)

    def _enumerate(self, tree_out: str) -> Tuple[Tuple[str, str], ...]:
        """``ls-tree -r`` output → (mode, path) members, with the refusals."""
        members: list[tuple[str, str]] = []
        for line in tree_out.splitlines():
            meta, _, name = line.partition("\t")
            parts = meta.split()
            if len(parts) != 3 or not name:
                raise FetchFailedError("git tree listing was malformed")
            mode = parts[0]
            if mode not in _ALLOWED_MODES:
                raise FetchRefusedError(
                    f"git source contains a forbidden member {name!r}: "
                    "symlinks and submodules are refused"
                )
            members.append((mode, name))
        if not members:
            raise FetchRefusedError("the git ref resolved to an empty tree")
        if len(members) > GIT_CHECKOUT_MEMBER_LIMIT:
            raise FetchRefusedError(
                f"git tree exceeds the {GIT_CHECKOUT_MEMBER_LIMIT}-member cap"
            )
        return tuple(members)

    @staticmethod
    def _discard(root: Path) -> None:
        import shutil

        shutil.rmtree(root, ignore_errors=True)
