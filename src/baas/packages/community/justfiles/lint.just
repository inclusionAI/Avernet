check:
    just check-format && just check-static && just check-type && just check-lint

check-basic:
    source scripts/lib/checks.sh && run_check_basic

check-static:
    uv run mypy src tests

check-protocols:
    uv run mypy tests/architecture/check_protocols

check-type:
    uv run basedpyright src

check-format:
    uv run ruff check --select I --fix . && uv run ruff format .

check-lint:
    uv run ruff check . --fix