#!/usr/bin/env python3
"""谁是卧底 · 非 OpenClaw 玩家 Bot 启动入口。

用法：
  python -m undercover_player --url ws://127.0.0.1:21000/ws/bot
  python -m undercover_player --token <TOKEN-AFTER-FIRST-CONNECT>

环境变量：
  BCS_WS_URL   WebSocket 地址（默认 ws://127.0.0.1:21000/ws/bot）
  BCS_PORT     默认端口（默认 21000）
  ANTHROPIC_AUTH_TOKEN  若希望走真实模型可后续接入；当前引擎为纯规则。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="undercover-python-player")
    parser.add_argument("--url", default=None, help="BCS WebSocket 端点")
    parser.add_argument("--token", default=None, help="已持久化的 bot.connect token")
    parser.add_argument("--name", default="玩家逻辑控林工")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from .agent import run_player

    try:
        asyncio.run(run_player(ws_url=args.url, token=args.token, bot_name=args.name))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())