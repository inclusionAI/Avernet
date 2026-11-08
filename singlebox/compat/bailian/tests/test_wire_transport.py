"""Only fake local BCS frames: no engine, API key or model invocation."""
import asyncio
from pathlib import Path
import sys
import unittest

from aiohttp import ClientSession, web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wire_probe import WireProbe


class TransportTest(unittest.IsolatedAsyncioTestCase):
    async def test_forwards_http_and_bidirectional_ws_without_recording_auth(self):
        seen = []
        abort = {'type': 'req', 'id': 'abort-1', 'method': 'chat.abort', 'params': {'run_id': 'run-1'}}
        ack = {'type': 'res', 'id': 'abort-1', 'ok': True, 'payload': {'aborted_run_ids': ['run-1']}}
        async def handler(request):
            if request.path == '/ws/bot':
                ws = web.WebSocketResponse()
                await ws.prepare(request)
                seen.append(await ws.receive_json())
                await ws.send_json(abort)
                seen.append(await ws.receive_json())
                await ws.close()
                return ws
            return web.json_response({'body': await request.json(), 'query': request.query['a'],
                                      'token': request.headers['X-BCS-Bot-Token']})
        app = web.Application()
        app.router.add_route('*', '/{path:.*}', handler)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        upstream = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
        probe = WireProbe(upstream)
        port = probe.start()
        try:
            async with ClientSession() as client:
                async with client.post(f'http://127.0.0.1:{port}/echo?a=value',
                                       json={'hello': '世界'}, headers={'X-BCS-Bot-Token': 'fake'}) as response:
                    self.assertEqual(await response.json(), {'body': {'hello': '世界'}, 'query': 'value', 'token': 'fake'})
                async with client.ws_connect(f'http://127.0.0.1:{port}/ws/bot') as ws:
                    connect = {'type': 'req', 'method': 'connect', 'params': {'token': 'fake-secret'}}
                    await ws.send_json(connect)
                    self.assertEqual(await ws.receive_json(), abort)
                    await ws.send_json(ack)
                    await ws.receive()
                self.assertEqual(seen, [connect, ack])
                self.assertEqual(probe.frames, [('bcs', abort), ('engine', ack)])
        finally:
            await asyncio.to_thread(probe.close)
            await runner.cleanup()
