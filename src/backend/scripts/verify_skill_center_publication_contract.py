#!/usr/bin/env python3
"""Audit the real Team-scoped Skill Center Publication contract.

Run this from an OCB/corp deployment checkout whose composition root binds the
real ``SkillCenterGateway``. The script never reads credentials itself and
prints no package URL or service secret. ``--submit`` is an explicit mutating
opt-in and invokes the SC publish POST exactly once.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import time

from agentclaw.community.api.skill_center_publication_gateway import (
    SkillCenterPublicationGatewayProtocol,
)
from agentclaw.community.api.skill_center_gateway_service import (
    SkillCenterGatewayServiceProtocol,
)
from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterGatewayError,
    SkillCenterPublishState,
    SkillCenterPublishStatusRequest,
    SkillCenterPublishSubmitRequest,
    SkillCenterReadScope,
    SkillCenterTeamSkillDetailRequest,
    SkillCenterVersionListRequest,
    SkillCenterVisibility,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify the Team-scoped SC Publication contract and write JSON evidence."
    )
    parser.add_argument("--team-id", required=True)
    parser.add_argument("--skill-code", required=True)
    parser.add_argument("--version-number", required=True)
    parser.add_argument("--skill-name")
    parser.add_argument("--package-url")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.submit and (not args.skill_name or not args.package_url):
        parser.error("--submit requires --skill-name and --package-url")
    if args.poll_seconds <= 0 or args.timeout_seconds <= 0:
        parser.error("poll and timeout must be positive")
    return args


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _safe_failure_message(message: str, *, package_url: str | None) -> str:
    if package_url:
        return message.replace(package_url, "<redacted-package-url>")
    return message


def _status_evidence(status) -> dict[str, object]:
    return {
        "skill_code": status.skill_code,
        "version_number": status.version_number,
        "status": status.status.value,
        "is_completed": status.is_completed,
        "is_success": status.is_success,
        "upstream_status": status.upstream_status,
        "standard_check_result": (
            status.standard_check_result.to_raw_dict()
            if status.standard_check_result is not None
            else None
        ),
        "security_check_report": (
            status.security_check_report.to_raw_dict()
            if status.security_check_report is not None
            else None
        ),
    }


def _run(args: argparse.Namespace) -> dict[str, object]:
    profile = DeployProfile.detect()
    injector = build_injector(profile=profile)
    gateway = injector.get(SkillCenterPublicationGatewayProtocol)
    exact_reader = injector.get(SkillCenterGatewayServiceProtocol)
    evidence: dict[str, object] = {
        "started_at": _now(),
        "deploy_profile": profile.value,
        "team_id": args.team_id,
        "skill_code": args.skill_code,
        "version_number": args.version_number,
        "submit_enabled": bool(args.submit),
        "checks": [],
    }
    checks: list[dict[str, object]] = evidence["checks"]  # type: ignore[assignment]

    if args.submit:
        submitted = gateway.submit_publish(
            SkillCenterPublishSubmitRequest(
                team_id=args.team_id,
                skill_code=args.skill_code,
                skill_name=args.skill_name,
                version_number=args.version_number,
                package_url=args.package_url,
                visibility=SkillCenterVisibility.PRIVATE,
            )
        )
        checks.append(
            {
                "operation": "submit_publish",
                "passed": True,
                "called_once": True,
                "result": {
                    "skill_code": submitted.skill_code,
                    "version_number": submitted.version_number,
                    "status": submitted.status.value,
                    "external_request_id": submitted.external_request_id,
                },
            }
        )

    deadline = time.monotonic() + args.timeout_seconds
    while True:
        status = gateway.get_publish_status(
            SkillCenterPublishStatusRequest(args.skill_code)
        )
        if status.version_number != args.version_number:
            raise RuntimeError("SC status returned another version")
        if status.status is not SkillCenterPublishState.PENDING:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("SC publish status did not settle before timeout")
        time.sleep(min(args.poll_seconds, max(0.01, deadline - time.monotonic())))
    checks.append(
        {
            "operation": "get_publish_status",
            "passed": status.status is SkillCenterPublishState.PUBLISHED,
            "result": _status_evidence(status),
        }
    )
    if status.status is not SkillCenterPublishState.PUBLISHED:
        raise RuntimeError("SC publication reached a non-success terminal state")

    team_skill = gateway.get_team_skill(
        SkillCenterTeamSkillDetailRequest(args.team_id, args.skill_code)
    )
    if team_skill is None:
        raise RuntimeError("published Team Skill is not queryable")
    checks.append(
        {
            "operation": "get_team_skill",
            "passed": True,
            "result": {
                "team_id": team_skill.team_id,
                "skill_id": team_skill.skill_id,
                "skill_code": team_skill.skill_code,
                "skill_name": team_skill.skill_name,
            },
        }
    )

    versions = gateway.list_versions(
        SkillCenterVersionListRequest(
            args.skill_code, SkillCenterReadScope.TEAM, args.team_id
        )
    )
    exact = next(
        (item for item in versions if item.version_number == args.version_number),
        None,
    )
    if exact is None:
        raise RuntimeError("published exact Version is absent from version list")
    checks.append(
        {
            "operation": "list_versions",
            "passed": True,
            "result": {
                "count": len(versions),
                "exact": asdict(exact),
            },
        }
    )

    # The exact-download DTO validates the mandatory SHA-256 shape. The signed
    # URL itself is deliberately not written into the audit artifact.
    from agentclaw.community.plugin_api.skill_center_gateway import (
        SkillCenterExactDownloadRequest,
    )

    # Exact download remains on the existing validated exact-version consumer
    # seam used by Group 2's Materializer, not on the narrow publish protocol.
    download = exact_reader.get_exact_download(
        SkillCenterExactDownloadRequest(
            args.skill_code,
            args.version_number,
            SkillCenterReadScope.TEAM,
            args.team_id,
        )
    )
    checks.append(
        {
            "operation": "get_exact_download",
            "passed": True,
            "result": {
                "skill_code": download.skill_code,
                "version_number": download.version_number,
                "sha256": download.sha256,
                "download_url_present": bool(download.download_url),
                "mcp_service_count": len(download.mcp_services),
            },
        }
    )
    evidence["passed"] = True
    evidence["finished_at"] = _now()
    return evidence


def main() -> int:
    args = _args()
    try:
        evidence = _run(args)
    except SkillCenterGatewayError as exc:
        evidence = {
            "started_at": _now(),
            "finished_at": _now(),
            "passed": False,
            "submit_enabled": bool(args.submit),
            "team_id": args.team_id,
            "skill_code": args.skill_code,
            "version_number": args.version_number,
            "failure": {
                "type": "SkillCenterGatewayError",
                "code": exc.code.value,
                "upstream_code": exc.upstream_code,
                "trace_id": exc.trace_id,
                "message": _safe_failure_message(
                    str(exc), package_url=args.package_url
                ),
            },
        }
    except Exception as exc:
        evidence = {
            "started_at": _now(),
            "finished_at": _now(),
            "passed": False,
            "submit_enabled": bool(args.submit),
            "team_id": args.team_id,
            "skill_code": args.skill_code,
            "version_number": args.version_number,
            "failure": {
                "type": type(exc).__name__,
                "message": _safe_failure_message(
                    str(exc), package_url=args.package_url
                ),
            },
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True)
    args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if evidence.get("passed") is True else 1


if __name__ == "__main__":
    sys.exit(main())
