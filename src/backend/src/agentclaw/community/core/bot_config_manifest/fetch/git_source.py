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
    GIT_CHECKOUT_UNPACKED_LIMIT,
    GIT_FETCH_TIMEOUT_S,
    GIT_SINGLE_FILE_LIMIT,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: A full commit id, exactly as ``rev-parse FETCH_HEAD`` answers it.
_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")

#: The index modes a checkout may contain: plain files only. ``120000`` is a
#: symlink (``payload -> /etc`` is exactly the W2 hazard), ``160000`` is a
#: gitlink/submodule — both refused before any byte is read.
_ALLOWED_MODES = frozenset({"100644", "100755"})


def _require_safe(what: str, value: Optional[str]) -> None:
    """A subpath is refused if absolute or escaping — the PUT-time rule
    re-asked at apply time, because a stored document may have drifted."""
    if value is None:
        return
    if value.startswith("/") or value.startswith("\\"):
        raise FetchRefusedError(f"{what} must be relative, got {value!r}")
    parts = value.replace("\\", "/").split("/")
    if ".." in parts:
        raise FetchRefusedError(f"{what} may not contain '..': {value!r}")


def _is_plain_name(name: str) -> bool:
    if name.startswith("/") or ".." in name.replace("\\", "/").split("/"):
        return False
    return not name.startswith("~")


def _under_subpath(member: str, subpath: Optional[str]) -> Optional[str]:
    """The member's path relative to ``subpath`` (segment-boundary matched),
    or ``None`` when outside it — the skills materialiser's own arithmetic,
    on members the enumeration already proved plain."""
    _require_safe("subpath", subpath)
    if subpath is None or subpath == "":
        return member
    prefix = subpath.replace("\\", "/").strip("/")
    parts = member.split("/")
    mine = [p for p in prefix.split("/") if p]
    if len(parts) <= len(mine):
        return None
    if parts[: len(mine)] != mine:
        return None
    return "/".join(parts[len(mine) :])


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
    paths, so nothing outside the enumeration is ever touched. ``subpath``
    is the spec's own, remembered so a reader called bare re-uses what the
    document declared instead of silently meaning "the whole tree".
    """

    root: Path
    sha: str
    url: str
    ref: str
    # (mode, path) pairs; the readers below are the only sanctioned way to
    # reach these.
    members: Tuple[Tuple[str, str], ...] = ()
    subpath: Optional[str] = None

    # ── the readers ─────────────────────────────────────────────────────────
    #
    # Only enumerated members are read, only from this checkout's own root,
    # and only inside the limits: the enumeration proved every member a plain
    # file at fetch time, so a reader cannot be walked out of the temp dir by
    # anything the repository author left in the tree.

    def files(
        self, subpath: Optional[str] = None
    ) -> list[tuple[str, bytes]]:
        """Every file under ``subpath`` as (relative path, bytes).

        Raises ``FetchRefusedError`` — the pre-wire class — for an unsafe
        subpath, an empty selection, or a limit blown while reading.
        """
        if subpath is None:
            subpath = self.subpath
        selected = [
            (mode, _under_subpath(name, subpath), name)
            for mode, name in self.members
        ]
        selected = [(m, r, n) for m, r, n in selected if r is not None]
        if not selected:
            raise FetchRefusedError(
                f"the git tree contains nothing under subpath {subpath!r}"
            )
        out: list[tuple[str, bytes]] = []
        total = 0
        for mode, rel, name in selected:
            payload = self._read_member(name, mode)
            total += len(payload)
            if total > GIT_CHECKOUT_UNPACKED_LIMIT:
                raise FetchRefusedError(
                    "git checkout exceeds the "
                    f"{GIT_CHECKOUT_UNPACKED_LIMIT}-byte unpacked cap"
                )
            out.append((rel, payload))
        return out

    def read_file(self, subpath: Optional[str] = None) -> bytes:
        """The single file a subpath names — the identity-category road."""
        if subpath is None:
            subpath = self.subpath
        _require_safe("subpath", subpath)
        if subpath is None or subpath == "":
            raise FetchRefusedError(
                "read_file: the source's subpath must name a single file"
            )
        hit = [(mode, name) for mode, name in self.members if name == subpath]
        if not hit:
            # No member equals the subpath exactly: either it names a
            # directory (members live under it) or it names nothing.
            if any(name.startswith(subpath + "/") for _, name in self.members):
                raise FetchRefusedError(
                    f"subpath {subpath!r} names a directory, not a single file"
                )
            raise FetchRefusedError(
                f"the git tree has no file at subpath {subpath!r}"
            )
        mode, name = hit[0]
        payload = self._read_member(name, mode)
        if len(payload) > GIT_SINGLE_FILE_LIMIT:
            raise FetchRefusedError(
                "a single git tree file exceeds the "
                f"{GIT_SINGLE_FILE_LIMIT}-byte cap: {subpath!r}"
            )
        return payload

    def _read_member(self, name: str, mode: str) -> bytes:
        if not _is_plain_name(name):
            raise FetchRefusedError(f"refusing to read git member {name!r}")
        base = self.root.resolve()
        target = (self.root / name).resolve()
        if base != target and base not in target.parents:
            raise FetchRefusedError(
                f"git member {name!r} resolves outside the checkout"
            )
        return target.read_bytes()


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
                root=root, sha=sha, url=spec.url, ref=spec.ref, members=members,
                subpath=spec.subpath,
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
