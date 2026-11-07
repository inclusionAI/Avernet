"""Use the actual application injector, not the management unit-test world."""

from tests.community.framework.fixtures import (  # noqa: F401
    app_with_testing_modules,
    world,
)
