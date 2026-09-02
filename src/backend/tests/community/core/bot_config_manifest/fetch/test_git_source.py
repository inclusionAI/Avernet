"""Tests for git sources (``fetch/git_source.py``, W7, #1475).

Two kinds of tests live here, and the split is deliberate:

* **argv-shape tests** inject a fake runner and assert what the git CLI is
  asked to do — scheme guard, credential placement, no prompts. They exist
  because the https wire itself is not reachable from a test environment,
  and because "where the credential travels" is a property of the argv, not
  of the network.
* **behaviour tests** run real ``git`` subprocesses against bare repos
  created on disk by the fixtures — the same local-repo precedent as the
  schema suite's archive fixtures, and the only honest way to test that a
  shallow fetch, ref resolution and tree enumeration really work.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitSourceSpec,
    SubprocessGitClient,
    _GitCommandError,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
    FetchRefusedError,
)
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    ARCHIVE_MEMBER_LIMIT,
    FETCH_ENTRY_LIMITS,
    GIT_CHECKOUT_MEMBER_LIMIT,
    GIT_CHECKOUT_UNPACKED_LIMIT,
    GIT_FETCH_TIMEOUT_S,
    GIT_SINGLE_FILE_LIMIT,
)


def test_git_limits_align_with_the_archives_they_generalise():
    # A checkout is the same class of hazard as an unpacked archive: the
    # numbers align rather than invent a second dialect that drifts.
    assert GIT_CHECKOUT_UNPACKED_LIMIT == FETCH_ENTRY_LIMITS["resources_unpacked"]
    assert GIT_CHECKOUT_MEMBER_LIMIT == ARCHIVE_MEMBER_LIMIT
    assert GIT_SINGLE_FILE_LIMIT == FETCH_ENTRY_LIMITS["skills"]
    assert GIT_FETCH_TIMEOUT_S > 0


class _RecordingRunner:
    """Stands in for ``_real_run_git``: captures argv, answers scripted stdout."""

    def __init__(self, *, sha: str = "f" * 40, tree: str = "") -> None:
        self.argvs: list[list[str]] = []
        self.timeout: float | None = None
        self._sha = sha
        self._tree = tree

    def __call__(self, argv, *, cwd, timeout):
        self.argvs.append(list(argv))
        self.timeout = timeout
        if argv[1] == "rev-parse":
            return self._sha
        if argv[1] == "ls-tree":
            return self._tree
        return ""


def test_non_https_git_url_is_refused_before_any_subprocess():
    client = SubprocessGitClient(_run=_RecordingRunner())
    with pytest.raises(FetchRefusedError, match="https"):
        client.fetch(GitSourceSpec(url="http://git.corp/repo.git", ref="main"))


def test_credentials_travel_in_argv_config_never_in_the_remote_or_url():
    runner = _RecordingRunner(tree="100644 blob " + "a" * 40 + "\tpkg/skill.md\n")
    client = SubprocessGitClient(_run=runner)
    client.fetch(
        GitSourceSpec(url="https://git.corp/repo.git", ref="main"),
        headers={"Authorization": "Basic c2VjcmV0"},
    )
    fetch_argv = next(a for a in runner.argvs if "fetch" in a)
    assert any(
        part.startswith("http.extraHeader=Authorization: Basic")
        for part in fetch_argv
    ), fetch_argv
    # The credential rides the -c config, and nowhere else: not the remote,
    # not the fetch operands, not any other invocation.
    operands = fetch_argv[fetch_argv.index("fetch") + 1:]
    assert "Basic" not in " ".join(operands)
    remote_add = next(a for a in runner.argvs if a[1 : 3] == ["remote", "add"])
    assert "Basic" not in " ".join(remote_add)


def test_a_failed_git_command_is_a_fetch_failure_and_cleans_up(monkeypatch, tmp_path):
    class _Failing:
        def __init__(self):
            self.seen = 0

        def __call__(self, argv, *, cwd, timeout):
            self.seen += 1
            if argv[1] == "fetch":
                raise _GitCommandError("fetch", "remote: not found")
            return "" if argv[1] != "rev-parse" else "f" * 40

    # ``mkdtemp`` follows ``tempfile.gettempdir()``, which on macOS is the
    # per-user /var/folders dir — a hardcoded /tmp glob would pass vacuously.
    leftovers_before = set(
        p.name for p in Path(tempfile.gettempdir()).glob("manifest-git-*")
    )
    client = SubprocessGitClient(_run=_Failing())
    with pytest.raises(FetchFailedError, match="git fetch failed"):
        client.fetch(GitSourceSpec(url="https://git.corp/repo.git", ref="main"))
    leftovers_after = set(
        p.name for p in Path(tempfile.gettempdir()).glob("manifest-git-*")
    )
    assert leftovers_after == leftovers_before, "a failed fetch must leave no temp dir"


def _git(argv: list[str], *, cwd: Path) -> str:
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _serve_repo(
    tmp_path: Path,
    *,
    allow_any_sha: bool = False,
    symlink_member: bool = False,
    gitlink_member: bool = False,
) -> Path:
    """A bare repo on disk serving one committed tree over file://."""
    work = tmp_path / "src"
    (work / "pkg").mkdir(parents=True)
    (work / "pkg" / "skill.md").write_text("# a skill\n")
    (work / "root.txt").write_text("root\n")
    _git(["git", "init", "--quiet", "-b", "main"], cwd=work)
    if symlink_member:
        (work / "pkg" / "escape").symlink_to("/etc")
    _git(["git", "add", "."], cwd=work)
    if gitlink_member:
        # A gitlink row without its submodule contents: exactly the shape a
        # submodule author leaves in the index. Added after ``git add .`` —
        # ``git add .`` stages the deletion of index rows with no on-disk
        # path, so an earlier cacheinfo add would be wiped before the commit.
        _git(
            ["git", "update-index", "--add", "--cacheinfo",
             "160000,1111111111111111111111111111111111111111,vendor/sub"],
            cwd=work,
        )
    _git(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--quiet", "-m", "one"],
        cwd=work,
    )
    bare = tmp_path / "bare.git"
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", str(work), str(bare)],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    if allow_any_sha:
        _git(["git", "config", "uploadpack.allowAnySHA1InWant", "true"], cwd=bare)
    return bare


