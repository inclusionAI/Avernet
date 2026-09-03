"""Tests for git sources (``fetch/git_source.py``, W7, #1475).

Three kinds of tests live here, and the split is deliberate:

* **argv/env-shape tests** inject a fake runner and assert what the git CLI
  is asked to do — scheme guard, credential placement, no prompts, where the
  env comes from. They exist because the https wire itself is not reachable
  from a test environment, and because "where the credential travels" is a
  property of the invocation, not of the network.
* **behaviour tests** run real ``git`` subprocesses against bare repos
  created on disk by the fixtures — the same local-repo precedent as the
  schema suite's archive fixtures, and the only honest way to test that a
  shallow fetch, ref resolution and tree enumeration really work, including
  the quotepath round-trip of non-ASCII filenames.
* **guard tests** drive the enumeration and the readers directly with crafted
  listings — the byte caps and unquoting rules fire on declared sizes the
  same way whether the bytes are real or not.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitCheckout,
    GitSourceSpec,
    SubprocessGitClient,
    _GitCommandError,
    _real_run_git,
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
    # numbers align rather than inventing a second dialect that drifts.
    assert GIT_CHECKOUT_UNPACKED_LIMIT == FETCH_ENTRY_LIMITS["resources_unpacked"]
    assert GIT_CHECKOUT_MEMBER_LIMIT == ARCHIVE_MEMBER_LIMIT
    assert GIT_SINGLE_FILE_LIMIT == FETCH_ENTRY_LIMITS["skills"]
    assert GIT_FETCH_TIMEOUT_S > 0


class _RecordingRunner:
    """Stands in for ``_real_run_git``: captures argv/env, answers scripted stdout."""

    def __init__(self, *, sha: str = "f" * 40, tree: str = "") -> None:
        self.argvs: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.timeout: float | None = None
        self._sha = sha
        self._tree = tree

    def __call__(self, argv, *, cwd, timeout, env):
        self.argvs.append(list(argv))
        self.envs.append(dict(env))
        self.timeout = timeout
        if argv[1] == "rev-parse":
            return self._sha
        if argv[1] == "ls-tree":
            return self._tree
        return ""


def _argv_client(runner: _RecordingRunner) -> SubprocessGitClient:
    return SubprocessGitClient(_run=runner, env={"PATH": "/usr/bin"})


def test_non_https_git_url_is_refused_before_any_subprocess():
    runner = _RecordingRunner()
    client = _argv_client(runner)
    with pytest.raises(FetchRefusedError, match="https") as exc_info:
        client.fetch(
            GitSourceSpec(url="http://git.corp/repo.git?token=S", ref="main")
        )
    # The refusal text never echoes the URL — its query strings are where
    # signed-source tokens live.
    assert "token=S" not in str(exc_info.value)
    assert runner.argvs == []


def test_a_url_with_control_characters_is_refused_before_any_subprocess():
    runner = _RecordingRunner()
    client = _argv_client(runner)
    with pytest.raises(FetchRefusedError, match="control characters"):
        client.fetch(
            GitSourceSpec(url="https://git.corp/repo\n.git", ref="main")
        )
    assert runner.argvs == []


def test_a_ref_that_reads_as_a_git_option_is_refused_before_any_subprocess():
    runner = _RecordingRunner()
    client = _argv_client(runner)
    with pytest.raises(FetchRefusedError, match="git option"):
        client.fetch(
            GitSourceSpec(url="https://git.corp/repo.git", ref="--upload-pack=x")
        )
    assert runner.argvs == []


def test_credentials_travel_in_env_config_never_in_argv_or_the_url():
    runner = _RecordingRunner(
        tree="100644 blob " + "a" * 40 + " 12\tpkg/skill.md\n"
    )
    client = _argv_client(runner)
    client.fetch(
        GitSourceSpec(url="https://git.corp/repo.git", ref="main"),
        headers={"Authorization": "Basic c2VjcmV0"},
    )
    # The credential rides GIT_CONFIG_* env (readable only by this user),
    # and nowhere else: not any argv (ps shows argv to every local user),
    # not any URL, not the remote config the fetch reads argv-free.
    for argv, env in zip(runner.argvs, runner.envs):
        assert "Basic" not in " ".join(argv), argv
        assert "c2VjcmV0" not in " ".join(argv), argv
    fetch_at = runner.argvs.index(
        next(a for a in runner.argvs if a[1:2] == ["fetch"])
    )
    fetch_env = runner.envs[fetch_at]
    assert fetch_env["GIT_CONFIG_COUNT"] == "1"
    assert fetch_env["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    assert fetch_env["GIT_CONFIG_VALUE_0"] == "Authorization: Basic c2VjcmV0"
    # No invocation carries the source URL: the remote was written to the
    # checkout's own .git/config, not passed to a CLI.
    assert not any("repo.git" in " ".join(a) for a in runner.argvs)


def test_the_feeding_env_is_sanitised_and_hermetic_per_invocation():
    # Base env comes from the caller (the composition root in production);
    # every GIT_* it still carried is dropped, the hermetic knobs are
    # applied on top of whatever remains, and a fetch without credentials
    # carries no credential config at all.
    runner = _RecordingRunner(
        tree="100644 blob " + "a" * 40 + " 12\tpkg/skill.md\n"
    )
    client = SubprocessGitClient(
        _run=runner,
        env={"PATH": "/usr/bin", "MARKER": "kept", "GIT_ARGS": "--evil",
             "GIT_TERMINAL_PROMPT": "1"},
    )
    client.fetch(
        GitSourceSpec(url="https://git.corp/repo.git", ref="main"),
    )
    for env in runner.envs:
        assert env["MARKER"] == "kept"
        assert env["PATH"] == "/usr/bin"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_CONFIG_GLOBAL"] == os.devnull
        assert "GIT_ARGS" not in env
        assert "GIT_CONFIG_COUNT" not in env  # no headers this fetch


def test_real_run_git_runs_the_env_it_is_given_verbatim(tmp_path):
    # _real_run_git is the executor seam: the COMPOSITION (base env, GIT_*
    # drop, hermetic overrides, credential config) happens in _git and is
    # pinned by the argv/env-shape tests above. What this seam must never do
    # is reach for the ambient environment instead of the one it was handed.
    completed = _real_run_git(
        ["printenv"],
        cwd=tmp_path,
        timeout=10.0,
        env={"PATH": os.environ.get("PATH", os.defpath), "MARKER": "kept"},
    )
    seen = dict(
        line.split("=", 1) for line in completed.splitlines() if "=" in line
    )
    assert seen["MARKER"] == "kept"
    # Only what was handed in: the real ambient environment (which certainly
    # has more than PATH and MARKER) was not consulted.
    assert set(seen) <= {"PATH", "MARKER", "PWD", "OLDPWD", "SHLVL", "_"}


def test_a_failed_git_command_is_a_failure_and_cleans_up(monkeypatch, tmp_path):
    class _Failing:
        def __init__(self):
            self.seen = 0

        def __call__(self, argv, *, cwd, timeout, env):
            self.seen += 1
            if argv[1] == "fetch":
                raise _GitCommandError("fetch", "exit 128")
            return "" if argv[1] != "rev-parse" else "f" * 40

    # ``mkdtemp`` follows ``tempfile.gettempdir()``, which on macOS is the
    # per-user /var/folders dir — a hardcoded /tmp glob would pass vacuously.
    leftovers_before = set(
        p.name for p in Path(tempfile.gettempdir()).glob("manifest-git-*")
    )
    client = SubprocessGitClient(_run=_Failing(), env={"PATH": "/usr/bin"})
    with pytest.raises(FetchFailedError, match="git fetch failed"):
        client.fetch(GitSourceSpec(url="https://git.corp/repo.git", ref="main"))
    leftovers_after = set(
        p.name for p in Path(tempfile.gettempdir()).glob("manifest-git-*")
    )
    assert leftovers_after == leftovers_before, "a failed fetch must leave no temp dir"


def test_failure_text_carries_the_step_only_never_stderr(tmp_path):
    # The FetchFailedError is the carrier into the persisted apply report, so
    # its message must be report-safe: git's stderr echoes the full source
    # URL on transport failures (query strings included), and it is dropped
    # everywhere — step and exit status are all that survive. Pinned against
    # a REAL failing invocation whose stderr carries both.
    with pytest.raises(_GitCommandError) as exc_info:
        _real_run_git(
            ["sh", "-c",
             "echo \"fatal: unable to access 'https://x/y?token=S'\" 1>&2; exit 3"],
            cwd=tmp_path,
            timeout=10.0,
            env={"PATH": os.environ.get("PATH", os.defpath)},
        )
    assert "token=S" not in str(exc_info.value)
    assert "fatal" not in str(exc_info.value)
    assert "exit 3" in str(exc_info.value)
    wrapped = FetchFailedError(f"git {exc_info.value}")
    assert "token=S" not in str(wrapped)
    assert str(wrapped) == "git -c failed: exit 3"


def test_the_tree_byte_cap_refuses_before_checkout_writes_the_tree():
    size = GIT_CHECKOUT_UNPACKED_LIMIT + 1
    runner = _RecordingRunner(
        tree="100644 blob " + "a" * 40 + f" {size}\tpkg/skill.md\n"
    )
    client = _argv_client(runner)
    with pytest.raises(FetchRefusedError, match="unpacked cap"):
        client.fetch(GitSourceSpec(url="https://git.corp/repo.git", ref="main"))
    steps = [a[1] for a in runner.argvs]
    assert "ls-tree" in steps
    assert "checkout" not in steps, "the refusal must fire before the tree lands on disk"


def test_quoted_member_names_are_unquoted_by_the_enumeration():
    runner = _RecordingRunner(
        tree='100644 blob ' + "a" * 40 + ' 12\t"\\350\\257\\204\\344\\273\\267.md"\n'
    )
    client = _argv_client(runner)
    checkout = client.fetch(
        GitSourceSpec(url="https://git.corp/repo.git", ref="main")
    )
    # \350\257\204... octal-escapes to 评价.md — quotepath's default for any
    # name outside printable ASCII. Unquoted, it can match a declared
    # subpath; as the raw literal it never could.
    assert [name for _, name, _ in checkout.members] == ["评价.md"]
    assert checkout.tree_bytes == 12


def test_a_member_name_that_is_not_utf8_is_refused_by_its_quoted_form():
    runner = _RecordingRunner(
        tree='100644 blob ' + "a" * 40 + ' 4\t"caf\\351.md"\n'
    )
    client = _argv_client(runner)
    with pytest.raises(FetchRefusedError, match="not valid UTF-8"):
        client.fetch(
            GitSourceSpec(url="https://git.corp/repo.git", ref="main")
        )


def test_read_file_refuses_by_declared_size_before_reading(tmp_path):
    # No file is ever created on disk under the member's name: the refusal
    # must answer on the *declared* size, before a byte is paid for.
    checkout = GitCheckout(
        root=tmp_path,
        sha="a" * 40,
        url="https://git.corp/repo.git",
        ref="main",
        members=(("100644", "big.bin", GIT_SINGLE_FILE_LIMIT + 1),),
    )
    with pytest.raises(FetchRefusedError, match="exceeds the"):
        checkout.read_file("big.bin")
    assert not (tmp_path / "big.bin").exists()


def test_files_refuses_a_member_that_blows_the_category_cap(tmp_path):
    (tmp_path / "small.txt").write_bytes(b"x" * 4)
    (tmp_path / "large.txt").write_bytes(b"y" * 32)
    checkout = GitCheckout(
        root=tmp_path,
        sha="a" * 40,
        url="https://git.corp/repo.git",
        ref="main",
        members=(("100644", "small.txt", 4), ("100644", "large.txt", 32)),
    )
    with pytest.raises(FetchRefusedError, match="exceeds the .*-byte cap"):
        checkout.files(None, file_limit=16)


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
    unicode_member: bool = False,
) -> Path:
    """A bare repo on disk serving one committed tree over file://."""
    work = tmp_path / "src"
    (work / "pkg").mkdir(parents=True)
    (work / "pkg" / "skill.md").write_text("# a skill\n")
    (work / "root.txt").write_text("root\n")
    if unicode_member:
        (work / "pkg" / "评价.md").write_text("# 评价\n")
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
    # Behaviour tests run real git, so the env owes a PATH; ``file`` widens
    # the scheme dialect for the on-disk bare repos the fixtures serve.
    return SubprocessGitClient(
        allowed_schemes=frozenset({"https", "file"}),
        env={"PATH": os.environ.get("PATH", os.defpath)},
    )


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


def test_a_unicode_member_name_is_enumerated_and_readable(tmp_path):
    bare = _serve_repo(tmp_path, unicode_member=True)
    checkout = _client().fetch(
        GitSourceSpec(url=f"file://{bare}", ref="main", subpath="pkg")
    )
    # git quotes the non-ASCII name in its listing; the round-trip means the
    # member matches the subpath's directory and the file reads by its real
    # name on disk.
    assert ("评价.md", "# 评价\n".encode()) in checkout.files()


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
    assert any(path == "new.txt" for _, path, _ in second.members)
