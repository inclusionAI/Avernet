"""Tests for the production external Space Skill source adapter."""

from pathlib import Path
import subprocess

import pytest

from agentclaw.community.plugin_api.space_skill_source import (
    ExactSkillPackageFetchError,
    GitSnapshotInvalidError,
)
from agentclaw.community.plugins.community.space_skill_source import (
    CommunitySpaceSkillSource,
)


def test_git_root_selection_excludes_nested_skill_packages(monkeypatch) -> None:
    source = CommunitySpaceSkillSource()
    monkeypatch.setattr(source, "_resolved_addresses", lambda _host: ("8.8.8.8",))

    def fake_run(command, *, cwd, environment, **limits):
        assert set(environment) == {
            "HOME",
            "PATH",
            "GIT_TERMINAL_PROMPT",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_CONFIG_GLOBAL",
            "GIT_ASKPASS",
        }
        if "clone" in command:
            assert limits["max_bytes"] > 0
            assert "http.followRedirects=false" in command
            assert "http.curloptResolve=example.com:443:8.8.8.8" in command
            checkout = Path(command[-1])
            checkout.mkdir()
            (checkout / "SKILL.md").write_text(
                "---\nname: root-skill\ndescription: root\n---\n"
            )
            nested = checkout / "nested"
            nested.mkdir()
            (nested / "SKILL.md").write_text(
                "---\nname: nested-skill\ndescription: nested\n---\n"
            )
            (nested / "secret.txt").write_text("not part of root package")
            return ""
        if command[-2:] == ["branch", "--show-current"]:
            return "main\n"
        return "a" * 40 + "\n"

    monkeypatch.setattr(source, "_run", fake_run)

    snapshot = source.fetch_git_snapshot(
        git_url="https://example.com/repo.git", branch=None, subdir=None
    )

    assert snapshot.source_subdir == ""
    assert [path for path, _content in snapshot.files] == ["SKILL.md"]


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1"],
)
def test_git_snapshot_rejects_every_non_public_resolved_address(
    monkeypatch, address: str
) -> None:
    source = CommunitySpaceSkillSource()
    monkeypatch.setattr(source, "_resolved_addresses", lambda _host: (address,))
    source._run = lambda *_args, **_kwargs: pytest.fail("git must not run")

    with pytest.raises(GitSnapshotInvalidError):
        source.fetch_git_snapshot(
            git_url="https://git.example/repo.git", branch=None, subdir=None
        )


def test_git_clone_watchdog_kills_acquisition_that_exceeds_disk_budget(
    monkeypatch, tmp_path: Path
) -> None:
    source = CommunitySpaceSkillSource()
    (tmp_path / "oversized.pack").write_bytes(b"xx")

    class RunningClone:
        pid = 123
        returncode = None

        @staticmethod
        def communicate(timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(["git", "clone"], timeout)
            return "", ""

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: RunningClone())
    killed = []
    monkeypatch.setattr("os.killpg", lambda pid, signal: killed.append((pid, signal)))

    with pytest.raises(GitSnapshotInvalidError, match="acquisition limit"):
        source._run(
            ["git", "clone"],
            cwd=None,
            environment={},
            resource_root=tmp_path,
            max_bytes=1,
        )

    assert killed


def test_exact_package_download_stops_before_buffering_oversized_response(
    monkeypatch,
) -> None:
    source = CommunitySpaceSkillSource()
    exited = []

    class StreamingResponse:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            exited.append(True)

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def iter_content(*, chunk_size):
            assert chunk_size > 0
            yield b"aa"
            yield b"bb"

    def fake_get(url, *, timeout, stream):
        assert url == "https://download.example/exact.zip"
        assert timeout == 60
        assert stream is True
        return StreamingResponse()

    monkeypatch.setattr(
        "agentclaw.community.plugins.community.space_skill_source.MAX_COMPRESSED_BYTES",
        3,
    )
    monkeypatch.setattr(
        "agentclaw.community.plugins.community.space_skill_source.requests.get",
        fake_get,
    )

    with pytest.raises(ExactSkillPackageFetchError, match="too large"):
        source.fetch_exact_package(
            url="https://download.example/exact.zip", expected_sha256="a" * 64
        )

    assert exited == [True]
