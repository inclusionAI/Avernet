"""Dedicated authenticated llm-task call, without a persistent reply session."""

from urllib.parse import urlsplit, urlunsplit

import httpx

from engine.community.kernel.dws_execution import ExecutionError, HostingScope


async def classify(config, data: dict, prompt: str) -> HostingScope:
    try:
        value = await invoke(config, data, prompt, {
            "type": "object", "additionalProperties": False,
            "required": ["decision", "reason_code"],
            "properties": {
                "decision": {"enum": ["allow", "ignore", "uncertain"]},
                "reason_code": {"enum": ["inside_rule", "outside_rule", "insufficient_facts"]},
            },
        }, failure="scope_unavailable")
        return HostingScope.model_validate(value)
    except ValueError:
        raise ExecutionError("scope_unavailable") from None


async def invoke(config, data, prompt: str, schema: dict, *, failure: str) -> dict:
    url = urlsplit(config.url)
    endpoint = urlunsplit(
        (
            "https" if url.scheme == "wss" else "http",
            url.netloc,
            "/tools/invoke",
            "",
            "",
        )
    )
    body = {
        "tool": "llm-task",
        "args": {
            "prompt": prompt,
            "input": data,
            "schema": schema,
            "timeoutMs": 30000,
            "thinking": "off",
            "maxTokens": 2048,
        },
    }
    try:
        # Do not follow redirects or inherit a proxy carrying the gateway token.
        async with httpx.AsyncClient(
            timeout=35, trust_env=False, follow_redirects=False
        ) as client:
            response = await client.post(
                endpoint, json=body, headers={"Authorization": "Bearer " + config.token}
            )
            response.raise_for_status()
            if len(response.content) > 65536:
                raise ValueError("oversized scope result")
            value = response.json()
        if value.get("ok") is not True or value["result"].get("isError"):
            raise ValueError("failed scope task")
        result = value["result"]["details"]["json"]
        if not isinstance(result, dict):
            raise ValueError("non-object task result")
        return result
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        raise ExecutionError(failure) from None
