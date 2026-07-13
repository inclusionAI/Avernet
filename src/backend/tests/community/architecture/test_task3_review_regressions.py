from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.community.architecture.test_no_singlebox_env_axis import _violations

_THIS_FILE = Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]


@pytest.mark.parametrize(
    "module_name",
    [
        "agentclaw.community.utils.env_utils",
        "agentclaw.community.core.workspace.path_factory",
    ],
)
def test_low_level_module_cold_import_does_not_initialize_di(module_name):
    proof = f"""
import importlib
import sys

importlib.import_module({module_name!r})
assert "agentclaw.community.di" not in sys.modules, (
    {module_name!r} + " must not initialize the DI composition-root package"
)
"""
    env = dict(os.environ)
    env["DEPLOY_PROFILE"] = "singlebox"
    env["SERVER_ENV"] = "dev"
    proc = subprocess.run(
        [sys.executable, "-c", proof],
        capture_output=True,
        text=True,
        env=env,
        cwd=_BACKEND_ROOT,
        timeout=120,
    )

    assert proc.returncode == 0, (
        f"Cold import failed for {module_name}.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_guard_keeps_rejecting_is_singlebox_symbols(tmp_path):
    source = tmp_path / "legacy_helper.py"
    source.write_text(
        "from legacy import is_singlebox\n"
        "def is_singlebox():\n"
        "    pass\n"
        "is_singlebox()\n",
        encoding="utf-8",
    )

    assert _violations(source, source_root=tmp_path) == [
        "legacy_helper.py:1 is_singlebox import",
        "legacy_helper.py:2 is_singlebox definition",
        "legacy_helper.py:4 is_singlebox call",
    ]


def test_guard_rejects_singlebox_literals_in_all_env_bypass_forms(tmp_path):
    source = tmp_path / "bypasses.py"
    source.write_text(
        "import os\n"
        'env = "singlebox"\n'
        "\n"
        "def global_writer():\n"
        "    global env\n"
        '    env = "singlebox"\n'
        "\n"
        "def outer():\n"
        '    env = "singlebox"\n'
        "    def inner():\n"
        "        nonlocal env\n"
        '        env = "singlebox"\n'
        "    return inner\n"
        "\n"
        '(os.getenv("SERVER_ENV") or "").lower() == "singlebox"\n'
        'config.runtime_env = "singlebox"\n'
        'config["runtime_env"] = "singlebox"\n'
        "\n"
        "def helper():\n"
        '    return "singlebox"\n',
        encoding="utf-8",
    )

    assert _violations(source, source_root=tmp_path) == [
        "bypasses.py:2 singlebox literal outside canonical profile definitions",
        "bypasses.py:6 singlebox literal outside canonical profile definitions",
        "bypasses.py:9 singlebox literal outside canonical profile definitions",
        "bypasses.py:12 singlebox literal outside canonical profile definitions",
        "bypasses.py:15 singlebox literal outside canonical profile definitions",
        "bypasses.py:16 singlebox literal outside canonical profile definitions",
        "bypasses.py:17 singlebox literal outside canonical profile definitions",
        "bypasses.py:20 singlebox literal outside canonical profile definitions",
    ]


def test_guard_allows_only_canonical_profile_literals(tmp_path):
    profile_path = tmp_path / "di" / "profile.py"
    profile_path.parent.mkdir()
    profile_path.write_text(
        "class DeployProfile:\n"
        '    SINGLEBOX = "singlebox"\n'
        '_RETIRED_SERVER_ENV_VALUES = frozenset({"singlebox"})\n',
        encoding="utf-8",
    )
    env_utils_path = tmp_path / "utils" / "env_utils.py"
    env_utils_path.parent.mkdir()
    env_utils_path.write_text(
        '_LOCAL_DEPLOY_PROFILES = frozenset({"test", "singlebox", "corp_test"})\n',
        encoding="utf-8",
    )
    noncanonical_profile_path = tmp_path / "di" / "other_profile.py"
    noncanonical_profile_path.write_text(
        'message = "singlebox"\n', encoding="utf-8"
    )

    assert _violations(profile_path, source_root=tmp_path) == []
    assert _violations(env_utils_path, source_root=tmp_path) == []
    assert _violations(noncanonical_profile_path, source_root=tmp_path) == [
        "di/other_profile.py:1 singlebox literal outside canonical profile definitions"
    ]


def test_guard_ignores_profile_members_and_non_exact_strings(tmp_path):
    source = tmp_path / "legal_profile.py"
    source.write_text(
        "profile is DeployProfile.SINGLEBOX\n"
        'display_label = "singlebox profile"\n'
        'Parser.from_string("singlebox-profile")\n',
        encoding="utf-8",
    )

    assert _violations(source, source_root=tmp_path) == []


def test_guard_rejects_case_insensitive_literals_and_noncanonical_case(tmp_path):
    bypass = tmp_path / "case_bypass.py"
    bypass.write_text(
        "import os\n"
        '(os.getenv("SERVER_ENV") or "").upper() == "SINGLEBOX"\n'
        'mixed_case = "SiNgLeBoX"\n',
        encoding="utf-8",
    )
    profile_path = tmp_path / "di" / "profile.py"
    profile_path.parent.mkdir()
    profile_path.write_text(
        "class DeployProfile:\n"
        '    SINGLEBOX = "SINGLEBOX"\n'
        '_RETIRED_SERVER_ENV_VALUES = frozenset({"SiNgLeBoX"})\n',
        encoding="utf-8",
    )
    env_utils_path = tmp_path / "utils" / "env_utils.py"
    env_utils_path.parent.mkdir()
    env_utils_path.write_text(
        '_LOCAL_DEPLOY_PROFILES = frozenset({"SINGLEBOX"})\n',
        encoding="utf-8",
    )

    assert _violations(bypass, source_root=tmp_path) == [
        "case_bypass.py:2 singlebox literal outside canonical profile definitions",
        "case_bypass.py:3 singlebox literal outside canonical profile definitions",
    ]
    assert _violations(profile_path, source_root=tmp_path) == [
        "di/profile.py:2 singlebox literal outside canonical profile definitions",
        "di/profile.py:3 singlebox literal outside canonical profile definitions",
    ]
    assert _violations(env_utils_path, source_root=tmp_path) == [
        "utils/env_utils.py:1 singlebox literal outside canonical profile definitions"
    ]


def test_guard_requires_exact_canonical_assignment_shapes(tmp_path):
    profile_path = tmp_path / "di" / "profile.py"
    profile_path.parent.mkdir()
    profile_path.write_text(
        "def configure():\n"
        '    _RETIRED_SERVER_ENV_VALUES = frozenset({"singlebox"})\n'
        "class DeployProfile:\n"
        '    SINGLEBOX = ALSO_SINGLEBOX = "singlebox"\n',
        encoding="utf-8",
    )
    env_utils_path = tmp_path / "utils" / "env_utils.py"
    env_utils_path.parent.mkdir()
    env_utils_path.write_text(
        "def configure():\n"
        '    _LOCAL_DEPLOY_PROFILES = frozenset({"test", "singlebox", "corp_test"})\n',
        encoding="utf-8",
    )

    assert _violations(profile_path, source_root=tmp_path) == [
        "di/profile.py:2 singlebox literal outside canonical profile definitions",
        "di/profile.py:4 singlebox literal outside canonical profile definitions",
    ]
    assert _violations(env_utils_path, source_root=tmp_path) == [
        "utils/env_utils.py:2 singlebox literal outside canonical profile definitions"
    ]
