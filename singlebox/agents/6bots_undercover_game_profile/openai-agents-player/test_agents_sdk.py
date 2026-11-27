"""Run the Agents SDK against a fixture Responses endpoint."""

import json
import unittest

from agents import set_tracing_disabled
import httpx2
from openai import AsyncOpenAI

from player import AgentsPlayer, parse_task
from test_player import SESSION, VOTE


class AgentsSDKTest(unittest.IsolatedAsyncioTestCase):
    async def test_illegal_vote_gets_one_correction_through_responses(self):
        requests = []
        audits = []
        drafts = iter(("我投4号", "我投2号"))

        def respond(request):
            self.assertEqual((request.method, request.url.path), ("POST", "/v1/responses"))
            requests.append(json.loads(request.content))
            index = len(requests)
            return httpx2.Response(200, json={
                "id": f"resp_fixture_{index}",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "fixture",
                "output": [{
                    "type": "message",
                    "id": f"msg_fixture_{index}",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": next(drafts), "annotations": []}],
                }],
                "usage": {
                    "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            })

        def record(event, **fields):
            audits.append((event, fields))

        set_tracing_disabled(True)
        http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
        async with AsyncOpenAI(api_key="fixture", base_url="http://127.0.0.1:1/v1", http_client=http_client) as client:
            answer = await AgentsPlayer(client, "fixture")(parse_task(VOTE), SESSION, record)

        self.assertEqual(answer, "我投2号")
        self.assertEqual(audits, [("draft_rejected", {"reason": "vote_target", "attempt": 1})])
        self.assertEqual([(body["model"], body["store"]) for body in requests], [("fixture", False)] * 2)
        self.assertEqual(requests[0]["input"], [{"content": VOTE, "role": "user"}])
        self.assertIn("上一稿未通过规则检查", json.dumps(requests[1]["input"], ensure_ascii=False))
