source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

run_check_basic() {
    local _origin
    _origin="$(pwd)"
    cd "$GATEWAY_DIR" || return 1

    log_stage
    echo "[CHECK] check-basic: ruff import sort, format, lint"

    log_sub "Import sort + format (ruff check --select I --fix && ruff format)..."
    _run uv run ruff check --select I --fix . || { cd "$_origin"; return 1; }
    _run uv run ruff format . || { cd "$_origin"; return 1; }

    log_sub "Lint check with auto fix (ruff check --fix)..."
    _run uv run ruff check . --fix || { cd "$_origin"; return 1; }

    cd "$_origin"
    log_info "check-basic passed"
}
