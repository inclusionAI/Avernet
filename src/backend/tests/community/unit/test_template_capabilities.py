from agentclaw.community.core.bot_management.capabilities import (
    can_join_bcn_as_provider,
    has_declared_capabilities,
)


def test_declared_capabilities_empty_dict_still_takes_over():
    template_config = {"capabilities": {}}

    assert has_declared_capabilities(template_config) is True
    assert can_join_bcn_as_provider(template_config) is False


def test_can_join_bcn_as_provider_reads_nested_flag():
    template_config = {"capabilities": {"bcn": {"join_as_provider": True}}}

    assert can_join_bcn_as_provider(template_config) is True


def test_missing_or_invalid_capabilities_default_false():
    assert has_declared_capabilities(None) is False
    assert can_join_bcn_as_provider(None) is False
    assert can_join_bcn_as_provider({"capabilities": None}) is False
    assert can_join_bcn_as_provider({"capabilities": {"bcn": None}}) is False


def test_can_join_bcn_as_provider_reads_flat_available_tc_flag():
    template_config = {"capabilities": {"enable_bcn_network": True}}

    assert can_join_bcn_as_provider(template_config) is True


def test_flat_false_takes_over_legacy_shape():
    template_config = {
        "capabilities": {
            "enable_bcn_network": False,
            "bcn": {"join_as_provider": True},
        }
    }

    assert can_join_bcn_as_provider(template_config) is False