def _client() -> SubprocessGitClient:
    return SubprocessGitClient(allowed_schemes=frozenset({"https", "file"}))


@pytest.fixture
def serve(tmp_path):
    return _serve_repo(tmp_path)


def test_fetch_by_branch_and_read_the_tree(serve):
    bare = serve
    checkout = _client().fetch(
        GitSourceSpec(url=f"file://{bare}", ref="main", subpath="pkg")
    )
    assert checkout.sha
    assert checkout.files() == [("skill.md", b"# a skill\n")]


def test_read_file_requires_the_subpath_to_name_exactly_one_file(serve):
    checkout = _client().fetch(
        GitSourceSpec(url=f"file://{serve}", ref="main", subpath="pkg")
    )
    with pytest.raises(FetchRefusedError, match="directory|single file"):
        checkout.read_file()
    checkout_file = _client().fetch(
        GitSourceSpec(url=f"file://{serve}", ref="main", subpath="root.txt")
    )
    assert checkout_file.read_file() == b"root\n"


def test_subpath_escape_is_refused(serve):
    checkout = _client().fetch(
        GitSourceSpec(url=f"file://{serve}", ref="main")
    )
    for bad in ("../root.txt", "/etc/passwd", "pkg/../../root.txt"):
        with pytest.raises(FetchRefusedError):
            checkout.files(bad)


def test_a_symlink_member_is_refused(tmp_path):
    bare = _serve_repo(tmp_path, symlink_member=True)
    with pytest.raises(FetchRefusedError, match="forbidden member"):
        _client().fetch(GitSourceSpec(url=f"file://{bare}", ref="main"))


def test_a_gitlink_member_is_refused(tmp_path):
    bare = _serve_repo(tmp_path, gitlink_member=True)
    with pytest.raises(FetchRefusedError, match="forbidden member"):
        _client().fetch(GitSourceSpec(url=f"file://{bare}", ref="main"))


def test_unknown_ref_is_a_fetch_failure(serve):
    with pytest.raises(FetchFailedError):
        _client().fetch(GitSourceSpec(url=f"file://{serve}", ref="no-such-branch"))


def test_a_sha_ref_fetches_when_the_server_allows_it(tmp_path):
    bare = _serve_repo(tmp_path, allow_any_sha=True)
    first = _client().fetch(GitSourceSpec(url=f"file://{bare}", ref="main"))
    again = _client().fetch(GitSourceSpec(url=f"file://{bare}", ref=first.sha))
    assert again.sha == first.sha


def test_a_moved_tag_resolves_to_the_new_commit(tmp_path):
    bare = _serve_repo(tmp_path)
    first = _client().fetch(GitSourceSpec(url=f"file://{bare}", ref="main"))
    # Push a second commit through a fresh clone, then move a tag to it.
    work2 = tmp_path / "src2"
    subprocess.run(
        ["git", "clone", "--quiet", str(bare), str(work2)],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    (work2 / "new.txt").write_text("new\n")
    _git(["git", "add", "."], cwd=work2)
    _git(["git", "-c", "user.email=t@t", "-c", "user.name=t",
          "commit", "--quiet", "-m", "two"], cwd=work2)
    _git(["git", "push", "--quiet", "origin", "main"], cwd=work2)
    _git(["git", "tag", "release"], cwd=work2)
    _git(["git", "push", "--quiet", "-f", "origin", "release"], cwd=work2)
    second = _client().fetch(GitSourceSpec(url=f"file://{bare}", ref="release"))
    assert second.sha != first.sha
    assert any(path == "new.txt" for _, path in second.members)
