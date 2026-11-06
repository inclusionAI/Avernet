"""Consume the repository's real referee renderer and ballot checker."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml
from conftest import node_prompt
from pydantic_ai import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from pydanticai_player.brain import PydanticPlayer
from pydanticai_player.domain import speech_error
from pydanticai_player.tasks import parse_task


@pytest.fixture
def referee():
    script = (
        Path(__file__).parents[4] / "singlebox/agents/6bots_undercover_game_profile/"
        "referee/skills/undercover-game-referee/scripts/undercover.py"
    )
    spec = importlib.util.spec_from_file_location("referee_contract", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def state():
    seats = [
        {
            "seat": n,
            "kind": "human" if n == 1 else "bot",
            "display": f"玩家{n}",
            "bot_uuid": f"bot-{n}" if n != 1 else None,
            "binding": f"seat{n}",
            "word": "雨伞" if n != 3 else "阳伞",
            "role": "civilian" if n != 3 else "undercover",
            "alive": True,
        }
        for n in (1, 2, 3)
    ]
    return {
        "session_id": "g:a",
        "group_id": "g",
        "human_actor_id": "human-1",
        "referee_uuid": "referee",
        "config": {"max_rounds": 6},
        "phase": "SPEAK",
        "round": 1,
        "rounds": [
            {
                "round": 1,
                "order": [1, 2, 3],
                "speeches": {},
                "votes": {},
                "counts": {},
                "renders": {},
            }
        ],
        "words": {"civilian": "雨伞", "undercover": "阳伞"},
        "seats": seats,
    }


@pytest.mark.parametrize("pk", [False, True])
async def test_actual_referee_vote_instruction_and_final_ballot(referee, state, pk):
    if pk:
        state["rounds"][0]["pk"] = {"order": [1, 3], "speeches": {}, "votes": {}, "renders": {}}
    rendered, _ = referee.render_vote_yaml(state)
    definition = yaml.safe_load(rendered)
    nodes = definition["runtime"]["state_machine"]["nodes"]
    task = parse_task(
        node_prompt(
            "vote_2",
            nodes["vote_2"]["instruction"],
            {"alive": ["1号 玩家1", "2号 玩家2", "3号 玩家3"]},
        )
    )
    assert task.allowed_targets == (1, 3)
    assert task.allow_abstain is (not pk)

    def model(messages, info):
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"target": 3})])

    answer = await PydanticPlayer(FunctionModel(model)).answer(task)
    assert referee.parse_vote(state, 2, answer.text) == (3, None)


def test_actual_referee_speech_instruction_and_own_word_checks(referee, state):
    rendered, _ = referee.render_speak_yaml(state)
    nodes = yaml.safe_load(rendered)["runtime"]["state_machine"]["nodes"]
    task = parse_task(node_prompt("speak_2", nodes["speak_2"]["instruction"]))
    assert task.word == "雨伞" and task.seat == 2 and task.max_chars == 25
    for text in ["雨伞真好用。", "下雨时撑开伞。", "长" * 26, "天气变化时会带着。"]:
        _, referee_error = referee.check_text(state, 2, text, 25)
        assert (speech_error(text, task) is None) == (referee_error is None)
