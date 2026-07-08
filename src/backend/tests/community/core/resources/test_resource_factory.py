"""Regression guard for ResourceServiceFactory.

Binding-resolution tests only *construct* the factory singleton; they
never call ``create()``. A real prod break (``create()`` forwarding a
``bot_repo=`` kwarg the slim ``ResourceService`` does not accept) slipped
past the whole suite because every route/service test mocks the factory.

This test resolves the REAL factory from the test injector and actually
invokes ``create()`` — the exact call ``api/resources/router.py`` makes —
so a broken factory method body fails here instead of in production.
"""
from agentclaw.community.core.resources.factory import ResourceServiceFactory
from agentclaw.community.core.resources.service import ResourceService


def test_real_factory_create_returns_resource_service(test_injector):
    factory = test_injector.get(ResourceServiceFactory)
    # The router calls exactly this; it must not raise (e.g. TypeError
    # from an unexpected kwarg against the slim ResourceService ctor).
    svc = factory.create(bot_id="bot-1")
    assert isinstance(svc, ResourceService)
