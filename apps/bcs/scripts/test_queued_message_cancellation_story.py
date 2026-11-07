"""Shared Provider captures must not complete another story's work."""
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent / "e2e-test"))
from queued_message_cancellation_story import QueuedMessageCancellationStory


class CaptureIsolationTests(unittest.TestCase):
    def test_only_own_session_and_bots_receive_callbacks(self):
        story = QueuedMessageCancellationStory(Mock())
        story.session_id = "own-session"
        story.bots = [{"provider_bot_ref": "driver"}, {"provider_bot_ref": "observer"}]

        def request(ref, session="own-session", method="chat.send"):
            return {"body": {"method": method, "session_id": session,
                             "to_bot": {"provider_bot_ref": ref}}}

        own = [request("driver"), request("observer")]
        story.captured = Mock(return_value=[request("other-bot"), request("driver", "other-session"),
                                            request("driver", method="chat.inject"), *own])
        story.callback = Mock()
        story.complete_dispatched_work()
        self.assertEqual(story.callback.call_count, 2)
        for captured, bot in zip(own, story.bots):
            story.callback.assert_any_call(captured, bot, "NO_REPLY")


if __name__ == "__main__":
    unittest.main()
