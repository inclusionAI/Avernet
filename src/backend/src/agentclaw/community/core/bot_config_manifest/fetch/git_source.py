"""Git sources — a shallow single-ref fetch through the git CLI (W7, #1475).

The contract is **git over HTTPS with header-injected credentials** — W3's
``header`` credential type — and it is deliberately host-agnostic (work-items
§W7: any HTTPS-reachable git service satisfies it). The implementation shells
out to ``git`` the way ``skill_center/services/git_sync.py`` already does,
rather than adding a pure-Python client dependency the corp dependency mirror
would have to carry too.

Five properties the shape of this module guarantees:

* **Refusal before the wire** — an https-only scheme check happens before any
  subprocess, so a refused scheme looks like W2's ``FetchRefusedError``, the
  class ``keep_last`` may not answer.
* **Read-only by construction** — ``fetch`` never evaluates server-controlled
  input: no hooks run on a fetch, no filters are requested, and the tree that
  comes back is enumerated *before* it is read, so a symlink or submodule
  member is refused before any platform code could follow it.
* **Report-safe failure text** — the carrier of a git failure into the apply
  report is ``FetchFailedError``'s message, and that message carries only the
  git step and its exit status. stderr is dropped everywhere: git prints the
  full source URL (query strings included — where signed-source tokens live)
  into its own failures, and W2's ruling — never the URL, host-only logging —
  is the transport-wide contract this client now matches.
* **Sized before it is read** — the tree is listed with ``ls-tree -l`` at
  fetch time, so the member count, the tree's total bytes and every member's
  individual size are known and enforced *before* ``git checkout`` writes the
  tree to disk or any byte is read into memory. The refusals a hostile
  repository meets (member cap, unpacked cap, single-file cap) all fire on
  declared sizes, not after the damage.
* **The credential stays in env config** — ``GIT_CONFIG_KEY``/``VALUE`` pairs
  on the fetch invocation only. Never the URL (it would persist in receipts),
  never ``-c`` argv (``ps`` shows argv to every local user), never the
  environment the parent hands down wholesale: the base env is injected by
  the composition root, every ``GIT_*`` knob it still carried is dropped, and
  only this module's own hermetic overrides ride along.

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

#: The hermetic git knobs every invocation owns. Applied on top of a base env
#: with every ambient ``GIT_*`` key already dropped, so a user-authored
#: ``insteadOf`` rewrite in ``~/.gitconfig``, or an inherited prompt setting,
#: cannot redirect a fetch the document never named — a credential-leaking
#: hole, not a convenience.
_HERMETIC_ENV: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "/bin/true",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


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


def _unquote_ls_tree_name(name: str) -> str:
    """Undo git's ``core.quotepath`` C-quoting of a listed member name.

    ``git ls-tree`` writes any name containing bytes outside printable ASCII
    C-style quoted and octal-escaped (``"caf\\351.md"``, ``"\\350\\257\\204
    .md"``), so an unquoted round-trip is required before a name can ever
    match a declared subpath — without it, every non-ASCII filename in a
    repository silently selects nothing. The quoted form itself is pure
    ASCII, which is what makes it safe to echo back in the refusal below.
    """
    if not (len(name) >= 2 and name.startswith('"') and name.endswith('"')):
        return name
    body = name[1:-1]
    out = bytearray()
    i = 0
    simple = {"\\": 0x5C, '"': 0x22, "a": 0x07, "b": 0x08, "f": 0x0C,
              "n": 0x0A, "r": 0x0D, "t": 0x09, "v": 0x0B}
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out += ch.encode()
            i += 1
            continue
        if i + 1 >= len(body):
            break
        nxt = body[i + 1]
        if nxt in simple:
            out.append(simple[nxt])
            i += 2
        elif nxt.isdigit():
            digits = body[i + 1 : i + 4]
            if len(digits) == 3 and all(d.isdigit() for d in digits):
                out.append(int(digits, 8))
                i += 4
            else:
                i += 2
        else:
            i += 2
    try:
        return out.decode()
    except UnicodeDecodeError:
        # A repository may carry filenames in any legacy locale. A document's
        # subpaths are schema-validated UTF-8, so such a member could never be
        # addressed — refuse it by its (ASCII) quoted form rather than let it
        # break the decode or hide behind a silent match failure.
        raise FetchRefusedError(
            "the git tree contains a member whose name is not valid UTF-8 "
            f"({name!r}); it cannot be addressed by a manifest subpath"
        )


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
    """One git invocation failed. The step and exit status are kept — stderr
    is deliberately NOT: it echoes the full source URL, and the reason
    ``FetchFailedError`` carries lands in the persisted apply report."""

    def __init__(self, step: str, detail: str) -> None:
        self.step = step
        super().__init__(f"{step} failed: {detail}")


def _real_run_git(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
    env: Mapping[str, str],
) -> str:
    """One git invocation: argv as a list (no shell), stdout back, or the
    CLI's own failure as ``_GitCommandError``.

    ``env`` is the composed environment — base env from the composition root,
    ``GIT_*`` keys dropped, this module's hermetic overrides and the
    invocation's credential config applied. See the module docstring for why
    the parent environment is never read here.
    """
    try:
        completed = subprocess.run(  # noqa: S603 - argv is list-built in this module
            list(argv), cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=dict(env), stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise _GitCommandError(argv[1], "timed out") from exc
    if completed.returncode != 0:
        raise _GitCommandError(argv[1], f"exit {completed.returncode}")
    return completed.stdout


@dataclass
class GitCheckout:
    """One fetched commit's tree, on disk, already proven safe to read.

    ``members`` is the tree enumerated by ``git ls-tree -r -l`` at fetch time
    — every member a plain file, with its declared size — and the readers
    below walk **only** those paths, so nothing outside the enumeration is
    ever touched. ``subpath`` is the spec's own, remembered so a reader called
    bare re-uses what the document declared instead of silently meaning "the
    whole tree".
    """

    root: Path
    sha: str
    url: str
    ref: str
    # (mode, path, size) triples; the readers below are the only sanctioned
    # way to reach these. Sizes are the ones git declared for the blob at
    # fetch time — the authority every cap below consults BEFORE a byte is
    # paid for, the same discipline `fetch/unpack.py` streams by.
    members: Tuple[Tuple[str, str, int], ...] = ()
    subpath: Optional[str] = None

    @property
    def tree_bytes(self) -> int:
        """The whole tree's declared size — what the apply ledger charges."""
        return sum(size for _, _, size in self.members)

    # ── the readers ─────────────────────────────────────────────────────────
    #
    # Only enumerated members are read, only from this checkout's own root,
    # and only inside the limits: the enumeration proved every member a plain
    # file at fetch time, so a reader cannot be walked out of the temp dir by
    # anything the repository author left in the tree.

    def files(
        self,
        subpath: Optional[str] = None,
        *,
        file_limit: Optional[int] = None,
    ) -> list[tuple[str, bytes]]:
        """Every file under ``subpath`` as (relative path, bytes).

        Raises ``FetchRefusedError`` — the pre-wire class — for an unsafe
        subpath, an empty selection, or a member blowing the single-file or
        unpacked cap. Both caps check the **declared** size before the member
        is read into memory.
        """
        if subpath is None:
            subpath = self.subpath
        limit = GIT_SINGLE_FILE_LIMIT if file_limit is None else file_limit
        selected = [
            (mode, _under_subpath(name, subpath), name, size)
            for mode, name, size in self.members
        ]
        selected = [(m, r, n, s) for m, r, n, s in selected if r is not None]
        if not selected:
            raise FetchRefusedError(
                f"the git tree contains nothing under subpath {subpath!r}"
            )
        if sum(size for _, _, _, size in selected) > GIT_CHECKOUT_UNPACKED_LIMIT:
            raise FetchRefusedError(
                "git checkout exceeds the "
                f"{GIT_CHECKOUT_UNPACKED_LIMIT}-byte unpacked cap"
            )
        out: list[tuple[str, bytes]] = []
        for mode, rel, name, size in selected:
            if size > limit:
                raise FetchRefusedError(
                    "a git tree file exceeds the "
                    f"{limit}-byte cap: {rel!r}"
                )
            payload = self._read_member(name, mode)
            out.append((rel, payload))
        return out

    def read_file(
        self, subpath: Optional[str] = None, *, file_limit: Optional[int] = None
    ) -> bytes:
        """The single file a subpath names — the identity-category road."""
        if subpath is None:
            subpath = self.subpath
        _require_safe("subpath", subpath)
        if subpath is None or subpath == "":
            raise FetchRefusedError(
                "read_file: the source's subpath must name a single file"
            )
        hit = [
            (mode, name, size)
            for mode, name, size in self.members
            if name == subpath
        ]
        if not hit:
            # No member equals the subpath exactly: either it names a
            # directory (members live under it) or it names nothing.
            if any(name.startswith(subpath + "/") for _, name, _ in self.members):
                raise FetchRefusedError(
                    f"subpath {subpath!r} names a directory, not a single file"
                )
            raise FetchRefusedError(
                f"the git tree has no file at subpath {subpath!r}"
            )
        mode, name, size = hit[0]
        limit = GIT_SINGLE_FILE_LIMIT if file_limit is None else file_limit
        if size > limit:
            # Declared size, checked before the read: a hostile repository's
            # oversized blob must not be loaded to be refused.
            raise FetchRefusedError(
                "a single git tree file exceeds the "
                f"{limit}-byte cap: {subpath!r}"
            )
        return self._read_member(name, mode)

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
    fetch; it admits *destinations*, never behaviors. ``env`` is the base
    subprocess environment, owed by the composition root (production) or the
    test (unit rigs) — this module never reads the ambient environment, and
    whatever ``GIT_*`` the base still carries is dropped before the hermetic
    overrides apply.
    """

    def __init__(
        self,
        _run: Optional[Callable[..., str]] = None,
        *,
        allowed_schemes: frozenset[str] = frozenset({"https"}),
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._run = _run if _run is not None else _real_run_git
        self._allowed_schemes = allowed_schemes
        self._base_env: Mapping[str, str] = (
            env if env is not None else MappingProxyType({})
        )

    def fetch(
        self, spec: GitSourceSpec, *, headers: Mapping[str, str] = MappingProxyType({})
    ) -> GitCheckout:
        if any(ch in spec.url for ch in "\r\n\x00"):
            # Before any subprocess, and before the remote is written: a URL
            # with control characters could inject config lines into
            # ``.git/config`` (and ``urlparse`` silently strips them, so the
            # scheme check below would still pass) — the same "untrusted URL
            # shape" family the guarded fetcher refuses.
            raise FetchRefusedError("untrusted git source url: control characters")
        if urlparse(spec.url).scheme not in self._allowed_schemes:
            # Before any subprocess: a refused scheme is configuration, the
            # class of failure keep_last must not mask (W5's ruling). The
            # scheme alone is named — never the URL, whose query strings are
            # where signed-source tokens live.
            raise FetchRefusedError(
                f"git source url must be https, got scheme "
                f"{urlparse(spec.url).scheme!r}"
            )
        if not spec.ref:
            raise FetchRefusedError("'ref' must be a non-empty string")
        self._require_safe_ref(spec.ref)

        root = Path(mkdtemp(prefix="manifest-git-"))
        try:
            self._git(["init", "--quiet"], cwd=root)
            self._write_remote(root, spec.url)
            self._git(
                ["fetch", "--quiet", "--depth=1", "origin", spec.ref],
                cwd=root,
                headers=headers,
            )
            sha = self._git(["rev-parse", "FETCH_HEAD"], cwd=root).strip()
            if not _SHA_RE.match(sha):
                raise FetchFailedError(
                    f"git fetch of ref {spec.ref!r} produced no commit id"
                )
            tree_out = self._git(
                ["ls-tree", "-r", "-l", "FETCH_HEAD"], cwd=root
            )
            members = self._enumerate(tree_out)
            self._git(
                ["checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=root
            )
            logger.info(
                "[manifest.git] fetched host=%s ref=%s sha=%s files=%s "
                "bytes=%s",
                urlparse(spec.url).netloc, spec.ref, sha, len(members),
                sum(size for _, _, size in members),
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
            # Host only, never stderr: git's failure text echoes the full
            # source URL on transport failures — the exact carrier W2's
            # report-safety ruling exists to keep out of the apply report.
            logger.warning(
                "[manifest.git] host=%s ref=%s step=%s failed",
                urlparse(spec.url).netloc, spec.ref, exc.step,
            )
            raise FetchFailedError(f"git {exc}") from exc
        except Exception:
            # Anything else (disk, os) still must not leak the temp dir.
            self._discard(root)
            raise

    @staticmethod
    def _require_safe_ref(ref: str) -> None:
        """A ref that could be read as a git option is refused *before* the
        subprocess that would honor it.

        The argv is a list (no shell), so the classical injection is not
        available — this guard is about the argument *position*: ``fetch``
        reads a leading ``-`` as an option (``--upload-pack=<cmd>`` executes
        a command), and a ref a document can name must stay an operand.
        """
        if ref.startswith("-"):
            raise FetchRefusedError(
                "'ref' may not start with '-' (it must name a branch, tag or "
                "commit id, never a git option)"
            )

    @staticmethod
    def _write_remote(root: Path, url: str) -> None:
        """Declare the ``origin`` remote by writing ``.git/config`` directly.

        ``git remote add`` would put the URL on the subprocess argv, where
        ``ps`` shows it to every local user (a query string can carry a
        signed-source token). The file the remote lands in instead is the
        temp checkout's own config — created 0700 by ``mkdtemp`` and removed
        when the checkout closes — and control characters in the URL were
        already refused above, so the appended block cannot inject config
        lines.
        """
        config = root / ".git" / "config"
        # Idempotent in a real run (``git init`` made the directory); the
        # no-op creation keeps the same block writable under the scripted
        # test runner, where no real git ever ran.
        config.parent.mkdir(parents=True, exist_ok=True)
        with config.open("a", encoding="utf-8") as handle:
            handle.write(
                '[remote "origin"]\n'
                f"\turl = {url}\n"
                "\tfetch = +refs/heads/*:refs/remotes/origin/*\n"
            )

    def _git(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        headers: Mapping[str, str] = MappingProxyType({}),
    ) -> str:
        """One invocation with hermetic overrides and, for the fetch, the
        credential as ``GIT_CONFIG_*`` env (readable only by this user, never
        the world-readable argv)."""
        extra: dict[str, str] = {}
        for index, (key, value) in enumerate(dict(headers).items()):
            extra[f"GIT_CONFIG_KEY_{index}"] = "http.extraHeader"
            extra[f"GIT_CONFIG_VALUE_{index}"] = f"{key}: {value}"
        if extra:
            extra["GIT_CONFIG_COUNT"] = str(len(headers))
        env = {
            key: value
            for key, value in self._base_env.items()
            if not key.startswith("GIT_")
        }
        env.update(_HERMETIC_ENV)
        env.update(extra)
        return self._run(["git", *argv], cwd=cwd, timeout=GIT_FETCH_TIMEOUT_S, env=env)

    def _enumerate(self, tree_out: str) -> Tuple[Tuple[str, str, int], ...]:
        """``ls-tree -r -l`` output → (mode, path, size) members, with the
        refusals. Sizes are git's own declaration — trusted for every cap,
        while bytes are only ever read after the cap says they fit."""
        members: list[tuple[str, str, int]] = []
        total = 0
        for line in tree_out.splitlines():
            meta, _, name = line.partition("\t")
            parts = meta.split()
            if len(parts) != 4 or not name:
                raise FetchFailedError("git tree listing was malformed")
            mode, _type, _sha, size_text = parts
            if mode not in _ALLOWED_MODES:
                # The quoted form is pure ASCII — safe to name in the reason.
                raise FetchRefusedError(
                    f"git source contains a forbidden member {name!r}: "
                    "symlinks and submodules are refused"
                )
            if not size_text.isdigit():
                raise FetchFailedError("git tree listing was malformed")
            size = int(size_text)
            member = _unquote_ls_tree_name(name)
            members.append((mode, member, size))
            total += size
        if not members:
            raise FetchRefusedError("the git ref resolved to an empty tree")
        if len(members) > GIT_CHECKOUT_MEMBER_LIMIT:
            raise FetchRefusedError(
                f"git tree exceeds the {GIT_CHECKOUT_MEMBER_LIMIT}-member cap"
            )
        if total > GIT_CHECKOUT_UNPACKED_LIMIT:
            # Before ``git checkout`` writes the tree: the disk cost of a
            # repository whose ref names a few enormous blobs is refused with
            # the bytes still only in the pack, never materialised.
            raise FetchRefusedError(
                "git tree exceeds the "
                f"{GIT_CHECKOUT_UNPACKED_LIMIT}-byte unpacked cap "
                f"({total} bytes declared)"
            )
        return tuple(members)

    @staticmethod
    def _discard(root: Path) -> None:
        import shutil

        shutil.rmtree(root, ignore_errors=True)
