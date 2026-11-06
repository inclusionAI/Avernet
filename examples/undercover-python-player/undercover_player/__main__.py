"""纯 Python BCN 玩家入口。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .bcn_client import BcnClient, HandshakeError
from .config import PlayerConfig
from .store import EventLog, TokenStore

LOGGER = logging.getLogger("undercover_player")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Avernet BCN V3 pure-Python undercover player")
    parser.add_argument("--bcs-url", default=None, help="BCS HTTP base URL, default http://127.0.0.1:21000")
    parser.add_argument("--bot-name", default=None, help="display name shown in the group")
    parser.add_argument("--token-file", type=Path, default=None, help="persisted bot session file")
    parser.add_argument("--log-dir", type=Path, default=None, help="JSONL event log directory")
    parser.add_argument("--rules-only", action="store_true", help="never call a model; use deterministic fallback")
    parser.add_argument("--debug", action="store_true", help="verbose stderr logging")
    return parser


def _config_from_args(args: argparse.Namespace) -> PlayerConfig:
    config = PlayerConfig.from_environment()
    overrides: dict[str, object] = {}
    if args.bcs_url:
        overrides["bcs_url"] = args.bcs_url
    if args.bot_name:
        overrides["bot_name"] = args.bot_name
    if args.token_file:
        overrides["token_file"] = args.token_file
    if args.log_dir:
        overrides["log_dir"] = args.log_dir
    if args.rules_only:
        overrides["rules_only"] = True
    return config.with_overrides(**overrides)


async def _run(config: PlayerConfig) -> int:
    client = BcnClient(config, event_log=EventLog(config.log_dir), token_store=TokenStore(config.token_file))
    LOGGER.info(
        "starting player name=%s bcs=%s model=%s log=%s",
        config.bot_name,
        config.bcs_url,
        "enabled" if config.llm_enabled() else "rules-only",
        client.event_log.path,
    )
    try:
        await client.run()
    except HandshakeError as error:
        LOGGER.error("cannot join BCN: %s (code=%s)", error, error.code)
        return 2
    finally:
        await client.stop()
    return 0


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        config = _config_from_args(args)
    except ValueError as error:
        LOGGER.error("invalid configuration: %s", error)
        return 2
    try:
        return asyncio.run(_run(config))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
