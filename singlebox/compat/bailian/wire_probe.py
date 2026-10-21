"""Transparent runner-only BCS wire observer. Raw frames stay in memory."""
import asyncio
import json
import shlex
from threading import Event, Thread


class WireProbe:
    def __init__(self, upstream='http://127.0.0.1:21000'):
        self.upstream = upstream
        self.frames = []
        self.abort_ids = set()
        self.ready = Event()
        self.error = None

    def start(self):
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()
        if not self.ready.wait(15) or self.error:
            raise RuntimeError('wire_probe_start_failed')
        return self.port

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._serve())
        except Exception as error:
            self.error = type(error).__name__
            self.ready.set()
        finally:
            self.loop.close()

    async def _serve(self):
        from aiohttp import ClientSession, WSMsgType, web
        self.done = asyncio.Event()
        async with ClientSession() as client:
            async def relay(request):
                url = self.upstream + request.raw_path
                headers = {k: v for k, v in request.headers.items()
                           if k.lower().startswith('x-') or k.lower() in ('authorization', 'content-type')}
                if request.headers.get('Upgrade', '').lower() == 'websocket':
                    async with client.ws_connect(url, headers=headers, max_msg_size=4 * 1024 * 1024) as upstream:
                        downstream = web.WebSocketResponse(max_msg_size=4 * 1024 * 1024)
                        await downstream.prepare(request)
                        async def pump(source, target, direction):
                            async for message in source:
                                if message.type == WSMsgType.TEXT:
                                    value = json.loads(message.data)
                                    # Ignore connect/auth frames: never retain credentials.
                                    if direction == 'bcs' and value.get('method') == 'chat.abort':
                                        self.abort_ids.add(value.get('id'))
                                    if (value.get('type') == 'res' and value.get('id') in self.abort_ids):
                                        self.frames.append((direction, value))
                                    if value.get('event') in ('agent', 'chat') or value.get('method') == 'chat.abort':
                                        self.frames.append((direction, value))
                                    await target.send_str(message.data)
                                elif message.type == WSMsgType.BINARY:
                                    await target.send_bytes(message.data)
                        tasks = [asyncio.create_task(pump(downstream, upstream, 'engine')),
                                 asyncio.create_task(pump(upstream, downstream, 'bcs'))]
                        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        return downstream
                async with client.request(request.method, url, data=await request.read(), headers=headers) as response:
                    return web.Response(status=response.status, body=await response.read(),
                                        headers={'Content-Type': response.headers.get('Content-Type', 'application/json')})
            app = web.Application()
            app.router.add_route('*', '/{path:.*}', relay)
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            self.port = site._server.sockets[0].getsockname()[1]
            self.ready.set()
            await self.done.wait()
            await runner.cleanup()

    def close(self):
        if self.loop.is_closed():
            return
        self.loop.call_soon_threadsafe(self.done.set)
        self.thread.join(timeout=10)


def tool_events(frames):
    events = []
    for direction, frame in frames:
        if direction != 'engine' or frame.get('event') != 'agent':
            continue
        payload = frame.get('payload', {})
        if payload.get('stream') == 'tool':
            # V3 is flat; V2 uses data for tool fields.
            events.append({**payload, **payload.get('data', {}),
                           'runId': payload.get('runId', payload.get('run_id')),
                           'sessionId': payload.get('sessionId', payload.get('session_key'))})
    return events


def contains_argument(value, text):
    if isinstance(value, str):
        if text in value:
            return True
        try:
            return any(text in word for word in shlex.split(value))
        except ValueError:
            return False
    if isinstance(value, dict):
        return any(contains_argument(item, text) for item in value.values())
    if isinstance(value, list):
        return any(contains_argument(item, text) for item in value)
    return False


def contains_text(value, text):
    if isinstance(value, str):
        return text in value
    if isinstance(value, dict):
        return any(contains_text(item, text) for item in value.values())
    if isinstance(value, list):
        return any(contains_text(item, text) for item in value)
    return False


def validate_tools(frames, filename, marker):
    starts = {}
    for event in tool_events(frames):
        call_id = event.get('toolCallId')
        if not event.get('runId') or not event.get('sessionId'):
            raise AssertionError('tool_run_or_session_missing')
        if not isinstance(call_id, str) or not call_id:
            raise AssertionError('tool_call_id_missing')
        if event.get('phase') == 'start':
            name = event.get('name', event.get('toolName'))
            args = event.get('args', event.get('arguments'))
            if isinstance(args, str):
                args = json.loads(args)
            if not name or not isinstance(args, dict):
                raise AssertionError('tool_name_or_arguments_invalid')
            starts[(event.get('runId'), call_id)] = (args, event.get('sessionId'))
        elif event.get('phase') == 'result':
            key = (event.get('runId'), call_id)
            if key not in starts:
                raise AssertionError('tool_result_without_matching_start')
            args, session = starts[key]
            if session != event.get('sessionId'):
                raise AssertionError('tool_result_session_mismatch')
            if contains_argument(args, filename) and contains_text(event.get('result'), marker):
                if event.get('isError') is True:
                    raise AssertionError('tool_result_error')
                return {'matched_call_id': call_id, 'arguments_object': True,
                        'result_marker_matched': True, 'run_session_matched': True}
    raise AssertionError('expected_tool_call_and_result_not_observed')


def abort_acknowledged(frames, run_id):
    requests = {frame['id'] for direction, frame in frames
                if direction == 'bcs' and frame.get('method') == 'chat.abort'
                and frame.get('params', {}).get('run_id') == run_id}
    return any(direction == 'engine' and frame.get('type') == 'res'
               and frame.get('id') in requests and frame.get('ok') is True
               and run_id in frame.get('payload', {}).get('aborted_run_ids', [])
               for direction, frame in frames)
