#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT))

from clawevolve_plan.input.contract import PlanSourceError  # noqa: E402
from clawevolve_plan.input.resolver import fetch_step_input, resolve_plan_source  # noqa: E402


def _local_test_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise PlanSourceError(
            "PLAN_SOURCE_INPUT_UNAVAILABLE",
            "--clawweb-base-url 仅允许本地回环地址用于测试",
            stage="interface",
        )
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve canonical plan-source/v2 for one Evolve Plan Step.")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--step-id", required=True)
    parser.add_argument("--evolve-results-dir", default="")
    parser.add_argument("--clawweb-base-url", default="")
    args = parser.parse_args(argv)
    try:
        fetcher = None
        if args.clawweb_base_url:
            local_base_url = _local_test_base_url(args.clawweb_base_url)
            fetcher = lambda task_id, step_id: fetch_step_input(  # noqa: E731
                task_id,
                step_id,
                base_url=local_base_url,
            )
        result = resolve_plan_source(
            task_id=args.task_id,
            step_id=args.step_id,
            evolve_results_dir=args.evolve_results_dir,
            step_input_fetcher=fetcher,
        )
        print(json.dumps({
            "status": result.status,
            "digest": result.digest,
            "source_path": str(result.source_path),
            "descriptor_path": str(result.descriptor_path),
        }, ensure_ascii=False, indent=2))
        return 0
    except PlanSourceError as error:
        print(json.dumps({"status": "error", "error": error.report_error()}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
