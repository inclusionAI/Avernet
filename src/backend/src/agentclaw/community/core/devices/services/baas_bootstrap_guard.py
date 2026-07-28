"""Build the guarded DaaS bootstrap command shared by BaaS startup paths."""

from __future__ import annotations


def build_baas_bootstrap_guard_command(*, as_admin: bool) -> str:
    """Wait for image init, reuse a complete checkout, or bootstrap once.

    The image ``root_init.sh`` and Backend container initialization can overlap.
    Waiting for root init before inspecting the checkout prevents a compensating
    bootstrap from clearing files while ``install_engine.sh`` consumes them.
    """
    git_tag_command = (
        "git -C /home/admin/agentclaw-daas-scripts describe --tags --exact-match"
    )
    bootstrap = "bash /home/admin/bin/bootstrap_minimal.sh"
    if as_admin:
        git_tag_command = f"su admin -c '{git_tag_command}'"
        bootstrap = f"su admin -c '{bootstrap}'"

    return (
        "("
        "_agentclaw_cleanup_lock() { "
        'rm -f "$_agentclaw_lock_dir/pid"; '
        'rmdir "$_agentclaw_lock_dir"; '
        "}; "
        "_agentclaw_release_tag_valid() { "
        '_agentclaw_release_tag="$1"; '
        'echo "$_agentclaw_release_tag" | '
        "grep -Eq '^v(0\\.([3-9]|[1-9][0-9]+)\\.[0-9]+|"
        "[1-9][0-9]*\\.[0-9]+\\.[0-9]+)(_dev|_pre)?$' || return 1; "
        '_agentclaw_runtime_env="${AGENTCLAW_ENV:-${env:-}}"; '
        'case "$_agentclaw_runtime_env" in '
        'dev) case "$_agentclaw_release_tag" in *_dev) return 0;; *) return 1;; esac;; '
        'pre) case "$_agentclaw_release_tag" in *_pre) return 0;; *) return 1;; esac;; '
        'prod) case "$_agentclaw_release_tag" in *_dev|*_pre) return 1;; *) return 0;; esac;; '
        "*) return 1;; "
        "esac; "
        "}; "
        "_agentclaw_checkout_valid() { "
        '[ -d "$_agentclaw_bootstrap_dir/.git" ] || return 1; '
        f'_agentclaw_release_tag="$({git_tag_command} 2>/dev/null)" || return 1; '
        '_agentclaw_release_tag_valid "$_agentclaw_release_tag" || return 1; '
        '[ -s "$_agentclaw_bootstrap_dir/installations/openClawEnterprise.properties" ] || return 1; '
        '[ -s "$_agentclaw_bootstrap_dir/bootstrapping/install_engine.sh" ] || return 1; '
        '[ -s "$_agentclaw_bootstrap_dir/bootstrapping/start_service.sh" ] || return 1; '
        "cmp -s "
        '"$_agentclaw_bootstrap_dir/bootstrapping/install_engine.sh" '
        "/home/admin/bin/install_engine.sh || return 1; "
        "cmp -s "
        '"$_agentclaw_bootstrap_dir/bootstrapping/start_service.sh" '
        "/home/admin/bin/start_service.sh || return 1; "
        "return 0; "
        "}; "
        "_agentclaw_init_ready=0; "
        "for _agentclaw_wait in $(seq 1 120); do "
        "if [ -f /var/run/agentclaw/.install_dependency_file ] || "
        '[ "$(cat /proc/1/comm 2>/dev/null)" = "supervisord" ]; then '
        "_agentclaw_init_ready=1; break; "
        "fi; "
        "sleep 1; "
        "done; "
        'if [ "$_agentclaw_init_ready" != "1" ]; then '
        "echo '[bootstrap] container initialization timed out' >&2; "
        "exit 1; "
        "fi; "
        "_agentclaw_bootstrap_dir=/home/admin/agentclaw-daas-scripts; "
        "mkdir -p /var/run/agentclaw; "
        "_agentclaw_lock_dir=/var/run/agentclaw/.baas-bootstrap.lock; "
        "_agentclaw_lock_acquired=0; "
        "for _agentclaw_lock_wait in $(seq 1 120); do "
        'if mkdir "$_agentclaw_lock_dir" 2>/dev/null; then '
        'echo "$$" > "$_agentclaw_lock_dir/pid"; '
        "_agentclaw_lock_acquired=1; break; "
        "fi; "
        'if [ -f "$_agentclaw_lock_dir/pid" ]; then '
        '_agentclaw_lock_pid="$(cat "$_agentclaw_lock_dir/pid" 2>/dev/null)"; '
        'case "$_agentclaw_lock_pid" in '
        "''|*[!0-9]*) ;; "
        '*) if ! kill -0 "$_agentclaw_lock_pid" 2>/dev/null; then '
        'rm -f "$_agentclaw_lock_dir/pid"; '
        'rmdir "$_agentclaw_lock_dir" 2>/dev/null || true; '
        "fi;; "
        "esac; "
        "fi; "
        "sleep 1; "
        "done; "
        'if [ "$_agentclaw_lock_acquired" != "1" ]; then '
        "echo '[bootstrap] bootstrap lock timed out' >&2; "
        "exit 1; "
        "fi; "
        "trap '_agentclaw_cleanup_lock' EXIT; "
        "trap 'exit 1' INT TERM; "
        "_agentclaw_result=0; "
        "if _agentclaw_checkout_valid; then "
        "echo '[bootstrap] reusing completed bootstrap checkout'; "
        f"else {bootstrap} && _agentclaw_checkout_valid || _agentclaw_result=$?; "
        "fi; "
        "_agentclaw_cleanup_lock || _agentclaw_result=1; "
        "trap - EXIT INT TERM; "
        'exit "$_agentclaw_result"'
        ")"
    )
