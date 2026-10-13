"""G1a：官方 SDK 按 card 自动选绑定并消费完整流（1.2.2 API——G2 备忘）。

Card 驱动：经 A2ACardResolver 拉卡 → ClientFactory.create(card) 由卡选
transport。禁手工指定端点。事件经 normalize → 标准wire → g1a_validate 判定。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx

from g1a_validate import validate

BASE = "http://127.0.0.1:20043"
KINDS = ("task", "message", "statusUpdate", "artifactUpdate")


def _pb2dict(pb_msg) -> dict:
    """protobuf → dict（preserving snake→camelCase 由 MessageToDict 自带）。"""
    from google.protobuf.json_format import MessageToDict
    return MessageToDict(pb_msg, preserving_proto_field_name=False,
                         use_integers_for_enums=False)


def normalize(response) -> dict:
    """StreamResponse(protobuf) → 标准 wire 事件 dict（result 恰含一成员）。

    kind 判定用 WhichOneof("payload")；snake_case 字段名由 _pb2dict
    转 camelCase（status_update → statusUpdate；append/lastChunk 保持）。"""
    kind = response.WhichOneof("payload")
    if kind == "status_update":
        return {"result": {"statusUpdate": _pb2dict(response.status_update)}}
    if kind == "artifact_update":
        return {"result": {"artifactUpdate": _pb2dict(response.artifact_update)}}
    if kind == "task":
        return {"result": {"task": _pb2dict(response.task)}}
    if kind == "message":
        return {"result": {"message": _pb2dict(response.message)}}
    raise ValueError(f"empty StreamResponse payload: {response}")


async def main() -> int:
    from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
    from a2a.helpers.proto_helpers import new_text_message
    from a2a.types.a2a_pb2 import SendMessageRequest

    async with httpx.AsyncClient(timeout=30) as http:
        resolver = A2ACardResolver(httpx_client=http, base_url=BASE)
        card = await resolver.get_agent_card()

        # 由卡选 transport（自动 JSONRPC 绑定；禁手工端点）
        factory = ClientFactory(ClientConfig(streaming=True,
                                             httpx_client=http))
        client = factory.create(card)

        msg = new_text_message("g1a-probe-text", context_id="g1a-ctx")
        events = []
        async for response in client.send_message(
                SendMessageRequest(message=msg)):
            events.append(normalize(response))
        await client.close()

    ok, checks = validate(events)
    out = {"gate": "G1a", "verdict": "pass" if ok else "fail",
           "checks": checks, "event_count": len(events),
           "evidence_level": "protocol-contract",
           "note": "SDK 1.2.2; card-driven transport; "
                   "send_message -> AsyncIterator[StreamResponse]"}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open(Path(__file__).resolve().parents[1] / "evidence" / "G1a.json",
              "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
