from __future__ import annotations

import json

import pytest
from pydantic_ai import models

from pydanticai_player.domain import PlayerTask


@pytest.fixture(autouse=True)
def forbid_live_models(monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)


@pytest.fixture
def vote_task():
    return PlayerTask(
        "vote",
        "vote_2",
        "你是2号，词是【雨伞】。可以投：1号、3号；",
        '{"history": []}',
        "",
        2,
        "雨伞",
        10,
        (1, 3),
        False,
    )


@pytest.fixture
def speech_task():
    return PlayerTask(
        "speech",
        "speak_2",
        "你是2号，你的词是【雨伞】。输出一句话。",
        '{"history": []}',
        "(none)",
        2,
        "雨伞",
        25,
    )


def node_prompt(node="speak_2", instruction=None, input_data=None, upstream="(none)"):
    if instruction is None:
        instruction = "你是2号，你的词是【雨伞】。第1轮发言。输出一句话，不超过25字。"
    return (
        f"[State Machine Task]\nnode_id: {node}\ndisplay_name: 玩家任务\n\n"
        f"[Input]\n{json.dumps(input_data or {}, ensure_ascii=False)}\n\n"
        f"[Upstream Outputs]\n{upstream}\n\n[Instruction]\n{instruction}"
    )


def send_frame(request_id="send", session="g:a", text=None, **params):
    return {
        "type": "req",
        "id": request_id,
        "method": "chat.send",
        "params": {
            "session_key": session,
            "bcs_session_id": session,
            "message": {"content": [{"type": "text", "text": text or node_prompt()}]},
            **params,
        },
    }
