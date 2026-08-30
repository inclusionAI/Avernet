"""The backfill runs the flush deliberately and reports what it moved."""

from __future__ import annotations

import pytest

from agentclaw.community.api.installation_backfill_service import (
    InstallationBackfillServiceProtocol,
)
from agentclaw.community.core.repository.capability_desired_state_types import (
    InstallationFlushPlan,
)
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
from agentclaw.community.core.skill_center.services.installation_backfill_service import (
    InstallationBackfillService,
)

_BOT = {
    "bot_id": "bot-1",
    "owner_id": "owner",
    "entity_id": "entity-1",
    "active_engine": "openclaw",
    "env": "pre",
}

_EXPECTED_FLUSH = {
    "bot_id": "bot-1",
    "owner_id": "owner",
    "env": "pre",
    "engine_type": "openclaw",
    "default_engine_types": ("openclaw",),
}


def _plan(*, changed: bool) -> InstallationFlushPlan:
    return InstallationFlushPlan(
        member_skill_ids=frozenset({1}),
        skills_to_install=frozenset({1}),
        skills_to_uninstall=frozenset(),
        changed=changed,
    )


class _Repository:
    """Flushes as scripted; ``changed`` per bot_id, default ``False``."""

    def __init__(self, changed: dict[str, bool] | None = None) -> None:
        self.flush_calls: list[dict] = []
        self._changed = changed or {}
        self.raise_for: set[str] = set()

    def flush_installations(self, **kwargs) -> InstallationFlushPlan:
        self.flush_calls.append(kwargs)
        bot_id = str(kwargs["bot_id"])
        if bot_id in self.raise_for:
            raise RuntimeError(f"lock held on {bot_id}")
        return _plan(changed=self._changed.get(bot_id, False))


class _Bots:
    def __init__(
        self,
        *,
        bot: dict | None = _BOT,
        page: tuple[int, list[dict]] | None = None,
    ) -> None:
        self._bot = bot
        self._page = page if page is not None else (1, [_BOT])
        self.lookups: list[tuple[str, str]] = []
        self.list_calls: list[dict] = []

    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict | None:
        self.lookups.append((bot_id, owner_id))
        return self._bot

    def list_by_conditions(self, **kwargs) -> tuple[int, list[dict]]:
        self.list_calls.append(kwargs)
        return self._page


def _service(
    *, repository: _Repository | None = None, bots: _Bots | None = None
) -> tuple[InstallationBackfillService, _Repository, _Bots]:
    repository = repository if repository is not None else _Repository()
    bots = bots if bots is not None else _Bots()
    return (
        InstallationBackfillService(repository=repository, bot_repo=bots),
        repository,
        bots,
    )


def _bot(bot_id: str, *, owner_id: str = "owner") -> dict:
    return {**_BOT, "bot_id": bot_id, "owner_id": owner_id}


def test_the_implementation_satisfies_the_public_protocol():
    service, _repository, _bots = _service()
    assert isinstance(service, InstallationBackfillServiceProtocol)


def test_one_bot_flushes_with_the_same_scope_the_reader_uses():
    service, repository, bots = _service(
        repository=_Repository(changed={"bot-1": True})
    )

    outcome = service.backfill_bot(bot_id="bot-1", owner_id="owner")

    assert repository.flush_calls == [_EXPECTED_FLUSH]
    assert bots.lookups == [("bot-1", "owner")]
    assert outcome.bot_id == "bot-1"
    assert outcome.owner_id == "owner"
    assert outcome.changed is True
    assert outcome.error is None


def test_a_bot_already_agreeing_reports_unchanged():
    service, _repository, _bots = _service()

    assert service.backfill_bot(bot_id="bot-1", owner_id="owner").changed is False


def test_an_unknown_bot_is_an_error_not_a_silent_no_op():
    service, repository, _bots = _service(bots=_Bots(bot=None))

    with pytest.raises(LocalSkillNotFoundError):
        service.backfill_bot(bot_id="bot-1", owner_id="owner")

    assert repository.flush_calls == []


def test_a_page_flushes_every_bot_and_counts_the_ones_it_moved():
    bots = _Bots(page=(3, [_bot("bot-1"), _bot("bot-2"), _bot("bot-3")]))
    service, repository, _bots = _service(
        repository=_Repository(changed={"bot-1": True, "bot-3": True}), bots=bots
    )

    report = service.backfill_page(page=1, page_size=50)

    assert [call["bot_id"] for call in repository.flush_calls] == [
        "bot-1",
        "bot-2",
        "bot-3",
    ]
    assert (report.scanned, report.changed, report.failed) == (3, 2, 0)
    assert [(o.bot_id, o.changed) for o in report.outcomes] == [
        ("bot-1", True),
        ("bot-2", False),
        ("bot-3", True),
    ]


def test_a_page_passes_its_filters_through_untouched():
    service, _repository, bots = _service()

    service.backfill_page(
        owner_id="owner", engine_type="openclaw", page=2, page_size=10
    )

    assert bots.list_calls == [
        {
            "owner_id": "owner",
            "engine": "openclaw",
            "page": 2,
            "page_size": 10,
        }
    ]


def test_no_filters_means_every_bot_in_the_env():
    service, _repository, bots = _service()

    service.backfill_page()

    assert bots.list_calls == [
        {"owner_id": None, "engine": None, "page": 1, "page_size": 50}
    ]


def test_one_bots_failure_is_reported_and_the_rest_of_the_page_still_runs():
    repository = _Repository(changed={"bot-3": True})
    repository.raise_for = {"bot-2"}
    bots = _Bots(page=(3, [_bot("bot-1"), _bot("bot-2"), _bot("bot-3")]))
    service, _repository, _bots = _service(repository=repository, bots=bots)

    report = service.backfill_page()

    assert (report.scanned, report.changed, report.failed) == (3, 1, 1)
    failed = [o for o in report.outcomes if o.error is not None]
    assert [o.bot_id for o in failed] == ["bot-2"]
    assert "lock held on bot-2" in str(failed[0].error)
    # The failure did not stop the sweep, and did not read as a success.
    assert failed[0].changed is False
    assert [o.bot_id for o in report.outcomes if o.changed] == ["bot-3"]


def test_the_report_says_whether_more_pages_remain():
    bots = _Bots(page=(120, [_bot("bot-1")]))
    service, _repository, _bots = _service(bots=bots)

    assert service.backfill_page(page=1, page_size=50).has_more is True
    assert service.backfill_page(page=3, page_size=50).has_more is False


def test_the_layout_engine_wins_the_default_set_precedence():
    """A coding-template Bot resolves its Default Set by filesystem identity."""
    coding = {
        **_BOT,
        "active_engine": "claude_code",
        "template_type": "applicationCoding",
    }
    service, repository, _bots = _service(bots=_Bots(bot=coding))

    service.backfill_bot(bot_id="bot-1", owner_id="owner")

    assert repository.flush_calls[0]["engine_type"] == "claude_code"
    assert repository.flush_calls[0]["default_engine_types"] == (
        "aicoding",
        "claude_code",
    )
