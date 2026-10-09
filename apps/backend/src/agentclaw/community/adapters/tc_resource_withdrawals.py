"""Operator-only CLI using the application's configured database and DI.

Run inside the Backend environment with the same profile/config as the service.
This imports the composition root but does not start HTTP or worker lifecycles.
"""

import argparse
from dataclasses import asdict
import json

from agentclaw.community.core.repository.protocols.platform import (
    ResourceWithdrawalRepositoryProtocol,
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Inspect or explicitly replay durable TC withdrawal events"
    )
    commands = root.add_subparsers(dest="command", required=True)
    for command in ("stats", "inspect", "replay"):
        sub = commands.add_parser(command)
        sub.add_argument("--tenant", required=True)
        if command != "stats":
            sub.add_argument("--event-id", required=True)
        if command == "replay":
            sub.add_argument("--expected-attempts", type=int, required=True)
            sub.add_argument("--actor", required=True)
            sub.add_argument("--reason", required=True)
            sub.add_argument("--confirm", action="store_true", required=True)
    return root


def execute(
    args: argparse.Namespace, repository: ResourceWithdrawalRepositoryProtocol
) -> dict:
    if args.command == "stats":
        return repository.stats(tenant=args.tenant)
    if args.command == "inspect":
        record = repository.get(args.event_id)
        if record is None or record.tenant != args.tenant:
            raise ValueError("withdrawal_not_found")
        return asdict(record)
    changed = repository.replay(
        event_id=args.event_id,
        tenant=args.tenant,
        expected_attempts=args.expected_attempts,
        actor=args.actor,
        reason=args.reason,
    )
    return {"event_id": args.event_id, "replayed": changed}


def main() -> int:
    args = parser().parse_args()
    # Same bootstrap/config path as the HTTP service, without entering lifespan.
    from agentclaw.community.adapters.http.app import app

    repository = app.state.injector.get(ResourceWithdrawalRepositoryProtocol)
    result = execute(args, repository)
    print(json.dumps(result, default=str, ensure_ascii=False))
    return 1 if result.get("replayed") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
