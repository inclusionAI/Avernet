"""Engine-package pytest fixtures shared across all test directories.

Lives at the package root so the ``test_injector`` factory reaches every test
tree (``tests/``, ``api/tests/``, ``engines/*/tests/``, …). Defines fixtures
only — no autouse, no import-time side effects.
"""
from __future__ import annotations

import dataclasses

import pytest

from engine.community.config import EngineConfig, load_engine_config
from engine.community.di.container import build_injector
from engine.community.di.modules.testing_config_module import TestingConfigModule
from engine.community.di.runtime_mode import RuntimeConfig, RuntimeMode
from engine.community.di.testing_modules import testing_modules_for


@pytest.fixture
def test_injector():
    """Factory that builds an ``Injector`` with optional config override.

    Replaces module-global config mutation (``set_chat_engine`` /
    ``reset_*``) in tests. Usage::

        inj = test_injector()                         # production config
        inj = test_injector(default_engine="aicoding")  # override one field
        inj = test_injector(config=my_engine_config)    # full override

    ``default_engine`` is a convenience that clones the real
    ``load_engine_config()`` and replaces just that field; pass ``config`` for
    a fully custom :class:`EngineConfig`. ``extra_modules`` layers additional
    overrides last (highest precedence).
    """

    def _make(
        config: EngineConfig | None = None,
        *,
        default_engine: str | None = None,
        runtime: RuntimeMode = RuntimeMode.LOCAL,
        extra_modules=None,
    ):
        rc = RuntimeConfig(runtime=runtime)
        modules = list(testing_modules_for(rc))
        if config is None and default_engine is not None:
            config = dataclasses.replace(
                load_engine_config(), default_engine=default_engine
            )
        if config is not None:
            modules.append(TestingConfigModule(config))
        if extra_modules:
            modules.extend(extra_modules)
        return build_injector(config=rc, extra_modules=modules)

    return _make
