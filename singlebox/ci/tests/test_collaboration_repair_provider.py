"""Coverage-only Provider provisioning and disposable config authorization."""
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
import tomlkit

HELPER = Path(__file__).resolve().parents[1] / "collaboration_repair_provider.py"
spec = importlib.util.spec_from_file_location("repair_provider", HELPER)
provider = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provider)


def test_provision_requires_coverage_before_network_or_writes(tmp_path, monkeypatch):
    monkeypatch.delenv("SINGLEBOX_COVERAGE", raising=False)
    with pytest.raises(ValueError, match="coverage"):
        provider.provision("http://127.0.0.1:21000", "http://127.0.0.1:1234", tmp_path / "provider.json")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("url", ["https://127.0.0.1", "http://example.com", "http://127.0.0.1?x=1", "http://a:b@127.0.0.1"])
def test_provision_rejects_nonlocal_or_ambiguous_endpoints(tmp_path, monkeypatch, url):
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    with pytest.raises(ValueError, match="loopback"):
        provider.provision(url, "http://127.0.0.1:1234", tmp_path / "provider.json")


def test_provision_writes_private_minimal_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={
            "provider_id": "prv_coverage", "provider_admin_token": "test-token",
            "bcs_to_provider_token": "test-token",
        })

    client_class = httpx.Client
    monkeypatch.setattr(provider.httpx, "Client", lambda **kwargs: client_class(
        transport=httpx.MockTransport(handle), **kwargs))
    target = tmp_path / "provider.json"
    provider.provision("http://127.0.0.1:21000", "http://127.0.0.1:1234", target)
    assert json.loads(target.read_text()) == {
        "provider_id": "prv_coverage", "provider_admin_token": "test-token"}
    assert target.stat().st_mode & 0o777 == 0o600
    assert len(calls) == 1
    assert calls[0].url.path == "/providers"
    assert calls[0].headers["X-Mock-User-Id"] == "singlebox-repair-coverage"
    payload = json.loads(calls[0].content)
    assert payload.pop("slug").startswith("singlebox-repair-")
    assert payload == {
        "name": "Singlebox collaboration repair coverage",
        "auth": {"mode": "static_bearer"}, "protocol_version": "2.0",
        "webhook_url": "http://127.0.0.1:1234/provider/webhook",
    }
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("data", [{}, {"provider_id": "bad id", "provider_admin_token": "test-token"},
                                  {"provider_id": "prv_good", "provider_admin_token": ""}])
def test_invalid_provider_response_never_writes_fixture(tmp_path, monkeypatch, data):
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    client_class = httpx.Client
    monkeypatch.setattr(provider.httpx, "Client", lambda **kwargs: client_class(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data)), **kwargs))
    with pytest.raises(ValueError, match="fixture"):
        provider.provision("http://127.0.0.1:21000", "http://127.0.0.1:1234", tmp_path / "provider.json")
    assert list(tmp_path.iterdir()) == []


def test_authorize_uses_structured_toml_and_preserves_other_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    fixture = tmp_path / "provider.json"
    fixture.write_text(json.dumps({"provider_id": "prv_coverage", "provider_admin_token": "test-token"}))
    config = tmp_path / "bcs.toml"
    config.write_text('allowed_switch_provider_ids = [\n "prv_existing", # keep\n]\nport = 21000\n[llm]\ntype = "none"\n')
    provider.authorize(fixture, config)
    provider.authorize(fixture, config)
    assert tomlkit.parse(config.read_text()).unwrap() == {
        "allowed_switch_provider_ids": ["prv_existing", "prv_coverage"],
        "port": 21000, "llm": {"type": "none"}}
    assert "provider_admin_token" not in config.read_text()
    assert "# keep" in config.read_text()


def test_authorize_requires_coverage(tmp_path, monkeypatch):
    monkeypatch.delenv("SINGLEBOX_COVERAGE", raising=False)
    with pytest.raises(ValueError, match="coverage"):
        provider.authorize(tmp_path / "provider.json", tmp_path / "bcs.toml")


def test_authorize_rejects_invalid_configuration_without_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    fixture = tmp_path / "provider.json"
    fixture.write_text(json.dumps({"provider_id": "prv_coverage", "provider_admin_token": "test-token"}))
    config = tmp_path / "bcs.toml"
    original = 'allowed_switch_provider_ids = "not-an-array"\n'
    config.write_text(original)
    with pytest.raises(ValueError, match="allowlist"):
        provider.authorize(fixture, config)
    assert config.read_text() == original


def test_cli_registration_failure_never_logs_response_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.setattr("sys.argv", [str(HELPER), "provision", "--bcs-url", "http://127.0.0.1:21000",
                                    "--mock-url", "http://127.0.0.1:1234", "--fixture", str(tmp_path / "provider.json")])
    client_class = httpx.Client
    monkeypatch.setattr(provider.httpx, "Client", lambda **kwargs: client_class(
        transport=httpx.MockTransport(lambda request: httpx.Response(500, text="provider_admin_token=must-not-log")), **kwargs))
    with pytest.raises(SystemExit) as error:
        provider.main()
    assert error.value.code == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Coverage Provider fixture operation failed\n"
    assert list(tmp_path.iterdir()) == []
