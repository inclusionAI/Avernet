"""Opt-in bootstrap; defer application imports until coverage has started.

Python loads sitecustomize before ``python -m coverage``. Importing DI here
would import Core before measurement. The one-shot loader wraps DI only when
the application imports it, still before boot can capture either builder alias.
SystemExit prevents a missing fixture from silently booting an unmodified app.
"""
import importlib.abc
import importlib.machinery
import os
import sys


def patch_builder(di):
    try:
        import agentclaw.community.di.container as container
        from agentclaw.community.di.profile import DeployProfile
        from tests.community.acceptance.bot_ownership_sync.fixture_module import OwnerHandoffFixtureModule

        from tests.community.acceptance.collaboration_repair.fixture_module import CollaborationRepairFixtureModule

        original = di.build_injector
        if getattr(original, "_owner_acceptance_installed", False) is True:
            return

        def build_injector(*, profile, extra_modules=None):
            modules = extra_modules
            if profile is DeployProfile.SINGLEBOX:
                modules = [*(extra_modules or ()), OwnerHandoffFixtureModule(), CollaborationRepairFixtureModule()]
            return original(profile=profile, extra_modules=modules)

        build_injector._owner_acceptance_installed = True
        di.build_injector = container.build_injector = build_injector
    except Exception:
        raise SystemExit("Owner handoff acceptance fixture bootstrap failed") from None


class FixtureLoader(importlib.abc.Loader):
    def __init__(self, original):
        self.original = original

    def create_module(self, spec):
        return self.original.create_module(spec)

    def exec_module(self, module):
        self.original.exec_module(module)
        patch_builder(module)


class FixtureFinder(importlib.abc.MetaPathFinder):
    _owner_acceptance_finder = True

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "agentclaw.community.di":
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            raise SystemExit("Owner handoff acceptance DI bootstrap missing")
        spec.loader = FixtureLoader(spec.loader)
        sys.meta_path.remove(self)
        return spec


def install():
    if not (
        os.environ.get("SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF") == "1"
        and os.environ.get("SINGLEBOX_COVERAGE") == "1"
        and os.environ.get("DEPLOY_PROFILE") == "singlebox"
    ):
        return
    if "agentclaw.community.di" in sys.modules:
        patch_builder(sys.modules["agentclaw.community.di"])
    elif not any(getattr(finder, "_owner_acceptance_finder", False) for finder in sys.meta_path):
        sys.meta_path.insert(0, FixtureFinder())


install()
