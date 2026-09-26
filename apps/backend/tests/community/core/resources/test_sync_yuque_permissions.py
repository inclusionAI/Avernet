"""Coverage for ``sync_yuque_permissions`` (B11 Phase-A passport seam).

Reads a bot's yuque links from the resource repo, builds neutral
``SubResourceItem`` entries, and calls the injected passport's
``save_sub_resources``. Exercises the Doc + Book branches and the full-update
call.
"""
from __future__ import annotations

from agentclaw.community.core.resources.dependencies.service_dep import sync_yuque_permissions


class _StubRepo:
    def __init__(self, items):
        self._items = items
        self.calls = []

    def list_resources(self, **kwargs):
        self.calls.append(kwargs)
        return self._items


class _StubPassport:
    def __init__(self):
        self.received = None

    def save_sub_resources(self, bot_id, user_id, sub_resources):
        self.received = (bot_id, user_id, sub_resources)
        return True


def test_sync_yuque_permissions_builds_doc_and_book_sub_resources():
    items = [
        {
            "attributes": {
                "link_type": "yuque",
                "url": "https://yuque/doc/1",
                "doc_id": 111,
                "yuque_type": "Doc",
                "access_modes": ["READ"],
            }
        },
        {
            "attributes": {
                "link_type": "yuque",
                "url": "https://yuque/book/2",
                "book_id": 222,
                "yuque_type": "Book",
            }
        },
        # Non-yuque link is filtered out.
        {"attributes": {"link_type": "web", "url": "https://example.com"}},
    ]
    repo = _StubRepo(items)
    passport = _StubPassport()

    sync_yuque_permissions("bot-1", "user-1", repo, passport)

    assert passport.received is not None
    bot_id, user_id, subs = passport.received
    assert (bot_id, user_id) == ("bot-1", "user-1")
    assert len(subs) == 2
    by_type = {s.sub_resource_type: s for s in subs}
    assert by_type["YUQUE_DOC"].detail_config["doc_id"] == "111"
    assert by_type["YUQUE_BOOK"].detail_config["book_id"] == "222"


def test_sync_yuque_permissions_swallows_repo_errors():
    class _Boom:
        def list_resources(self, **kwargs):
            raise RuntimeError("db down")

    # Must not raise — permissions are eventual-consistency.
    sync_yuque_permissions("bot-1", "user-1", _Boom(), _StubPassport())
