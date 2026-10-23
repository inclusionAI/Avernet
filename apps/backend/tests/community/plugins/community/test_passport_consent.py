"""ManualConsentPassportPlugin prod-parity pins (D8).

The plugin under test is the community build's tcauthmng stand-in: a PENDING
application per key that no headless caller can flip (applyFirst bootstraps
headlessly, the trusted first-passport grant), a decision that only the
control flips, and post-approval reads that must be byte-equal to what
``SelfIssuedPassportPlugin`` would have said so the creation flow completes.
"""
from __future__ import annotations

import pytest

from agentclaw.community.plugins.community.passport import (
    SelfIssuedPassportPlugin,
    _token_for,
)
from agentclaw.community.plugins.community.passport_consent import (
    APPLICATION_STATUS_ISSUED,
    APPLICATION_STATUS_PENDING,
    APPLICATION_STATUS_REJECTED,
    ManualConsentPassportPlugin,
    NullPassportManualConsentControl,
    PassportConsentAlreadyDecidedError,
)
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope,
    get_current_avernet_tenant,
)

_BASE_URL = "http://backend-simulator:31231"
_BOT = "sim-bot-1"
_OWNER = "workno-1"


def _apply(plugin: ManualConsentPassportPlugin, **overrides):
    kwargs = dict(
        bot_id=_BOT,
        owner_workno=_OWNER,
        mcp_codes=["mcp-a"],
        bot_name="Simulator Bot",
        bot_desc="a manual-consent fixture bot",
        engine_type="openclaw",
    )
    kwargs.update(overrides)
    return plugin.apply_agent_passport(**kwargs)


def _application_id(apply_result: dict) -> str:
    import urllib.parse

    query = urllib.parse.urlsplit(apply_result["iframe_url"]).query
    return dict(urllib.parse.parse_qsl(query))["app"]


@pytest.fixture
def plugin() -> ManualConsentPassportPlugin:
    return ManualConsentPassportPlugin(consent_base_url=_BASE_URL)


def test_apply_registers_pending_and_pins_the_consent_url_shapes(plugin):
    result = _apply(plugin)

    assert result["token"] is None
    assert result["agent_code"] is None
    application_id = _application_id(result)
    assert application_id.startswith("sim-passport-")

    assert result["iframe_url"] == (
        f"{_BASE_URL}/simulator/agentpass/consent?app={application_id}"
    )
    assert result["redirect_url"] == (
        f"{_BASE_URL}/simulator/agentpass/applications/{application_id}"
    )


def test_query_auth_says_pending_while_undecided(plugin):
    _apply(plugin)
    assert plugin.query_auth_status(_BOT, _OWNER) == {
        "status": APPLICATION_STATUS_PENDING
    }


def test_reapply_serves_the_same_pending_application(plugin):
    first = _apply(plugin)
    second = _apply(plugin, bot_name="Another name")

    assert second["iframe_url"] == first["iframe_url"]
    assert second["redirect_url"] == first["redirect_url"]
    assert len(plugin.list_applications()) == 1


def test_first_passport_issues_inline_and_registers_the_answer(plugin):
    result = plugin.apply_first_agent_passport(
        bot_id=_BOT, owner_workno=_OWNER, mcp_codes=[]
    )

    assert result == {
        "token": _token_for(_BOT),
        "agent_code": _BOT,
        "iframe_url": None,
        "redirect_url": None,
    }
    assert plugin.query_auth_status(_BOT, _OWNER) == {
        "status": APPLICATION_STATUS_ISSUED,
        "token": _token_for(_BOT),
    }


def test_first_passport_record_then_apply_serves_the_issued_answer(plugin):
    plugin.apply_first_agent_passport(bot_id=_BOT, owner_workno=_OWNER, mcp_codes=[])
    result = _apply(plugin)

    assert result == {
        "token": _token_for(_BOT),
        "agent_code": _BOT,
        "iframe_url": None,
        "redirect_url": None,
    }


def test_approval_flips_pending_to_issued_with_post_decision_read_parity(plugin):
    application_id = _application_id(_apply(plugin))

    assert plugin.decide(application_id, approve=True, reason=None) == (
        APPLICATION_STATUS_ISSUED
    )

    self_issued = SelfIssuedPassportPlugin()
    assert plugin.query_auth_status(_BOT, _OWNER) == self_issued.query_auth_status(
        _BOT, _OWNER
    )
    assert plugin.query_token(_BOT, _OWNER) == self_issued.query_token(_BOT, _OWNER)
    assert plugin.query_agent_passport(_BOT, _OWNER) == self_issued.query_agent_passport(
        _BOT, _OWNER
    )
    self_issued_df = self_issued.query_agent_passport(_BOT, _OWNER)
    ours_df = plugin.query_agent_passport(_BOT, _OWNER)
    assert ours_df["agent_code"] == self_issued_df["agent_code"] == _BOT


def test_rejection_flips_pending_to_rejected_and_stays_tokenless(plugin):
    application_id = _application_id(_apply(plugin))

    assert plugin.decide(application_id, approve=False, reason="not mine") == (
        APPLICATION_STATUS_REJECTED
    )

    assert plugin.query_auth_status(_BOT, _OWNER) == {
        "status": APPLICATION_STATUS_REJECTED
    }
    assert plugin.query_token(_BOT, _OWNER) is None
    assert plugin.query_agent_passport(_BOT, _OWNER) is None


