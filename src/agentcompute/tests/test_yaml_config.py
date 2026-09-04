from agentcompute.community.bootstrap import detect_env, load_config


def test_detect_env_resolution(monkeypatch):
    monkeypatch.delenv("DEPLOY_ENV", raising=False)
    monkeypatch.delenv("SERVER_ENV", raising=False)
    monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)
    assert detect_env() == ""

    monkeypatch.setenv("DEPLOY_ENV", "prod")
    assert detect_env() == "prod"


def test_deep_merge_overlay(tmp_path, monkeypatch):
    base = tmp_path / "application.yaml"
    base.write_text(
        "app:\n  name: x\n  port: 8000\nuser_config:\n  plugins:\n    llm:\n      provider: stub\n      model: m1\n",
        encoding="utf-8",
    )
    overlay = tmp_path / "application-dev.yaml"
    overlay.write_text("user_config:\n  plugins:\n    llm:\n      model: m2\n", encoding="utf-8")
    monkeypatch.setenv("DEPLOY_ENV", "dev")

    config = load_config(base)
    assert config.get("user_config.plugins.llm.provider") == "stub"
    assert config.get("user_config.plugins.llm.model") == "m2"
    assert config.get("app.name") == "x"


def test_missing_file_yields_empty(tmp_path):
    config = load_config(tmp_path / "nope.yaml")
    assert config.get("app.name") is None
    assert config.get("app.name", "default") == "default"


def test_section_accessor(tmp_path):
    base = tmp_path / "application.yaml"
    base.write_text("user_config:\n  plugins:\n    logger: stdlib\n", encoding="utf-8")
    config = load_config(base)
    assert config.section("user_config")["plugins"]["logger"] == "stdlib"
    assert config.section("missing") == {}


def test_scenario_overlay_from_dedicated_dir(tmp_path, monkeypatch):
    base = tmp_path / "application.yaml"
    base.write_text(
        "user_config:\n  database:\n    database_url: sqlite:///base.db\n",
        encoding="utf-8",
    )
    (tmp_path / "overlays").mkdir()
    (tmp_path / "overlays" / "e2e-sqlite.yaml").write_text(
        'user_config:\n  database:\n    database_url: "sqlite:///:memory:"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SOFAPY_CONFIG_OVERLAY", "e2e-sqlite")
    monkeypatch.delenv("DEPLOY_ENV", raising=False)

    config = load_config(base)
    assert config.get("user_config.database.database_url") == "sqlite:///:memory:"


def test_scenario_overlay_missing_is_noop(tmp_path, monkeypatch):
    base = tmp_path / "application.yaml"
    base.write_text("app:\n  name: x\n", encoding="utf-8")
    monkeypatch.setenv("SOFAPY_CONFIG_OVERLAY", "does-not-exist")
    monkeypatch.delenv("DEPLOY_ENV", raising=False)
    config = load_config(base)
    assert config.get("app.name") == "x"
