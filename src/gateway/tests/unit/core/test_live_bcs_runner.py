"""Static safety checks for the opt-in live BCS runner."""

from pathlib import Path

RUNNER = Path(__file__).resolve().parents[3] / "scripts" / "test_live_bcs_forwarding.sh"


def test_live_bcs_runner_requires_the_started_process_to_own_its_port() -> None:
    source = RUNNER.read_text()

    assert 'lsof -nP -a -p "${bcs_pid}" -iTCP:"${port}" -sTCP:LISTEN' in source
    assert "BCS process ${bcs_pid} never owned selected loopback port ${port}" in source


def test_live_bcs_runner_pins_local_principal_environment_and_bounds_health() -> None:
    source = RUNNER.read_text()

    assert "-u SERVER_ENV -u REAL_SERVER_ENV -u ALIPAY_APP_ENV" in source
    assert "SERVER_ENV=local" in source
    assert "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE=" in source
    assert "BCS_SECRET_BCN_GROUP_SESSION_WS_JWT=" in source
    assert "--connect-timeout 2 --max-time 5" in source
