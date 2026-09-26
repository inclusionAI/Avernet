"""DI modules — one ``Module`` per area (config, process, manager, …).

Production bindings live in ``<area>_module.py``; test overrides in
``testing_<area>_module.py`` (assembled by ``di.testing_modules``). Mirror of
``src/backend/src/agentclaw/di/modules/``.
"""
