"""Consumer/adapter contracts: actual calls, restricted spec and failure cleanup."""

import json

import httpx
import pytest

from avernet_evolution.core.evolution_run.sandbox import ALLOWED_ENV_KEYS, job_worker_spec
from avernet_evolution.plugins.kubernetes_worker import SMOKE_COMMAND, KubernetesJobWorker

IMAGE = "registry.example/worker@sha256:" + "a" * 64


def spec(**kwargs):
    return job_worker_spec(
        worker_id="w1",
        image=IMAGE,
        job_endpoint="http://10.42.0.1:12345",
        job_token="test-token",
        **kwargs,
    )


class API:
    def __init__(self, *, fail_pod=False, phase="Succeeded"):
        self.calls, self.objects = [], {}
        self.fail_pod, self.phase = fail_pod, phase

    def request(self, req):
        body = json.loads(req.content) if req.content and req.content != b"null" else None
        self.calls.append((req.method, req.url.path, body))
        path = req.url.path
        if req.method == "POST":
            path += "/" + body["metadata"]["name"]
            if "/pods/" in path and self.fail_pod:
                return httpx.Response(403, json={"message": "private API body"})
            self.objects[path] = body | {"metadata": body["metadata"] | {"uid": "uid-real"}}
            return httpx.Response(201, json=self.objects[path])
        if req.method == "DELETE":
            self.objects.pop(path, None)
            return httpx.Response(200, json={})
        if path not in self.objects:
            return httpx.Response(404, json={})
        obj = self.objects[path]
        if "/pods/" in path:
            obj = obj | {
                "status": {
                    "phase": self.phase,
                    "podIP": "10.42.1.2",
                    "containerStatuses": [{"imageID": IMAGE, "state": {"terminated": {"exitCode": 0}}}],
                }
            }
        return httpx.Response(200, json=obj)


@pytest.mark.asyncio
async def test_kubernetes_restricts_actual_pod_and_waits_then_cleans():
    api = API()
    async with httpx.AsyncClient(
        base_url="https://kube.test", transport=httpx.MockTransport(api.request)
    ) as client:
        adapter = KubernetesJobWorker(client, namespace="worker-test", command=SMOKE_COMMAND)
        name = await adapter.create(spec())
        _, policy_path, policy = api.calls[0]
        _, pod_path, pod = api.calls[1]
        assert "networkpolicies" in policy_path and pod_path.endswith("/pods")
        assert policy["spec"]["egress"][0]["to"] == [{"ipBlock": {"cidr": "10.42.0.1/32"}}]
        assert policy["spec"]["egress"][0]["ports"] == [{"protocol": "TCP", "port": 12345}]
        body = pod["spec"]
        assert not body["automountServiceAccountToken"] and not body["enableServiceLinks"]
        assert body["securityContext"]["runAsNonRoot"]
        container = body["containers"][0]
        assert container["image"] == IMAGE
        assert {e["name"] for e in container["env"]} == ALLOWED_ENV_KEYS
        assert container["resources"]["limits"]["memory"] == "4096Mi"
        assert container["resources"]["requests"] == {"cpu": "1", "memory": "256Mi"}
        assert container["securityContext"] == {
            "readOnlyRootFilesystem": True,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
        }
        assert await adapter.exec(name, SMOKE_COMMAND, timeout_ms=1000) == 0
        assert adapter.evidence[name]["image_id"] == IMAGE
        await adapter.destroy(name)
        assert not api.objects and adapter.evidence[name]["destroyed"]
        assert api.calls[-1][0] == "DELETE" and "networkpolicies" in api.calls[-1][1]
        assert "test-token" not in json.dumps(adapter.evidence)


@pytest.mark.asyncio
async def test_kubernetes_partial_create_rolls_back_policy_without_leaking_body():
    api = API(fail_pod=True)
    async with httpx.AsyncClient(
        base_url="https://kube.test", transport=httpx.MockTransport(api.request)
    ) as client:
        adapter = KubernetesJobWorker(client, namespace="worker-test", command=SMOKE_COMMAND)
        with pytest.raises(RuntimeError, match="failed \\(403\\)") as error:
            await adapter.create(spec())
        assert "private API body" not in str(error.value)
        assert not api.objects


@pytest.mark.asyncio
async def test_kubernetes_refuses_dns_endpoint_before_api_call():
    api = API()
    async with httpx.AsyncClient(
        base_url="https://kube.test", transport=httpx.MockTransport(api.request)
    ) as client:
        adapter = KubernetesJobWorker(client, namespace="worker-test", command=SMOKE_COMMAND)
        import dataclasses

        with pytest.raises(ValueError):
            await adapter.create(dataclasses.replace(spec(), job_endpoint="http://broad-host:8000"))
        assert not api.calls


@pytest.mark.asyncio
async def test_kubernetes_carries_only_named_pull_secrets_and_records_them():
    api = API()
    async with httpx.AsyncClient(
        base_url="https://kube.test", transport=httpx.MockTransport(api.request)
    ) as client:
        adapter = KubernetesJobWorker(
            client, namespace="worker-test", command=SMOKE_COMMAND, image_pull_secrets=("registry-cred",)
        )
        name = await adapter.create(spec())
        pod = next(body for method, path, body in api.calls if method == "POST" and path.endswith("/pods"))
        assert pod["spec"]["imagePullSecrets"] == [{"name": "registry-cred"}]
        assert adapter.evidence[name]["image_pull_secrets"] == ["registry-cred"]
        await adapter.destroy(name)


@pytest.mark.parametrize("name", ["Registry/cred", "a b", "", "x" * 300])
def test_kubernetes_refuses_an_invalid_pull_secret_name(name):
    with pytest.raises(ValueError, match="pull secret"):
        KubernetesJobWorker(object(), namespace="worker-test", image_pull_secrets=(name,))
