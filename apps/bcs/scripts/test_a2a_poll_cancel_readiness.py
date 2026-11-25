"""Polling cancellation must wait for BCS to query the admitted remote Task."""
import json
from pathlib import Path
import sys
import unittest
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent / "e2e-test"))
from a2a_story import Peer


class PollCancelReadiness(unittest.TestCase):
    def test_task_creation_does_not_signal_poll_tracking_for_either_binding(self):
        for binding in ("HTTP+JSON", "JSONRPC"):
            with self.subTest(binding=binding):
                peer = Peer(binding)
                peer.mode = "poll_wait"
                peer.start()
                try:
                    def request(path, body=None):
                        data = None if body is None else json.dumps(body).encode()
                        req = Request(peer.base + path, data=data,
                                      headers={"A2A-Version": "1.0", "Content-Type": "application/json"})
                        with urlopen(req, timeout=5) as response:
                            return json.load(response)

                    body = {"message": {"parts": [{"text": "A2A question 1"}]},
                            "configuration": {"returnImmediately": True}}
                    if binding == "JSONRPC":
                        request("/a2a", {"jsonrpc": "2.0", "id": "send",
                                        "method": "SendMessage", "params": body})
                    else:
                        request("/a2a/message:send", body)
                    with peer.lock:
                        self.assertIn("peer-task-1", peer.tasks)
                        self.assertNotIn("peer-task-1", peer.observed_task_queries)
                    if binding == "JSONRPC":
                        request("/a2a", {"jsonrpc": "2.0", "id": "get",
                                        "method": "GetTask", "params": {"id": "peer-task-1"}})
                    else:
                        request("/a2a/tasks/peer-task-1")
                    with peer.lock:
                        self.assertIn("peer-task-1", peer.observed_task_queries)
                        self.assertEqual(peer.cancels, [])
                        self.assertEqual(len(peer.requests), 1)
                finally:
                    peer.close()


if __name__ == "__main__":
    unittest.main()
