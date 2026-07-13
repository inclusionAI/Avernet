from __future__ import annotations

from agentclaw.community.di.modules.infrastructure.test.http_client import (
    TestHttpClientModule,
)
from agentclaw.community.plugins.local.http_client import LocalHttpClient


def test_test_http_clients_are_local_and_scoped():
    module = TestHttpClientModule()

    baas = module.baas_http_client()
    bcn = module.bcn_http_client()
    general = module.general_http_client()
    masa = module.masa_agent_eval_http_client()

    assert isinstance(baas, LocalHttpClient)
    assert isinstance(bcn, LocalHttpClient)
    assert isinstance(general, LocalHttpClient)
    assert isinstance(masa, LocalHttpClient)
    assert baas._base_url == "http://localhost:8890"
    assert bcn._base_url == "http://localhost:8891"
    assert general._base_url == ""
    assert masa._base_url == "http://localhost:8080"
    assert len({id(baas), id(bcn), id(general), id(masa)}) == 4
