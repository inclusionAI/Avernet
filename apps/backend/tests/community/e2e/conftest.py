"""Expose the endpoint-framework fixtures to e2e flow tests.

pytest only applies a ``conftest.py`` to its own directory and below;
``app_with_testing_modules`` / ``world`` live under ``tests/framework``
and ``tests/endpoints``, so ``tests/e2e/`` would not see them without
this re-export. Same pattern as ``tests/endpoints/conftest.py`` and
``tests/framework/conftest.py``.
"""
from tests.community.framework.fixtures import app_with_testing_modules, world  # noqa: F401
