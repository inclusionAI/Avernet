"""``bot_id`` is globally unique; "first bot" is a data question.

Historically ``generate_bot_id`` returned ``"default"`` for an owner's first
bot (confined to the default tenant after the partial #556 fix). That id was
unique only per owner *per tenant*, yet every identity derived from it — the
Passport / AgentPass principal keyed on ``(botId, ownerWorkno)``, the issued
``agent_code``, and the BCN record keyed on ``"{bot_id}:{owner_id}"`` — carries
no tenant field to disambiguate it, so two tenants sharing a principal
collapsed to one record.

The full fix retires the ``"default"`` shortcut entirely: ``generate_bot_id``
always returns a globally-unique ``yyyymmdd_xxxxxxxx`` id, so the collision is
impossible at the source regardless of tenant. Properties that must hold:

1. No tenant ever mints ``"default"`` (the retirement), **and**
2. ``is_first_bot`` is derived from ``count_by_owner == 0`` (a data question,
   not an id-string proxy), so a genuinely-first bot in any tenant still picks
   ``apply_first_agent_passport``.

Regression for issue #556.
"""
import pytest
from unittest.mock import MagicMock

from agentclaw.community.core.bot_management.services.bot_service import (
    BotService,
    generate_bot_id,
)
from agentclaw.community.utils.avernet_tenant import (
    DEFAULT_AVERNET_TENANT,
    avernet_tenant_scope,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def repo_without_default():
    """Repository stub (shape kept; id allocation no longer reads it)."""
    repo = MagicMock()
    repo.exists_by_owner_and_bot_id.return_value = False
    return repo


def _bare_service(repository) -> BotService:
    """Only the slice ``is_first_bot`` touches."""
    svc = BotService.__new__(BotService)
    svc._repository = repository
    return svc


class TestGenerateBotIdIsGloballyUnique:
    """The retirement: every tenant gets a generated id, never ``"default"``."""

    def test_default_tenant_never_mints_default(self, repo_without_default):
        with avernet_tenant_scope(DEFAULT_AVERNET_TENANT):
            bot_id = generate_bot_id("85020", repo_without_default)
        assert bot_id != "default"
        assert len(bot_id) == 17  # yyyymmdd + "_" + 8 random chars

    def test_outside_any_request_never_mints_default(self, repo_without_default):
        # get_current_avernet_tenant() is total: background work and ad-hoc
        # scripts run as the default tenant, and still get a generated id.
        bot_id = generate_bot_id("85020", repo_without_default)
        assert bot_id != "default"
        assert len(bot_id) == 17

    def test_non_default_tenant_gets_generated_id(self, repo_without_default):
        with avernet_tenant_scope("acme"):
            bot_id = generate_bot_id("85020", repo_without_default)
        assert bot_id != "default"
        assert len(bot_id) == 17

    def test_same_owner_across_tenants_does_not_collide(self, repo_without_default):
        """The exact scenario in #556: same owner, two tenants, distinct ids."""
        with avernet_tenant_scope(DEFAULT_AVERNET_TENANT):
            first = generate_bot_id("85020", repo_without_default)
        with avernet_tenant_scope("acme"):
            second = generate_bot_id("85020", repo_without_default)
        assert first != "default"
        assert second != "default"
        assert first != second

    def test_two_non_default_tenants_do_not_collide(self, repo_without_default):
        with avernet_tenant_scope("acme"):
            first = generate_bot_id("85020", repo_without_default)
        with avernet_tenant_scope("globex"):
            second = generate_bot_id("85020", repo_without_default)
        assert first != "default"
        assert second != "default"
        assert first != second

    def test_allocation_does_not_consult_owner_history(self, repo_without_default):
        """Id allocation no longer depends on exists_by_owner_and_bot_id."""
        with avernet_tenant_scope(DEFAULT_AVERNET_TENANT):
            generate_bot_id("85020", repo_without_default)
        # The retired "default" shortcut read this; the new impl must not.
        repo_without_default.exists_by_owner_and_bot_id.assert_not_called()


class TestIsFirstBotIsDataDriven:
    """``is_first_bot`` must not infer "first" from the id being "default".

    It selects ``apply_first_agent_passport`` vs ``apply_agent_passport`` in
    ``create_flow``. Inferring it from the id string answers False for a
    genuinely-first bot in any tenant, which would send a brand-new owner down
    the non-first-bot Passport branch.
    """

    def test_no_bots_is_first(self):
        repo = MagicMock()
        repo.count_by_owner.return_value = 0

        assert _bare_service(repo).is_first_bot("85020") is True
        repo.count_by_owner.assert_called_once_with("85020")

    def test_existing_bots_is_not_first(self):
        repo = MagicMock()
        repo.count_by_owner.return_value = 1

        assert _bare_service(repo).is_first_bot("85020") is False

    def test_does_not_depend_on_a_default_bot_existing(self):
        """An owner whose only bot has a generated id is not a first-bot owner."""
        repo = MagicMock()
        repo.count_by_owner.return_value = 1
        # Would have answered "first bot" under the old id-based proxy.
        repo.exists_by_owner_and_bot_id.return_value = False

        assert _bare_service(repo).is_first_bot("85020") is False