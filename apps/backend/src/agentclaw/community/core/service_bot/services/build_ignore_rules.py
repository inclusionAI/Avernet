"""Literal artifact-relative exclusion rules shared by configuration and builds."""

from agentclaw.community.core.workspace.engine_sandbox import EngineBuildPlan


def is_excluded(path: str, paths: tuple[str, ...]) -> bool:
    return any(path == rule or path.startswith(rule + "/") for rule in paths)


def extra_root_rules(paths: tuple[str, ...], root: str) -> tuple[str, ...]:
    prefix = root.rstrip("/") + "/"
    return tuple(rule[len(prefix):] for rule in paths if rule.startswith(prefix))


def validate_required_paths(paths: tuple[str, ...], build_plan: EngineBuildPlan) -> None:
    required = [build_plan.mcp_config_relpath]
    if build_plan.engine_type == "openclaw":
        required.extend([
            "openclaw.json", "openclaw_verify.json", "openclaw_online.json",
            "openclaw_eval.json",
        ])
    for path in required:
        if path and is_excluded(path, paths):
            raise ValueError(f"required_build_path:{path}")
