"""Unit tests for CommandResult dataclass."""

from secbaas.api.device_manage import CommandResult


class TestCommandResult:
    """Test CommandResult dataclass."""

    def test_required_fields(self):
        r = CommandResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            execution_time_ms=100,
            command="echo hello",
        )
        assert r.exit_code == 0
        assert r.stdout == "ok"
        assert r.stderr == ""
        assert r.execution_time_ms == 100
        assert r.command == "echo hello"

    def test_env_default_none(self):
        r = CommandResult(
            exit_code=0,
            stdout="",
            stderr="",
            execution_time_ms=0,
            command="ls",
        )
        assert r.env is None

    def test_with_env(self):
        r = CommandResult(
            exit_code=0,
            stdout="",
            stderr="",
            execution_time_ms=0,
            command="ls",
            env={"PATH": "/usr/bin"},
        )
        assert r.env == {"PATH": "/usr/bin"}

    def test_non_zero_exit_code(self):
        r = CommandResult(
            exit_code=1,
            stdout="",
            stderr="error",
            execution_time_ms=50,
            command="false",
        )
        assert r.exit_code == 1
        assert r.stderr == "error"

    def test_repr(self):
        r = CommandResult(
            exit_code=0,
            stdout="output",
            stderr="",
            execution_time_ms=200,
            command="test",
        )
        rep = repr(r)
        assert "CommandResult" in rep
        assert "exit_code=0" in rep

    def test_equality(self):
        r1 = CommandResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            execution_time_ms=100,
            command="echo",
        )
        r2 = CommandResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            execution_time_ms=100,
            command="echo",
        )
        assert r1 == r2

    def test_inequality(self):
        r1 = CommandResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            execution_time_ms=100,
            command="echo",
        )
        r2 = CommandResult(
            exit_code=1,
            stdout="",
            stderr="err",
            execution_time_ms=100,
            command="echo",
        )
        assert r1 != r2
