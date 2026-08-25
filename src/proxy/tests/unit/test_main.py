"""Unit tests for main.py entrypoint argument handling."""

from __future__ import annotations


class TestMainArgparse:
    def test_missing_runner_reports(self, monkeypatch) -> None:
        from sandboxproxy.community import main as main_mod

        class FakeRunner:
            def run(self, config_path):
                raise RuntimeError("boom")

        monkeypatch.setattr(main_mod, "_load_runner", lambda mode: FakeRunner())

        import pytest

        with pytest.raises(RuntimeError, match="boom"):
            main_mod.main(["--mode", "bare", "--config", "/tmp"])

    def test_load_runner_error_message(self) -> None:
        import pytest

        from sandboxproxy.community.main import _load_runner

        with pytest.raises(RuntimeError, match="No runner registered"):
            _load_runner("nope")