def test_reapply_after_rejection_yields_the_neither_token_nor_url_answer(plugin):
    application_id = _application_id(_apply(plugin))
    plugin.decide(application_id, approve=False)

    result = _apply(plugin)

    assert result == {
        "token": None,
        "agent_code": None,
        "iframe_url": None,
        "redirect_url": None,
    }


def test_unknown_key_query_answers_none(plugin):
    _apply(plugin)
    assert plugin.query_auth_status("other-bot", _OWNER) is None
    assert plugin.query_token("other-bot", _OWNER) is None
    assert plugin.query_agent_passport("other-bot", _OWNER) is None
    assert plugin.decide("sim-passport-doesnotexist", approve=True) is None


def test_deciding_twice_raises_the_already_decided_error(plugin):
    application_id = _application_id(_apply(plugin))
    plugin.decide(application_id, approve=True)

    with pytest.raises(PassportConsentAlreadyDecidedError):
        plugin.decide(application_id, approve=False)


def test_control_facts_and_listing_shapes(plugin):
    _apply(plugin, bot_name="Alpha")
    _apply(plugin, bot_id="sim-bot-2", owner_workno="workno-2", bot_name="Beta")
    _apply(plugin, bot_id="sim-bot-3", owner_workno="workno-3", bot_name="Gamma")

    listing = plugin.list_applications()
    assert [item["status"] for item in listing] == [
        APPLICATION_STATUS_PENDING,
    ] * 3
    assert sorted(item["bot_id"] for item in listing) == [
        "sim-bot-1",
        "sim-bot-2",
        "sim-bot-3",
    ]
    for item in listing:
        assert set(item) >= {
            "application_id",
            "bot_id",
            "owner_workno",
            "status",
            "created_at",
        }

    facts = plugin.get_application(listing[-1]["application_id"])
    assert facts["bot_name"] == "Alpha"
    assert facts["tenant"] == get_current_avernet_tenant()
    assert facts["status"] == APPLICATION_STATUS_PENDING

    only_alpha_items = plugin.list_applications(bot_id=_BOT)
    assert len(only_alpha_items) == 1
    assert only_alpha_items[0]["bot_id"] == _BOT
    only_alpha_facts = plugin.get_application(only_alpha_items[0]["application_id"])
    assert only_alpha_facts["bot_name"] == "Alpha"


def test_registry_keys_include_the_ambient_tenant(plugin):
    _apply(plugin)
    assert len(plugin.list_applications()) == 1

    with avernet_tenant_scope("tenant-b"):
        # Key-scoped reads stay isolated: the teamclaw key is invisible from
        # tenant-b, and a key tenant-b never registered is unknown even
        # though (bot_id, owner) exists under the default tenant.
        assert plugin.query_auth_status(_BOT, "owner-never-applied-here") is None

        # Keyed writes register under the ambient tenant, not the key's origin.
        _apply(plugin)
        other_facts = plugin.get_application(
            plugin.list_applications(bot_id=_BOT)[0]["application_id"]
        )
        assert other_facts["tenant"] == "tenant-b"
        assert len(plugin.list_applications()) == 2

        # The decision surfaces (listing/decide) stay tenant-agnostic on
        # purpose: scripts and humans arrive on the default tenant, so they
        # must see every tenant's applications, never just their own.
        for item in plugin.list_applications():
            assert item["status"] == APPLICATION_STATUS_PENDING

    assert len(plugin.list_applications()) == 2
    assert plugin.query_auth_status(_BOT, _OWNER) == {
        "status": APPLICATION_STATUS_PENDING
    }


def test_decisions_are_addressed_by_application_id_not_ambient_tenant(plugin):
    """A human approves by following a URL, not by joining the bot's tenant.

    Scoping ``decide`` to the ambient tenant would strand every non-default
    tenant's creation at the gate, because the consent click arrives on the
    server-default tenant. The unguessable application id is the authority.
    """
    application_id = _application_id(_apply(plugin))
    with avernet_tenant_scope("tenant-b"):
        assert plugin.decide(application_id, approve=True) == (
            APPLICATION_STATUS_ISSUED
        )
    assert plugin.query_auth_status(_BOT, _OWNER) == {
        "status": APPLICATION_STATUS_ISSUED,
        "token": _token_for(_BOT),
    }


def test_decision_by_unknown_application_returns_none_instead_of_raising(plugin):
    assert plugin.decide("sim-passport-no-such", approve=True) is None


def test_null_control_answers_the_mode_off_vocabulary():
    null = NullPassportManualConsentControl()
    assert null.available is False
    assert null.decide("any", approve=True) is None
    assert null.get_application("any") is None
    assert null.list_applications() == []


def test_manual_plugin_advertises_itself_as_available(plugin):
    assert plugin.available is True


def test_concurrent_applies_for_one_key_stay_one_application(plugin):
    import threading

    results: list[dict] = []
    barrier = threading.Barrier(4)

    def hammer():
        barrier.wait()
        results.append(_apply(plugin))

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    iframe_urls = {result["iframe_url"] for result in results}
    assert len(iframe_urls) == 1
    assert len(plugin.list_applications()) == 1