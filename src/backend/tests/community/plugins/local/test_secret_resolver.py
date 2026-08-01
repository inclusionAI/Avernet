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


# ── which config file is the live one ────────────────────────────────────────


def test_runtime_overlay_wins_over_the_bundled_config(tmp_path, monkeypatch):
    """A deployed singlebox assembles configs/ in cwd; that is the live file.

    ``YamlConfigProvider`` boots from ``cwd/configs`` when it holds both
    ``application.yaml`` and the overlay, so an operator editing that copy must
    be the one this resolver reads — otherwise they set a value exactly as
    instructed and see no effect.
    """
    runtime = tmp_path / "configs"
    runtime.mkdir()
    (runtime / "application.yaml").write_text("user_config: {}\n", encoding="utf-8")
    (runtime / "application-singlebox.yaml").write_text(
        'user_config:\n  openclaw:\n    skills_repo_url: "git@example.com:from-runtime.git"\n',
        encoding="utf-8",
    )
    bundled = _write_config(
        tmp_path,
        monkeypatch,
        """
user_config:
  openclaw:
    skills_repo_url: "git@example.com:from-bundled.git"
""",
    )
    assert bundled.parent != runtime, "the two copies must be distinct files"
    monkeypatch.chdir(tmp_path)

    secret = LocalSecretResolver().get_secret(_AIWORKBENCH_REPO_URL_SECRET_NAME)

    assert secret is not None
    assert secret.secret_value == "git@example.com:from-runtime.git"


def test_bundled_config_is_used_when_no_runtime_overlay_exists(tmp_path, monkeypatch):
    """The monorepo case: no assembled cwd/configs, so the subtree copy is live."""
    _write_config(
        tmp_path,
        monkeypatch,
        """
user_config:
  openclaw:
    skills_repo_url: "git@example.com:from-bundled.git"
""",
    )
    monkeypatch.chdir(tmp_path)

    secret = LocalSecretResolver().get_secret(_AIWORKBENCH_REPO_URL_SECRET_NAME)

    assert secret is not None
    assert secret.secret_value == "git@example.com:from-bundled.git"


# ── the gateway principal signing key is deliberately absent ─────────────────


def test_principal_signing_key_does_not_resolve_locally():
    """Singlebox has no key, so the public surface denies.

    A yaml stand-in for a secret store would be a committed-credential shape
    shipped empty. Without one the verifier has nothing to trust and
    /openapi/v1 answers 401 — where singlebox has always been. Giving it a key
    is a deliberate change, so this pins the absence rather than leaving it to
    be re-added by habit.
    """
    assert LocalSecretResolver().get_secret("gateway_principal_signing_key") is None


def test_no_signing_key_is_committed_to_the_singlebox_config():
    """Whatever else the shipped config grows, it must carry no signing key."""
    import yaml

    with LocalSecretResolver._SINGLEBOX_CONFIG_PATH.open(encoding="utf-8") as f:
        user_config = yaml.safe_load(f)["user_config"]

    assert "gateway_principal" not in user_config, (
        "the singlebox signing-key block was removed deliberately; re-adding it "
        "reintroduces a committed-credential shape for a knob that ships inert"
    )
    assert "gateway_principal_signing_key" not in user_config.get("secret_names", {})


def test_value_defined_only_in_the_base_config_still_resolves(tmp_path, monkeypatch):
    """The overlay carries what singlebox *changes*; the base carries the rest.

    ``YamlConfigProvider`` deep-merges application.yaml under the overlay, so a
    value left in the base is part of the effective config the app booted with.
    Reading the overlay alone would return None for it and send GitSyncService
    to its on-disk fallback for no reason.
    """
    runtime = tmp_path / "configs"
    runtime.mkdir()
    (runtime / "application.yaml").write_text(
        'user_config:\n  openclaw:\n    skills_repo_url: "git@example.com:from-base.git"\n',
        encoding="utf-8",
    )
    (runtime / "application-singlebox.yaml").write_text(
        "user_config:\n  workspace:\n    env_folder: overlay-only\n", encoding="utf-8"
    )
    # Point the bundled path at a non-existent file that still carries the
    # overlay's *name* — the pair search derives the overlay filename from it.
    monkeypatch.setattr(
        LocalSecretResolver,
        "_SINGLEBOX_CONFIG_PATH",
        tmp_path / "bundled" / "application-singlebox.yaml",
    )
    monkeypatch.chdir(tmp_path)

    secret = LocalSecretResolver().get_secret(_AIWORKBENCH_REPO_URL_SECRET_NAME)

    assert secret is not None
    assert secret.secret_value == "git@example.com:from-base.git"


def test_overlay_still_wins_over_the_base_for_the_same_key(tmp_path, monkeypatch):
    """Deep merge, not base-wins: the overlay overrides what it does define."""
    runtime = tmp_path / "configs"
    runtime.mkdir()
    (runtime / "application.yaml").write_text(
        'user_config:\n  openclaw:\n    skills_repo_url: "git@example.com:from-base.git"\n',
        encoding="utf-8",
    )
    (runtime / "application-singlebox.yaml").write_text(
        'user_config:\n  openclaw:\n    skills_repo_url: "git@example.com:from-overlay.git"\n',
        encoding="utf-8",
    )
    # Point the bundled path at a non-existent file that still carries the
    # overlay's *name* — the pair search derives the overlay filename from it.
    monkeypatch.setattr(
        LocalSecretResolver,
        "_SINGLEBOX_CONFIG_PATH",
        tmp_path / "bundled" / "application-singlebox.yaml",
    )
    monkeypatch.chdir(tmp_path)

    secret = LocalSecretResolver().get_secret(_AIWORKBENCH_REPO_URL_SECRET_NAME)

    assert secret is not None
    assert secret.secret_value == "git@example.com:from-overlay.git"
