"""Assemble the model and adapters; never log credentials or private instructions."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

import httpx
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from .brain import PydanticPlayer
from .config import load_settings
from .domain import PlayerTask
from .transport import Connection


async def smoke(player: PydanticPlayer, path: Path) -> None:
    cases = [
        PlayerTask(
            "speech",
            "speak_2",
            "你是2号，你的词是【雨伞】。第1轮发言。"
            "输出一句话，不超过25字，不泄词，不下定义，描述至少适用于4种东西。",
            '{"history": []}',
            "(none)",
            2,
            "雨伞",
            25,
        ),
        PlayerTask(
            "vote",
            "vote_2",
            "你是2号，词是【雨伞】。可以投：1号、3号；"
            "不能投自己。仅据公开发言，选择相对最可疑的合法候选。",
            json.dumps(
                {
                    "history": [
                        {
                            "round": 1,
                            "speeches": {"1": "遇到天气变化时会拿出来。", "3": "睡前经常会用到。"},
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            "",
            2,
            "雨伞",
            10,
            (1, 3),
            True,
        ),
        PlayerTask(
            "vote",
            "vote_2",
            "你是2号，词是【雨伞】。PK投票，可以投：1号、3号；禁止弃权，不能投自己。必须选择一位。",
            '{"history": [{"speeches": {"1": "挡在上方", "3": "放在枕边"}}]}',
            "",
            2,
            "雨伞",
            10,
            (1, 3),
            False,
        ),
    ]
    records = []
    for case in cases:
        answer = await player.answer(case)
        records.append(
            {
                "kind": case.kind,
                "pk": not case.allow_abstain,
                "output": answer.text,
                "model_requests": answer.requests,
                "input_tokens": answer.input_tokens,
                "output_tokens": answer.output_tokens,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {"validation": "live-model-smoke-only", "mixed_games_played": 0, "cases": records}
    await asyncio.to_thread(
        path.write_text, json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Live model smoke passed ({len(records)} cases). Report: {path}")


async def run(args) -> None:
    settings = load_settings()
    async with httpx.AsyncClient(timeout=settings.timeout) as http_client:
        provider = OpenAIProvider(
            base_url=settings.base_url,
            api_key=settings.api_key.get_secret_value(),
            http_client=http_client,
        )
        model = OpenAIChatModel(
            settings.model,
            provider=provider,
            profile=OpenAIModelProfile(openai_supports_strict_tool_definition=False),
            # DeepSeek's default thinking mode rejects forced output-tool selection.
            # Other compatible providers can omit this extension via configuration.
            settings={"extra_body": {"thinking": {"type": "disabled"}}}
            if settings.disable_thinking
            else {},
        )
        player = PydanticPlayer(model, timeout=settings.timeout)
        if args.check_model:
            await smoke(player, args.report)
        else:
            await Connection(settings, player).run()


def main() -> None:
    parser = argparse.ArgumentParser(description="PydanticAI Undercover player for BCN V3")
    parser.add_argument("--check-model", action="store_true", help="Run 3 synthetic model checks")
    parser.add_argument("--report", type=Path, default=Path("artifacts/model-smoke.json"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    logging.getLogger("pydanticai_player").setLevel(logging.INFO)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except Exception as error:
        # Do not print provider exception bodies, config values or validation input values.
        parser.exit(
            1, f"Player failed ({type(error).__name__}); verify configuration and service status.\n"
        )
