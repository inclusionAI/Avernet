"""Env enum behavior tests."""
import pytest

from agentclaw.community.core.devices.models import Env


def test_singlebox_is_not_a_data_environment():
    with pytest.raises(ValueError):
        Env.from_string("singlebox")


def test_dev_still_maps_to_dev():
    assert Env.from_string("dev") == Env.DEV


def test_invalid_still_raises():
    with pytest.raises(ValueError):
        Env.from_string("nonsense")
