"""Command-line and programmatic entrypoint for the demo.

The ``run`` function is the API: it takes a goal and a caller-supplied list of
candidate agents (with enough context to discharge each role), planning the
DAG, executing it, and writing the result plus a visualization to disk.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .community import load_dotenv
from .community.bootstrap import Config, get_container, set_config
from .community.core import DAGPlan, NodeStatus, export_plan
from .community.plugins import register_plugins
from .community.spi import AgentSpec

__all__ = ["run"]


def run(
    goal: str,
    agents: list[AgentSpec],
    *,
    plan_path: str | Path | None = None,
    viz_path: str | Path | None = None,
    provider: str = "stub",
    llm_options: dict[str, Any] | None = None,
    driver: str = "static",
    replanner: str | None = None,
    driver_options: dict[str, Any] | None = None,
    replanner_options: dict[str, Any] | None = None,
    max_workers: int = 8,
    env_file: str | Path | None = ".env",
    verbose: bool = True,
) -> dict[str, Any]:
    if verbose:
        _install_stdout_logging()
    if env_file is not None:
        load_dotenv(env_file)
    register_plugins()
    options: dict[str, Any] = {"llm": llm_options or {}}
    if driver_options:
        options["driver"] = driver_options
    if replanner_options:
        options["replanner"] = replanner_options
    set_config(
        Config(
            llm_provider=provider,
            driver=driver,
            replanner=replanner,
            options=options,
        )
    )
    from .community.plugins import register_agents

    register_agents(agents)

    plugins = get_container().plugins()
    planner = plugins.planner()
    driver = plugins.driver()

    print("== planning ==")
    plan = planner.plan(goal, agents)
    print(f"== plan: {len(plan.nodes)} node(s), {len(plan.edges)} edge(s) ==")
    _print_plan(plan)
    print("== executing ==")
    result = driver.run(plan, on_progress=_print_progress, max_workers=max_workers)
    print("== done ==")

    if viz_path is not None:
        export_plan(plan, viz_path)

    summary = _summarize(goal, plan, result)
    if plan_path is not None:
        Path(plan_path).parent.mkdir(parents=True, exist_ok=True)
        Path(plan_path).write_text(
            json.dumps(summary["plan_executed"], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    return summary


def _install_stdout_logging() -> None:
    import logging

    root = logging.getLogger()
    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "_stdout", False) for h in root.handlers
    ):
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
        handler._stdout = True  # type: ignore[attr-defined]
        root.addHandler(handler)
        root.setLevel(logging.INFO)


def _summarize(goal: str, plan: DAGPlan, result: Any) -> dict[str, Any]:
    return {
        "goal": goal,
        "succeeded": result.succeeded,
        "node_statuses": {nid: s.value for nid, s in result.log.statuses.items()},
        "final_output": result.final_output,
        "plan": plan.to_dict(),
        "plan_executed": plan.to_full_dict(),
        "nodes": {nid: n.to_full_dict() for nid, n in plan.nodes.items()},
    }


def _print_plan(plan: DAGPlan) -> None:
    for node in plan.nodes.values():
        goal = node.input.get("goal", "")
        print(f"  node {node.id} [{node.agent}] goal={goal!r}")
    for src, dst in plan.edges:
        print(f"  edge {src} -> {dst}")


def _print_progress(node_id: str, status: NodeStatus, progress: float) -> None:
    print(f"  [{status.value:9s}] {node_id} {progress:.0%}", flush=True)


def _parse_agents(raw: str) -> list[AgentSpec]:
    if raw.startswith("@") or raw.endswith(".json"):
        data = json.loads(Path(raw.lstrip("@")).read_text(encoding="utf-8"))
        return [AgentSpec(**item) for item in data]
    specs = []
    for item in raw.split(","):
        name, role = (item.split(":", 1) + [""])[:2]
        specs.append(AgentSpec(name=name.strip(), role=role.strip() or name.strip()))
    return specs


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(prog="acd", description="Agent compute demo")
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="run a goal end-to-end (default)")
    run_parser.add_argument("goal", help="the goal to split into a DAG and execute")
    run_parser.add_argument(
        "-a",
        "--agents",
        required=True,
        help="candidate agents: 'searcher:Searches web,summarizer:Summarizes' or a JSON file",
    )
    run_parser.add_argument("--plan", default=None, help="write the DAG plan JSON to this path")
    run_parser.add_argument("--viz", default="output/dag.html", help="write DAG visualization HTML")
    run_parser.add_argument(
        "--report",
        default="output/result.md",
        help="write the task report as markdown to this path",
    )
    run_parser.add_argument(
        "--report-html",
        default="output/report.html",
        help="write the full task report as HTML to this path",
    )
    run_parser.add_argument(
        "--provider",
        default="openai",
        choices=["openai", "stub"],
        help="LLM provider plugin option (default: openai)",
    )
    run_parser.add_argument(
        "--max-workers",
        type=int,
        default=8,
        help="max concurrent node executors (default: 8, use 1 for sequential)",
    )
    run_parser.add_argument(
        "--driver",
        default="static",
        choices=["static", "dynamic"],
        help="driver plugin: static (one-shot plan) or dynamic (replans between waves; default: static)",
    )
    run_parser.add_argument(
        "--replanner",
        default=None,
        choices=["dynamic"],
        help="replanner plugin (required when --driver=dynamic; default: dynamic if driver=dynamic)",
    )
    run_parser.add_argument(
        "--driver-options",
        default="{}",
        help="JSON object of driver options (e.g. replan_strategy, replan_max_calls, replan_min_calls)",
    )
    run_parser.add_argument(
        "--replanner-options",
        default="{}",
        help="JSON object of replanner options (e.g. max_extensions, max_total_nodes, replan_timeout_seconds)",
    )
    run_parser.add_argument(
        "--env-file",
        default=".env",
        help="path to a .env file for LLM config (default: .env; use '-' to skip)",
    )

    serve_parser = sub.add_parser("serve", help="start the HTTP API service")
    serve_parser.add_argument(
        "--config",
        "-c",
        default="config/application.yaml",
        help="path to application.yaml (default: config/application.yaml)",
    )
    serve_parser.add_argument("--host", default=None, help="bind host (default: 0.0.0.0)")
    serve_parser.add_argument("--port", type=int, default=None, help="bind port")

    if argv and argv[0] not in ("run", "serve", "--help", "-h"):
        argv = ["run", *argv]
    args = parser.parse_args(argv)
    if args.command == "serve":
        return _serve(args)
    return _run_cli(args)


def _run_cli(args: argparse.Namespace) -> int:
    agents = _parse_agents(args.agents)
    replanner = args.replanner
    if replanner is None and args.driver == "dynamic":
        replanner = "dynamic"
    try:
        driver_options = json.loads(args.driver_options) if args.driver_options else {}
        replanner_options = json.loads(args.replanner_options) if args.replanner_options else {}
    except json.JSONDecodeError as exc:
        print(f"error: --driver-options/--replanner-options must be valid JSON: {exc}", file=sys.stderr)
        return 2
    summary = run(
        args.goal,
        agents,
        plan_path=args.plan,
        viz_path=args.viz or None,
        provider=args.provider,
        max_workers=args.max_workers,
        driver=args.driver,
        replanner=replanner,
        driver_options=driver_options,
        replanner_options=replanner_options,
        env_file=None if args.env_file == "-" else args.env_file,
    )
    if args.report and summary.get("plan_executed"):
        from .community.core import DAGPlan, render_report_md

        plan = DAGPlan.from_full_dict(summary["plan_executed"])
        md_path = Path(args.report)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(
            render_report_md(
                plan, summary["goal"], summary.get("final_output"), summary.get("succeeded", False)
            ),
            encoding="utf-8",
        )
        print(f"report -> {md_path}")
    if args.report_html and summary.get("plan_executed"):
        from .community.core import DAGPlan, render_report

        plan = DAGPlan.from_full_dict(summary["plan_executed"])
        html_path = Path(args.report_html)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(
            render_report(
                plan, summary["goal"], summary.get("final_output"), summary.get("succeeded", False)
            ),
            encoding="utf-8",
        )
        print(f"report-html -> {html_path}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["succeeded"] else 1


def _serve(args: argparse.Namespace) -> int:
    import os
    from importlib.metadata import entry_points

    if args.host:
        os.environ["AGENT_COMPUTE_HOST"] = args.host
    if args.port:
        os.environ["AGENT_COMPUTE_PORT"] = str(args.port)

    runners = entry_points(group="agentcompute.runner")
    matching = [ep for ep in runners if ep.name == "bare"]
    if not matching:
        raise RuntimeError("no runner registered for mode 'bare'")
    runner_cls = matching[0].load()
    runner_cls().run(args.config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
