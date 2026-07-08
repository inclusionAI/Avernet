"""Unit tests for LocalSecretResolver."""

from agentclaw.community.plugins.local.secret_resolver import LocalSecretResolver

_AIWORKBENCH_REPO_URL_SECRET_NAME = "other_manual_agentclaw_aiworkbench_repo_url"


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
