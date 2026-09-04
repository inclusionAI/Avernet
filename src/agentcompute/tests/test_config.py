import os

from agentcompute.community import load_dotenv, read_dotenv


def test_read_dotenv_parses_key_values(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        'OPENAI_API_KEY="sk-test"\n'
        "OPENAI_MODEL='gpt-4o'\n"
        "export OPENAI_BASE_URL=https://x.com/v1\n"
        "PLAIN=hello\n"
        "WITH_COMMENT=abc # trailing\n",
        encoding="utf-8",
    )
    values = read_dotenv(env)
    assert values["OPENAI_API_KEY"] == "sk-test"
    assert values["OPENAI_MODEL"] == "gpt-4o"
    assert values["OPENAI_BASE_URL"] == "https://x.com/v1"
    assert values["PLAIN"] == "hello"
    assert values["WITH_COMMENT"] == "abc"
    assert "#" not in values


def test_read_dotenv_missing_file_returns_empty(tmp_path):
    assert read_dotenv(tmp_path / "nope.env") == {}


def test_load_dotenv_does_not_override_existing(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("KEY=fromfile\n", encoding="utf-8")
    monkeypatch.setenv("KEY", "fromenv")
    load_dotenv(env)
    assert os.environ["KEY"] == "fromenv"


def test_load_dotenv_overrides_when_requested(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("KEY=fromfile\n", encoding="utf-8")
    monkeypatch.setenv("KEY", "fromenv")
    load_dotenv(env, override=True)
    assert os.environ["KEY"] == "fromfile"
