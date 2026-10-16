"""Regression coverage for real BaaS startup callback identity and exec user."""
import shlex
import subprocess
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.models import AllocatedDevice
from agentclaw.community.core.devices.services.baas_container_init import (
    BaasContainerInitializer, _admin_background_command,
)


@pytest.mark.parametrize("props,expected", [({}, "BOT-current"), ({"client_id": "explicit-current"}, "explicit-current")])
def test_initializer_uses_binding_device_identity_when_legacy_props_omit_client_id(props, expected):
    initializer = BaasContainerInitializer(MagicMock())
    for method in ("_run_baas_bootstrap", "_run_baas_install_engine", "_run_baas_setup_sync_service", "_ensure_baas_engine_dirs", "_create_baas_skill_symlink_conf", "_apply_baas_skill_symlinks", "_write_codefuse_token", "dispatch_watchdog"):
        setattr(initializer, method, MagicMock())
    initializer.run(
        bot_uuid="BOT-current", device=AllocatedDevice(device_id="BOT-current", device_provider="baas", device_props=props),
        engine="openclaw", bot_type="personal", bot_id="bot-1", owner_id="owner-1",
        callback_token="callback-fixture", startup_identity="publish-1", admins=None,
    )
    assert initializer.dispatch_watchdog.call_args.kwargs["client_id"] == expected


@pytest.mark.parametrize("user", ["root", "admin"])
def test_background_command_handles_both_exec_transport_users(tmp_path, user):
    logfile = tmp_path / "startup.log"
    command = _admin_background_command("/usr/bin/printf READY", str(logfile))
    assert subprocess.run(["bash", "-n", "-c", command], capture_output=True).returncode == 0
    # The stub validates the exact non-login su contract without changing UID.
    script = (
        f"id() {{ printf '%s\\n' {shlex.quote(user)}; }}\n"
        "su() { [ \"$1\" = admin ] && [ \"$2\" = -c ] || return 91; bash -c \"$3\nwait\"; }\n"
        + command + "\nwait\n"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert logfile.read_text() == "READY"
