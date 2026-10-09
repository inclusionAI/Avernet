"""Per-concern infrastructure DI modules — the concern × profile matrix.

Each module under this package binds **one** infrastructure concern for **one**
profile and imports only that profile's plugin. ``modules_for(profile)``
(``di/profile_modules.py``) assembles a profile's column from these. This is
the decomposition of the former ``infrastructure_module.py`` monolith (B1
Group C): a business module never imports a plugin; a column module imports
only its own profile's plugin.

Naming: ``Corp<Concern>Module`` (prod plugins), ``Test<Concern>Module``
(local test doubles), ``Community<Concern>Module`` (deployable OSS impls —
stubs in B1, filled by B3–B7).
"""
