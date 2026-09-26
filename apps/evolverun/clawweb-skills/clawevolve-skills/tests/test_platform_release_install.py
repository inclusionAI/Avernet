"""Build and install the real release into an isolated workspace."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run(*args, env):
    result = subprocess.run(args, env=env, text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def release(tmp_path_factory):
    temporary = tmp_path_factory.mktemp("platform-release")
    env = os.environ | {
        "CLAWEVOLVE_SKILLS_ROOT": str(ROOT),
        "CLAWEVOLVE_SKILLS_DIST_DIR": str(temporary),
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
    }
    run("/bin/bash", str(ROOT / "scripts/package_clawevolve_skills.sh"),
        "clawevolve-20260921-v1", env=env)
    return temporary / "clawevolve-20260921-v1"


def test_release_installs_code_runtime_and_retires_managed_stage_skill(release, tmp_path):
    runtime = tmp_path / "clawevolve-skills"
    old = runtime / "clawevolve-stage"
    old.mkdir(parents=True)
    (old / "SKILL.md").write_text("old orchestration Skill")
    (old / ".clawevolve-version").write_text("clawevolve-20260920-v1")
    env = os.environ | {"SECBAAS_SANDBOX_BACKEND": "local_proc",
        "OPENCLAW_WORKSPACE": str(tmp_path), "SKILL_BASE_DIR": str(runtime),
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}
    runner = release / "clawevolve_async_runner.sh"
    installed = json.loads(run("/bin/bash", str(runner), "--stage", "init", env=env))
    assert installed["result"] == "installed"
    assert not old.exists()
    entry = runtime / "platform/clawevolve_runtime/runner.py"
    assert "--action" in run(sys.executable, "-B", str(entry), "--help", env=env)
    assert entry.read_bytes() == (ROOT / "platform/clawevolve_runtime/runner.py").read_bytes()
    again = json.loads(run("/bin/bash", str(runner), "--stage", "init", env=env))
    assert again["result"] == "unchanged"
    entry.unlink()
    run("/bin/bash", str(runner), "--stage", "init", env=env)
    assert entry.is_file(), "same-release health check must repair a missing code runtime"
    # An unrelated user's directory is not owned by the release installer.
    old.mkdir()
    (old / "SKILL.md").write_text("user-owned")
    run("/bin/bash", str(runner), "--stage", "init", env=env)
    assert (old / "SKILL.md").read_text() == "user-owned"


def test_release_manifest_covers_the_exact_platform_files(release):
    reporter = "clawevolve_startup_failure.py"
    assert (release / reporter).read_bytes() == (ROOT / "scripts" / reporter).read_bytes()
    records = [line.split("\t") for line in (release / "RELEASE_VERSION").read_text().splitlines()]
    runtime = next(row for row in records if row[:2] == ["runtime", "platform"])
    assert not any(row[:2] == ["skill", "clawevolve-stage"] for row in records)
    archive_name = next(row[1] for row in records if row[0] == "archive_file")
    with tarfile.open(release / archive_name) as archive:
        files = {m.name.removeprefix("skills/platform/"): archive.extractfile(m).read()
            for m in archive.getmembers() if m.isfile() and m.name.startswith("skills/platform/")}
        assert not any("clawevolve-stage/" in m.name for m in archive.getmembers())
    assert files
    assert not any("__pycache__" in name or ".pytest_cache" in name for name in files)
    digest_input = "".join(f"./{name}  {hashlib.sha256(files[name]).hexdigest()}\n" for name in sorted(files))
    assert hashlib.sha256(digest_input.encode()).hexdigest() == runtime[3]
