from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "clawevolve_async_runner.sh"
TASK_LAUNCHER = ROOT / "scripts" / "clawevolve_task_launcher.sh"


def _runner_source() -> str:
    return RUNNER.read_text(encoding="utf-8")


def test_release_source_is_relative_to_runner_directory() -> None:
    source = _runner_source()

    assert 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' in source
    assert 'local release_root="$SCRIPT_DIR"' in source
    assert 'local release_file="${release_root}/RELEASE_VERSION"' in source
    assert 'archive="${release_root}/${archive_file}"' in source
    assert 'local release_root="/mnt/sys/linux/clawevolve"' not in source
    assert 'local release_file="/mnt/sys/linux/clawevolve/RELEASE_VERSION"' not in source


def test_release_skills_install_into_private_runtime_root() -> None:
    source = _runner_source()

    assert 'OPENCLAW_WORKSPACE="${OPENCLAW_WORKSPACE:-/home/admin/.openclaw/workspace}"' in source
    assert 'CLAWEVOLVE_SKILLS_ROOT="${SKILL_BASE_DIR:-${OPENCLAW_WORKSPACE}/clawevolve-skills}"' in source
    assert 'SKILL_BASE_DIR must be an absolute non-root path' in source
    assert 'export SKILL_BASE_DIR="$CLAWEVOLVE_SKILLS_ROOT"' in source
    assert 'installed_release_file="${runtime_root}/.clawevolve-release-version"' in source
    assert 'incoming="$runtime_root/.${name}.incoming.$$"' in source
    assert 'cp -a "$source_dir" "$incoming"' in source
    assert 'mv "$incoming" "$runtime_root/$name"' in source
    assert 'ln -sfn "skills-local/$name" "$installed_path"' not in source
    assert '/skills/pre/' not in source
    assert '/skills/prod/' not in source


def test_same_release_only_skips_copy_when_all_release_skills_are_healthy() -> None:
    source = _runner_source()

    unchanged = source.index('if [[ "$installed_release" == "$release_version" ]]')
    health = source.index('release_healthy=1', unchanged)
    entity_check = source.index('! -f "$runtime_root/$name/SKILL.md"', health)
    repair = source.index('skill release repair required:', health)
    healthy_return = source.index('skill release unchanged and healthy:', health)
    extract = source.index('SKILL_EXTRACT_DIR="$(mktemp -d /tmp/clawevolve-skills.')
    copy = source.index('cp -a "$source_dir" "$incoming"')

    assert unchanged < health < entity_check < repair < healthy_return < extract < copy


def test_debug_mode_migrates_managed_skills_instead_of_executing_legacy_root() -> None:
    source = _runner_source()

    assert "sync_debug_skills()" in source
    assert 'source_dir="$LEGACY_SKILLS_LOCAL_ROOT/$name"' in source
    assert 'if [[ ! -f "$source_dir/SKILL.md" ]]' in source
    assert 'incoming="$CLAWEVOLVE_SKILLS_ROOT/.${name}.incoming.$$"' in source
    assert 'mv "$incoming" "$CLAWEVOLVE_SKILLS_ROOT/$name"' in source
    assert 'candidate="${CLAWEVOLVE_SKILLS_ROOT}/${skill_name}/${relative_path}"' in source
    assert "debug mode: skip skill release comparison and synchronization" not in source


def test_legacy_cleanup_is_bounded_and_best_effort() -> None:
    source = _runner_source()

    assert "cleanup_legacy_release_skills()" in source
    assert 'other_evolve_runner_is_active' in source
    assert '[[ -L "$entry" || ! -d "$entry" || ! -f "$entry/.clawevolve-version" ]]' in source
    assert '[[ "$target" == "skills-local/${managed_name}" ]]' in source
    assert "-name '.*.backup.*'" in source
    assert 'legacy top-level backup cleanup warning:' in source
    assert 'legacy skill cleanup deferred: another ClawEvolve runner is active' in source
    assert 'legacy skill cleanup warning: unable to move' in source


