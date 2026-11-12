# Bot runtime transport errors

Use this page when a bot-runtime call fails with the opaque httpx send-hook
wrapper — ``HttpxCallingException('Error in httpx send hook')`` — or the same
text on any error type that hides the real transport failure. In deployments
where httpx is patched through a tracer send hook, every transport failure
(``ReadError``, ``RemoteProtocolError``, ``ConnectTimeout``, …) is re-raised
as this single message. The text alone therefore identifies neither the
failing call path nor the failure class: the same wrapper text surfaces on
MCP device sync, engine access through the BaaS invoke-http proxy, and
engine file reads/writes. Classify by the request behind the error before
merging incidents or choosing a fix.

## Establish the request

Record the failing operation (engine config read/write, file access, MCP
device sync, container result delivery), environment, request/trace
identifiers, Bot ID and owner. The wrapped cause usually survives on
``__cause__`` / ``__context__`` and the request may carry its URL; surface
both instead of trusting the wrapper text — see ``_exc_detail`` in the
[harness LLM service](../../src/agentclaw/community/core/harness/services/llm.py)
and the wrapper resolution note in the
[agentswarm OpenAI client](../../src/agentclaw/community/plugins/local/agentswarm/llm_openai.py).
The request URL is the primary discriminator between the paths below.

## Distinguish candidate causes

| Observation | Evidence to obtain | Next action |
| --- | --- | --- |
| Wrapper error behind an MCP device sync | Resolve the targeted engine from the device's conn_info and confirm the request path ends with ``/api/mcp`` (Arca/local: engine ``{url}/api/mcp``; BaaS: the invoke-http route computed by ``mcp_base_url_from_conn_info`` in the [MCP transport](../../src/agentclaw/community/core/devices/services/mcp_device_transport.py)). | Follow that transport's retry semantics (5xx retried with 1/2/4s backoff) and confirm whether retries were exhausted before escalating the failure. |
| Wrapper error behind an engine file API call | Filesystem log context (``[TeclawDeviceFileSystem.*]``) and the request path (``/api/v1/file/read``, ``/api/v1/file/remove``, …) in the [Teclaw filesystem](../../src/agentclaw/community/core/devices/services/teclaw_device_filesystem.py). | Check the transport leg and the read semantics before concluding the file is absent: ``read_file`` returns ``None`` both for a 404 and for a transport/timeout failure unless ``preserve_read_errors`` is set, and only 2xx is accepted for ``delete_tree``. A read that timed out can therefore present exactly like a missing file — a 404-shaped result is not evidence of absence. |
| Wrapper error behind engine access through the BaaS invoke-http proxy | Request URL of the shape ``{baas_base}/api/v1/bots/{tenant}/{bot_uuid}/invoke-http/{port}{path}`` built by the [invoke-http transports](../../src/agentclaw/community/core/devices/services/baas_invoke_transport.py). | Determine which leg failed: a 401 means a stale token (refresh-and-retry), while other transport failures point at the BaaS gateway or the engine behind the softlink. |
| Same wrapper text on different incidents | Request URL and entry operation for each incident. | Do not merge or deduplicate incidents by error text; the paths above have different owners and different fixes. |

## Follow the owning boundary

- Device access, filesystem semantics and transport selection belong to the
  [devices layer](../../src/agentclaw/community/core/devices/README.md); the
  invoke-http strategy is documented in
  [baas_invoke_transport.py](../../src/agentclaw/community/core/devices/services/baas_invoke_transport.py).
- Wrapper-classification helpers are consumer-side: they surface causes, they
  do not change them. Treat the send-hook wrapper itself as a deployment
  fact, not a code path in this repository.
- For runtime selection and binding failures (a different failure family that
  also ends in timeouts), start from
  [Runtime target diagnostics](runtime-targeting.md) instead.

## Evidence and verification

Classify by the observations above, then verify against the affected
contract: filesystem behavior via the device filesystem tests, MCP delivery
via the transport tests, and softlink behavior via the invoke-http transport
tests. Record repository revisions, the discriminating request URL, and
remaining gaps. An unclassified wrapper error stays unknown; it does not
default to "file missing" or "engine down". The wrapper itself rewrites every
transport failure identically, so reproducing the underlying class requires
the cause chain, not the message text.

Source-verified on the Avernet `dev` branch (2026-11-12): llm.py cause-chain
surfacing, llm_openai.py wrapper resolution, mcp_device_transport.py retry
semantics, teclaw_device_filesystem.py read/404 semantics, and
baas_invoke_transport.py URL shape.
