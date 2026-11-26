import httpx
import pytest

from avernet_evolution.plugins.job_http import HttpWorkerTransport


async def test_only_model_callbacks_use_long_deadline():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=30) as client:
        transport = HttpWorkerTransport("http://controller.test", client=client)
        await transport.models_complete("job", "fence", messages=[])
        await transport.heartbeat("job", "fence")
    assert requests[0].extensions["timeout"]["read"] == 260
    assert requests[1].extensions["timeout"]["read"] == 30


async def test_model_callback_does_not_echo_error_body():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500, text="fixture-secret and prompt"))
    ) as client:
        with pytest.raises(RuntimeError, match="job protocol error 500") as exc:
            await HttpWorkerTransport("http://controller.test", client=client).models_complete(
                "job", "fence", messages=[]
            )
    assert "secret" not in str(exc.value)
