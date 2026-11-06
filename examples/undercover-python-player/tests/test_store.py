from __future__ import annotations

import unittest

from undercover_player.store import HistoryStore, SessionMemory


class FakeClock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


class HistoryStoreTests(unittest.TestCase):
    def test_caps_sessions_and_keeps_active_sessions(self) -> None:
        store = HistoryStore(limit=5, max_sessions=2)
        store.append("s1", "one")
        store.append("s2", "two")
        store.append("s3", "three", active_session_keys={"s1"})
        self.assertNotEqual(store.messages("s1"), [])  # 活跃会话不能被容量清理删掉
        self.assertEqual(store.messages("s2"), [])
        self.assertNotEqual(store.messages("s3"), [])

    def test_ttl_expiry_and_active_refresh(self) -> None:
        clock = FakeClock()
        store = HistoryStore(limit=5, max_sessions=10, ttl_seconds=10, clock=clock)
        store.append("old", "hi")
        store.append("keep", "hi")
        clock.value += 11
        # 活跃会话被保护并刷新 last_used
        kept = store.messages("keep", active_session_keys={"keep"})
        self.assertEqual([item["content"] for item in kept], ["hi"])
        self.assertEqual(store.messages("old"), [])
        self.assertIsNone(store.session_id("old"))

    def test_single_session_limit_and_contract_fields(self) -> None:
        store = HistoryStore(limit=2)
        store.append("s", "a", session_id="sid")
        store.append("s", "b", session_id="sid")
        store.append("s", "c", session_id="sid")
        messages = store.messages("s")
        self.assertEqual([item["content"] for item in messages], ["b", "c"])
        self.assertEqual(set(messages[0]), {"role", "content", "timestamp"})
        self.assertEqual(store.session_id("s"), "sid")


class SessionMemoryTests(unittest.TestCase):
    def test_sessions_do_not_mix_words(self) -> None:
        memory = SessionMemory(max_sessions=10)
        memory.remember("a", 1, "苹果")
        memory.remember("b", 2, "香蕉")
        self.assertEqual(memory.get("a"), (1, "苹果"))
        self.assertEqual(memory.get("b"), (2, "香蕉"))

    def test_active_session_is_not_evicted(self) -> None:
        memory = SessionMemory(max_sessions=1)
        memory.remember("active", 1, "苹果")
        memory.remember("other", 2, "香蕉", active_session_keys={"active"})
        self.assertEqual(memory.get("active"), (1, "苹果"))
        self.assertIsNone(memory.get("other"))

    def test_idle_session_expires(self) -> None:
        clock = FakeClock()
        memory = SessionMemory(max_sessions=10, ttl_seconds=10, clock=clock)
        memory.remember("old", 1, "苹果")
        clock.value += 11
        memory.remember("new", 2, "香蕉")
        self.assertIsNone(memory.get("old"))
        self.assertEqual(memory.get("new"), (2, "香蕉"))


if __name__ == "__main__":
    unittest.main()
