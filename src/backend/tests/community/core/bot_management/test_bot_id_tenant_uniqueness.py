"""``bot_id`` never collides across tenants, and "first bot" is a data question.

``bot_id`` is unique only per owner *per tenant*, and the ``"default"`` shortcut
in ``generate_bot_id`` reads through the ``BotModel`` tenant guard — so a second
tenant asking "does this owner already have a 'default' bot?" cannot see the
first tenant's row and correctly answers no. Both tenants would then mint
``bot_id="default"`` for the same owner, and every identity derived from it is
handed to a system with no tenant field to disambiguate it: the Passport /
AgentPass principal keyed on ``(botId, ownerWorkno)``, the ``agent_code``
AgentPass returns, and the BCN record keyed on ``"{bot_id}:{owner_id}"``.

Confining the shortcut to the default tenant fixes all of them at the source,
with no change to any external contract. Two properties have to hold together:

1. Non-default tenants never mint ``"default"`` (the fix), **and**
2. the default tenant's ids are byte-identical to before (no re-keying of
   Passport principals or BCN records that already exist).

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
    """An owner who has no ``"default"`` bot yet in the current tenant."""
    repo = MagicMock()
    repo.exists_by_owner_and_bot_id.return_value = False
    return repo


def _bare_service(repository) -> BotService:
    """Only the slice ``is_first_bot`` touches."""
    svc = BotService.__new__(BotService)
    svc._repository = repository
    return svc


class TestDefaultTenantIsUnchanged:
    """The fix must not re-key any identity that already exists."""

    def test_default_tenant_still_mints_default(self, repo_without_default):
        with avernet_tenant_scope(DEFAULT_AVERNET_TENANT):
            assert generate_bot_id("85020", repo_without_default) == "default"

    def test_outside_any_request_still_mints_default(self, repo_without_default):
        # get_current_avernet_tenant() is total: background work and ad-hoc
        # scripts run as the default tenant, and must keep the old behavior.
        assert generate_bot_id("85020", repo_without_default) == "default"

    def test_default_tenant_second_bot_is_generated(self):
        repo = MagicMock()
        repo.exists_by_owner_and_bot_id.return_value = True

        with avernet_tenant_scope(DEFAULT_AVERNET_TENANT):
            bot_id = generate_bot_id("85020", repo)

        assert bot_id != "default"
        assert len(bot_id) == 17  # yyyymmdd + "_" + 8 random chars


class TestOtherTenantsNeverMintDefault:
    """The collision itself: same owner, two tenants, distinct ids."""

    def test_non_default_tenant_gets_generated_id(self, repo_without_default):
        with avernet_tenant_scope("acme"):
            bot_id = generate_bot_id("85020", repo_without_default)

        assert bot_id != "default"
        assert len(bot_id) == 17

    def test_same_owner_across_tenants_does_not_collide(self, repo_without_default):
        """The exact scenario in #556: every owner's first bot was "default"."""
        with avernet_tenant_scope(DEFAULT_AVERNET_TENANT):
            first = generate_bot_id("85020", repo_without_default)
        with avernet_tenant_scope("acme"):
            second = generate_bot_id("85020", repo_without_default)

        assert first == "default"
        assert second != first

    def test_two_non_default_tenants_do_not_collide(self, repo_without_default):
        with avernet_tenant_scope("acme"):
            first = generate_bot_id("85020", repo_without_default)
        with avernet_tenant_scope("globex"):
            second = generate_bot_id("85020", repo_without_default)

        assert first != "default"
        assert second != "default"
        assert first != second

    def test_shortcut_read_is_skipped_entirely(self, repo_without_default):
        """No point asking a tenant-scoped question whose answer cannot be used."""
        with avernet_tenant_scope("acme"):
            generate_bot_id("85020", repo_without_default)

        repo_without_default.exists_by_owner_and_bot_id.assert_not_called()


class TestIsFirstBotIsDataDriven:
    """``is_first_bot`` must not infer "first" from the id being "default".

    It selects ``apply_first_agent_passport`` vs ``apply_agent_passport`` in
    ``create_flow``. Inferring it from the id string answers False for a
    genuinely-first bot in any tenant that does not mint ``"default"``, which
    would send a brand-new owner down the non-first-bot Passport branch.
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
