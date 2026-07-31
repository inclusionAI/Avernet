"""Unit tests for LocalSecretResolver."""

from agentclaw.community.plugins.local.secret_resolver import LocalSecretResolver

_AIWORKBENCH_REPO_URL_SECRET_NAME = "other_manual_agentclaw_aiworkbench_repo_url"
_PRINCIPAL_SIGNING_KEY_SECRET_NAME = "gateway_principal_signing_key"


def _write_config(tmp_path, monkeypatch, body: str):
    config_path = tmp_path / "application-singlebox.yaml"
    config_path.write_text(body.lstrip(), encoding="utf-8")
    monkeypatch.setattr(LocalSecretResolver, "_SINGLEBOX_CONFIG_PATH", config_path)
    return config_path


def test_aiworkbench_repo_url_is_read_from_singlebox_yaml(tmp_path, monkeypatch):
    config_path = tmp_path / "application-singlebox.yaml"
    config_path.write_text(
        """
user_config:
  openclaw:
    skills_repo_url: "git@code.teamclaw.com:security_release/aiworkbench.git"
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(LocalSecretResolver, "_SINGLEBOX_CONFIG_PATH", config_path)

    secret = LocalSecretResolver().get_secret(_AIWORKBENCH_REPO_URL_SECRET_NAME)

    assert secret is not None
    assert secret.secret_user == "aiworkbench_repo_url"
    assert secret.secret_value == "git@code.teamclaw.com:security_release/aiworkbench.git"


def test_aiworkbench_repo_url_missing_in_singlebox_yaml_returns_none(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "application-singlebox.yaml"
    config_path.write_text("user_config: {}\n", encoding="utf-8")
    monkeypatch.setattr(LocalSecretResolver, "_SINGLEBOX_CONFIG_PATH", config_path)

    assert LocalSecretResolver().get_secret(_AIWORKBENCH_REPO_URL_SECRET_NAME) is None


def test_aiworkbench_repo_url_unreadable_singlebox_yaml_returns_none(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        LocalSecretResolver,
        "_SINGLEBOX_CONFIG_PATH",
        tmp_path / "missing-application-singlebox.yaml",
    )

    assert LocalSecretResolver().get_secret(_AIWORKBENCH_REPO_URL_SECRET_NAME) is None


# ── the gateway principal signing key ────────────────────────────────────────


def test_principal_signing_key_is_read_from_singlebox_yaml(tmp_path, monkeypatch):
    """Singlebox has no secret store, so the operator's key comes from the yaml."""
    _write_config(
        tmp_path,
        monkeypatch,
        """
user_config:
  gateway_principal:
    signing_key: "a-local-shared-secret-of-32-bytes-plus"
""",
    )

    secret = LocalSecretResolver().get_secret(_PRINCIPAL_SIGNING_KEY_SECRET_NAME)

    assert secret is not None
    assert secret.secret_value == "a-local-shared-secret-of-32-bytes-plus"


def test_principal_signing_key_ships_unset_and_resolves_to_none(tmp_path, monkeypatch):
    """The shipped config carries the block but no value — a committed shared
    secret would be a committed credential. Absent means the public API denies."""
    _write_config(
        tmp_path,
        monkeypatch,
        """
user_config:
  gateway_principal:
    signing_key: ""
""",
    )

    assert LocalSecretResolver().get_secret(_PRINCIPAL_SIGNING_KEY_SECRET_NAME) is None


def test_principal_signing_key_whitespace_only_resolves_to_none(tmp_path, monkeypatch):
    """A key that is accidentally whitespace must not look configured."""
    _write_config(
        tmp_path,
        monkeypatch,
        """
user_config:
  gateway_principal:
    signing_key: "   "
""",
    )

    assert LocalSecretResolver().get_secret(_PRINCIPAL_SIGNING_KEY_SECRET_NAME) is None


def test_principal_signing_key_missing_block_resolves_to_none(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, "user_config: {}\n")

    assert LocalSecretResolver().get_secret(_PRINCIPAL_SIGNING_KEY_SECRET_NAME) is None


def test_shipped_singlebox_config_registers_the_name_but_no_key():
    """The real shipped file: wiring live, value absent, nothing committed."""
    import yaml

    with LocalSecretResolver._SINGLEBOX_CONFIG_PATH.open(encoding="utf-8") as f:
        user_config = yaml.safe_load(f)["user_config"]

    assert (
        user_config["secret_names"]["gateway_principal_signing_key"]
        == _PRINCIPAL_SIGNING_KEY_SECRET_NAME
    ), "the name must be registered or the resolver is never consulted"
    assert not user_config["gateway_principal"]["signing_key"], (
        "no signing key may be committed to the repository"
    )
