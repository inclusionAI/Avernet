"""Run the Agents SDK player with an existing BCN Bot identity."""

import argparse
import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

from agents import set_tracing_disabled
from openai import AsyncOpenAI
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from bcn import BotConnection, HandshakeError, audit
from player import AgentsPlayer


@dataclass(frozen=True)
class Settings:
    bcn_url: str
    bot_id: str
    bcn_token: str = field(repr=False)
    model: str
    model_url: str | None
    model_key: str = field(repr=False)


def settings() -> Settings:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-file", required=True, type=Path, help="BCN .bcs/session.json for this player")
    parser.add_argument("--bcn-url", help="Override the WebSocket URL from the session file")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-6-luna"))
    args = parser.parse_args()
    try:
        session = json.loads(args.session_file.read_text(encoding="utf-8"))
        url = args.bcn_url or session["bcs_url"]
        bot_id = session["bot_uuid"]
        token = session["token"]
    except (OSError, ValueError, KeyError, TypeError):
        parser.error("session file must contain bcs_url, bot_uuid and token")
    if not all(isinstance(value, str) and value.strip() for value in (url, bot_id, token, args.model)):
        parser.error("missing session value or model")
    endpoint = urlsplit(url)
    if endpoint.scheme not in {"ws", "wss"} or not endpoint.hostname or endpoint.username or endpoint.password:
        parser.error("BCN requires a WebSocket URL without embedded credentials")
    model_key = os.environ.get("OPENAI_API_KEY")
    if not model_key:
        parser.error("OPENAI_API_KEY is required")
    return Settings(url, bot_id, token, args.model, os.environ.get("OPENAI_BASE_URL"), model_key)


async def run(config: Settings) -> None:
    set_tracing_disabled(True)
    async with AsyncOpenAI(api_key=config.model_key, base_url=config.model_url, max_retries=1) as client:
        player = AgentsPlayer(client, config.model)
        terminal_runs: OrderedDict[str, str] = OrderedDict()
        delay = 1
        while True:
            try:
                async with connect(config.bcn_url, open_timeout=15) as socket:
                    bot = BotConnection(socket, config.bot_id, player, terminal_runs)
                    await bot.serve(config.bcn_token)
            except HandshakeError as error:
                if str(error) != "already_connected":
                    raise
                audit("reconnect", reason="already_connected", delay_seconds=delay)
            except (OSError, ConnectionClosed, asyncio.TimeoutError) as error:
                if isinstance(error, ConnectionClosed):
                    delay = 1
                audit("reconnect", reason=type(error).__name__, delay_seconds=delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(run(settings()))
    except KeyboardInterrupt:
        pass
    except HandshakeError as error:
        raise SystemExit(str(error))
