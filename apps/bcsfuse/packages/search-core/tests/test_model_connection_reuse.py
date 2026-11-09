"""Real TLS bursts reuse sockets; cancelled model calls leave no stuck requests."""

import asyncio
import json
import shutil
import ssl
import subprocess
from contextlib import suppress

import pytest
from search_core.config.embedding import EmbeddingSettings
from search_core.core.observation import upstream_status
from search_core.providers.async_model_http import ModelHTTPError
from search_core.providers.embedding import AsyncHttpEmbedding
from search_core.providers.reranker import AsyncHttpReranker


@pytest.mark.parametrize(
    "kind,status", [("embedding", 200), ("embedding", 429), ("reranker", 401)]
)
def test_bursts_reuse_tls_connections_and_recover_after_cancellation(
    kind, status, tmp_path, monkeypatch
):
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("local TLS integration requires openssl")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
        ],
        check=True,
        capture_output=True,
    )
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")

    async def run():
        burst = 128
        accepted = requests = closed = stalled = 0
        gates = [asyncio.Event() for _ in range(3)]
        all_stalled, all_closed = asyncio.Event(), asyncio.Event()
        handlers = set()

        async def serve(reader, writer):
            nonlocal accepted, requests, closed, stalled
            accepted += 1
            task = asyncio.current_task()
            handlers.add(task)
            try:
                while True:
                    headers = await reader.readuntil(b"\r\n\r\n")
                    length = next(
                        int(line.split(b":", 1)[1])
                        for line in headers.split(b"\r\n")
                        if line.lower().startswith(b"content-length:")
                    )
                    body = await reader.readexactly(length)
                    if b"hold-this-request" in body:
                        stalled += 1
                        if stalled == burst:
                            all_stalled.set()
                        await reader.read()
                        return
                    gate = gates[requests // burst]
                    requests += 1
                    if requests % burst == 0:
                        gate.set()
                    await gate.wait()
                    payload = (
                        {"data": [{"embedding": [1.0] * 64}]}
                        if status == 200
                        else {"error": "test rejection"}
                    )
                    data = json.dumps(payload).encode()
                    writer.write(
                        f"HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n\r\n".encode()
                        + data
                    )
                    await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            finally:
                writer.close()
                with suppress(ConnectionError):
                    await writer.wait_closed()
                closed += 1
                handlers.discard(task)
                if closed == accepted:
                    all_closed.set()

        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        server = await asyncio.start_server(serve, "127.0.0.1", 0, ssl=tls)
        url = f"https://localhost:{server.sockets[0].getsockname()[1]}"
        provider = (
            AsyncHttpEmbedding(
                EmbeddingSettings(
                    base_url=url,
                    auth_token="test-only",
                    model="test",
                    dimension=64,
                    timeout_ms=10000,
                )
            )
            if kind == "embedding"
            else AsyncHttpReranker(base_url=url, api_key="test-only", timeout=10)
        )

        async def request(query="query"):
            try:
                if kind == "embedding":
                    assert await provider.embed(query) == [1.0] * 64
                else:
                    await provider.rerank(query, [{"id": "a", "text": "doc"}])
            except ModelHTTPError as error:
                assert status >= 400 and upstream_status(error) == status
            else:
                assert status == 200

        pending = []
        try:
            for _ in range(2):
                await asyncio.wait_for(
                    asyncio.gather(*(request() for _ in range(burst))), 5
                )
            assert accepted == burst
            pending = [
                asyncio.create_task(request("hold-this-request")) for _ in range(burst)
            ]
            await asyncio.wait_for(all_stalled.wait(), 5)
            for task in pending:
                task.cancel()
            results = await asyncio.gather(*pending, return_exceptions=True)
            assert all(isinstance(result, asyncio.CancelledError) for result in results)
            await asyncio.wait_for(all_closed.wait(), 5)
            all_closed.clear()
            await asyncio.wait_for(
                asyncio.gather(*(request() for _ in range(burst))), 5
            )
            assert requests == burst * 3
            assert accepted == burst * 2
            await provider.aclose()
            await asyncio.wait_for(all_closed.wait(), 5)
            assert closed == accepted
        finally:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await provider.aclose()
            server.close()
            await server.wait_closed()
            remaining = tuple(handlers)
            for task in remaining:
                task.cancel()
            await asyncio.gather(*remaining, return_exceptions=True)

    asyncio.run(run())
