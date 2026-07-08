"""Pre-DI ConfigProvider selection for the composition root (B2).

Configuration is read before the injector exists, so the active
:class:`~agentclaw.community.core.config.provider.ConfigProvider` is chosen here, by
profile, the same way the composition root reads the profile once. Both boot
entrypoints call :func:`register_config_provider` right after resolving the
deploy profile:

- ``adapters/http/app.py`` — before ``build_injector`` / first DI resolution.
- ``main.py`` (prod branch) — before the first ``sofa_config`` read, which the
  sofapy runner path performs before it imports ``app.py``.

Keeping the decision in one helper means the two entrypoints can't drift.
"""
from __future__ import annotations

from agentclaw.community.di.profile import DeployProfile


def register_config_provider(profile: DeployProfile) -> None:
    """Install ``profile``'s corp-runtime ConfigProvider into the registry.

    ``corp`` registers the sofapy-backed :class:`ConfigProvider` (so core reads
    configuration the corporate way). Every other profile (``community`` /
    ``test`` / ``singlebox``) leaves the registry on its YAML default. The corp
    branch lives in the corp-only ``di.corp_bootstrap`` module, loaded here via
    ``importlib`` (a string import, corp-profile only) so this shared file names
    no ``plugins/prod`` — a community build (without ``corp``) imports it fine.

    (DRM dynamic-config is a DI plugin — ``DRMReaderPlugin`` — injected into its
    consumers per profile, not a pre-DI registry; see ``plugin_api/drm.py``.)
    """
    if profile is DeployProfile.CORP:
        from importlib import import_module

        import_module("agentclaw.corp.di.corp_bootstrap").install_corp_config_provider()
