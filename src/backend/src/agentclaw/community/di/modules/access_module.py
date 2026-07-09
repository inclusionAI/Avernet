"""AccessModule — production singletons for the access module.

Bindings:

- ``PolicyRepository`` — a single unified ORM body
  (``plugins/policy_repository.py``) that runs on both OceanBase
  prod and SQLite via the injected DatabasePlugin. No override module.
- ``PolicyService`` and ``UserService`` — mode-agnostic
  ``@singleton`` self-bindings; both have ``@inject`` on their
  constructors and resolve the repo through the injector. They
  share the same ``PolicyRepository`` singleton.
"""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.policy_service import PolicyServiceProtocol
from agentclaw.community.api.user_service import UserServiceProtocol
from agentclaw.community.core.access.repository import PolicyRepository
from agentclaw.community.core.access.services.policy_service import PolicyService
from agentclaw.community.core.access.services.user_service import UserService
from agentclaw.community.log import get_logger
from agentclaw.community.plugins.policy_repository import PolicyRepository as UnifiedPolicyRepository


logger = get_logger()


class AccessModule(Module):
    """Production bindings for the access module."""

    def configure(self, binder: Binder) -> None:
        binder.bind(PolicyService, to=PolicyService, scope=singleton)
        binder.bind(UserService, to=UserService, scope=singleton)
        # Single unified ORM impl — runs on prod OceanBase and SQLite
        # via the injected DatabasePlugin (@inject ctor).
        binder.bind(
            PolicyRepository,
            to=UnifiedPolicyRepository,
            scope=singleton,
        )

    # Service API Protocol → concrete service aliases so routers can
    # resolve Injected(<X>Protocol) without naming the concrete class.
    @singleton
    @provider
    @inject
    def _policy_service_protocol(self, svc: PolicyService) -> PolicyServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _user_service_protocol(self, svc: UserService) -> UserServiceProtocol:
        return svc