def test_runtime_maintenance_is_once_per_task_and_default_enabled() -> None:
    source = _runner_source()
    launcher = TASK_LAUNCHER.read_text(encoding="utf-8")

    assert 'RUNTIME_MAINTENANCE="${CLAWEVOLVE_RUNTIME_MAINTENANCE:-true}"' in source
    assert 'TASK_LAUNCHER="${SCRIPT_DIR}/clawevolve_task_launcher.sh"' in source
    assert 'setsid nohup "${LAUNCH_COMMAND[@]}"' in source
    assert "sudo -n supervisorctl restart openclaw" not in source

    assert ".runtime_maintenance_v1.json" in launcher
    assert "cleanup_clawevolve_openclaw_runtime.py" in launcher
    assert "assert_no_other_active_evolve_children" in launcher
    assert "sudo -n supervisorctl restart openclaw" in launcher
    assert "wait_for_openclaw_gateway" in launcher
    assert "runtime maintenance cleanup warning:" in launcher
    assert "runtime maintenance skipped to protect active task:" in launcher
    assert 'write_runtime_maintenance_marker "$marker_path" "$cleanup_result" false' in launcher
    assert 'supervisorctl status openclaw' in launcher
    assert 'socket.create_connection(("127.0.0.1", int(sys.argv[1]))' in launcher
    maintenance = launcher.index("ensure_task_runtime_maintenance()")
    restart = launcher.index("  restart_openclaw_gateway_once", maintenance)
    cleaner = launcher.index('python3 "$cleaner" --openclaw-home', restart)
    completed = launcher.index("runtime maintenance completed: marker=", cleaner)
    assert restart < cleaner < completed
    assert 'GATEWAY_RESTARTED_FOR_TASK="true"' in launcher
    assert "OPENCLAW_RUNTIME_MAINTENANCE_FAILED" in launcher


def test_gateway_restart_is_deferred_until_after_dispatch_ack_boundary() -> None:
    source = _runner_source()

    launch = source.index('setsid nohup "${LAUNCH_COMMAND[@]}"')
    started = source.index('status":"started', launch)

    assert launch < started
    assert "ensure_task_runtime_maintenance" not in source


def test_launcher_is_validated_before_started_and_dead_launch_is_not_reused() -> None:
    source = _runner_source()

    stale_check = source.index('code":"RUNNER_LAUNCH_STALE')
    launcher = source.index('TASK_LAUNCHER="${SCRIPT_DIR}/clawevolve_task_launcher.sh"')
    syntax_check = source.index('bash -n "$TASK_LAUNCHER"', launcher)
    launch = source.index('setsid nohup "${LAUNCH_COMMAND[@]}"', syntax_check)
    launched_marker = source.index('touch "$LAUNCHED_FILE"', launch)
    started = source.index('status":"started', launched_marker)

    assert 'status":"already_started' not in source
    assert stale_check < launcher < syntax_check < launch < launched_marker < started
    assert 'code":"TASK_LAUNCHER_INVALID' in source[syntax_check:launch]


def test_runtime_cleanup_uses_release_handler_and_disables_maintenance() -> None:
    source = _runner_source()
    launcher = TASK_LAUNCHER.read_text(encoding="utf-8")
    package = (ROOT / "scripts" / "package_clawevolve_skills.sh").read_text(encoding="utf-8")

    assert 'if [[ "$STAGE" == "runtime-cleanup" ]]' in source
    assert 'log_line "runtime cleanup: skip skill synchronization"' in source
    assert 'if (( DEBUG_MODE )); then' in source
    assert 'STAGE_RUNNER="${SCRIPT_DIR}/clawevolve_runtime_cleanup.py"' in source
    assert 'RUNTIME_MAINTENANCE="false"' in source
    assert 'LAUNCH_COMMAND+=(--preflight-active-evolve-guard true)' in source
    assert 'LAUNCH_COMMAND+=(--refresh-gateway-before-handler true)' in source
    preflight = launcher.index('if [[ "$PREFLIGHT_ACTIVE_EVOLVE_GUARD" == "true" ]]')
    environment = launcher.index("ensure_openclaw_environment", preflight)
    refresh = launcher.index('if [[ "$REFRESH_GATEWAY_BEFORE_HANDLER" == "true"', environment)
    maintenance = launcher.index("ensure_task_runtime_maintenance", refresh)
    assert preflight < environment
    assert environment < refresh < maintenance
    assert 'SKIP_ENVIRONMENT_ADAPTATION="true"' in launcher[preflight:environment]
    assert "restart_openclaw_gateway_once" in launcher[refresh:maintenance]
    assert "environment adaptation skipped to protect active Evolve task:" in launcher
    assert 'clawevolve_runtime_cleanup.py" "${RELEASE_BUILD_DIR}/clawevolve_runtime_cleanup.py"' in package
