"""Make the endpoint-framework fixtures available to framework self-tests.

Also triggers endpoint case-module discovery — required so the
coverage gate sees the full registry even when only the framework
tests are run (e.g. a PyCharm runner targeting one gate test).
Without this, ``ENDPOINT_CASES`` would be empty in that scope and the
gate would falsely report covered endpoints as NEW gaps.

Same re-export pattern is used by ``tests/endpoints/conftest.py`` so
case files in that tree can request these fixtures by name.
"""
from tests.community.framework._case_discovery import load_endpoint_case_modules
from tests.community.framework.fixtures import app_with_testing_modules, world  # noqa: F401

load_endpoint_case_modules()
