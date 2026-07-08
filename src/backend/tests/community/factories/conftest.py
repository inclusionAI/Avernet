"""Make the endpoint-framework fixtures available to factory unit tests.

Same re-export pattern used by ``tests/framework/conftest.py`` and
``tests/endpoints/conftest.py``. The endpoint case-discovery is not
needed here (factories don't run the gate), so we don't trigger it
from this conftest.
"""
from tests.community.framework.fixtures import app_with_testing_modules, world  # noqa: F401
