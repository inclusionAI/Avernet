"""Single-operation loopback capture for an exclusively owned local product CLI.

This is not a mock response server or a remote proxy. A finite admitted CLI
request is forwarded once to the live owned BCS with its original body and
authentication. Explicit legacy local fuse and Group-get contracts preserve absent auth;
it never strips or invents a credential. Only a runner correlation header and the bounded workstation Origin/Referer ingress pair are forwarded. No route/auth fallback,
redirect, retry, arbitrary destination, raw log or response persistence exists.

Admitted operations form a CLOSED taxonomy. Every future CLI story lands as a new
bounded ``auth_contract`` (or an explicit operation body on the default contract),
never as a loosened exchange:

* ``owned-local-legacy-fuse-no-auth-v1`` — one exact POST to ``/groups/{id}/fuse``
  with the two-participant ``how to align?`` body; Authorization absent by design;
  any other path, method, participant shape or token injection is refused.
* ``owned-local-legacy-group-get-no-auth-v1`` — the five exact Group reads
  (get-primary, get-after-add, get-after-status, get-before-terminate,
  get-termination) with Authorization absent and no list-escape path.
* ``owned-bot-bearer`` (default) — one exact method+path+body pair forwarded with
  ``Authorization: Bearer <owned-bot-token>``. Material admitted operations today:
  session-message creation (``/friends/request`` shape routed by the event's own
  path/method), Group status/terminate/complete conversations, Group creation
  (``POST /groups``), and the single admitted DELETE channel-unbind no-op against
  a missing binding.

Per-request bounds: exactly one dispatch attempt (sticky refusal afterwards);
method and path equality; Authorization header-list equality; forbidden headers
(Cookie, X-BCS-Service-Key, X-BCS-Bot-Token, X-Avernet-Principal,
Transfer-Encoding, X-Request-ID) must be absent; Content-Length single, numeric,
<= 8192 bytes; JSON body deep-equality against the admitted pair/decision;
upstream loopback only; response budget 65536 bytes; absolute 12s deadline with
cancellation fencing.

Wire-default rule: the product CLI's explicit Group-creation default
(``human_mention_notify_mode: "all"``) is materialized ONLY by
``_admitted_wire_body``; no other code path may mutate a request or decision body.
Any NEW product-CLI wire default MUST be added to ``_admitted_wire_body`` together
with a dedicated loopback test proving (a) the default is filled exactly when
absent, (b) an explicit producer-declared value passes through byte-identical,
and (c) an unrelated body addition is still refused.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import re
import threading
import socket
import time
from http.client import HTTPConnection
from urllib.parse import urlsplit

from .correlation import record_response_id
from .profile import unique_keys


class LocalCliExchangeError(RuntimeError):
    pass


class LocalCliExchange:
    def __init__(self, case, event: dict, *, token: str, body: object,
                 auth_contract: str = 'owned-bot-bearer'):
        if auth_contract == 'owned-local-legacy-fuse-no-auth-v1':
            match = re.fullmatch(r'/groups/([A-Za-z0-9_-]+)/fuse', event.get('path', ''))
            if (token != '' or event.get('operation') != 'fuse' or event.get('method') != 'POST'
                    or match is None or not isinstance(body, dict)
                    or set(body) != {'question', 'participants', 'focus', 'session_id', 'fusion_mode'}
                    or body['session_id'] != match.group(1) or body['question'] != 'how to align?'
                    or body['focus'] is not None or body['fusion_mode'] is not None
                    or not isinstance(body['participants'], list) or len(body['participants']) != 2
                    or any(not isinstance(item, str) or not item for item in body['participants'])
                    or len(set(body['participants'])) != 2):
                raise LocalCliExchangeError('unauthenticated capture is restricted to the explicit local legacy fuse contract')
            expected_authorization = None
        elif auth_contract == 'owned-local-legacy-group-get-no-auth-v1':
            if (token != '' or event.get('method') != 'GET' or body is not None
                    or event.get('operation') not in ('get-primary', 'get-after-add', 'get-after-status',
                                                     'get-before-terminate', 'get-termination')
                    or re.fullmatch(r'/groups/[A-Za-z0-9_-]+', event.get('path', '')) is None):
                raise LocalCliExchangeError('unauthenticated group reads require the explicit local legacy get contract')
            expected_authorization = None
        elif auth_contract == 'owned-bot-bearer' and isinstance(token, str) and token:
            expected_authorization = ['Bearer ' + token]
        else:
            raise LocalCliExchangeError('unsupported CLI capture authentication contract')
        if event.get('method') == 'DELETE':
            if (auth_contract != 'owned-bot-bearer' or body is not None
                    or event.get('operation') != 'channel-unbind'
                    or re.fullmatch(r'/channels/bindings/missing-channel-cli-[0-9a-f]{24}', event.get('path', '')) is None):
                raise LocalCliExchangeError('DELETE capture is restricted to an admitted missing Channel binding')
        event['auth_contract'] = auth_contract
        self.case, self.event, self.token, self.body = case, event, token, body
        self.attempts = 0
        self.forwarded = 0
        self.response = None
        self.failure = None
        self._stop = threading.Event()
        self._dispatch_lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._incoming = None
        self._upstream = None
        self._upstream_socket = None
        self.deadline = time.monotonic() + 12
        target = urlsplit(case.identities.origin)
        if target.scheme != 'http' or target.hostname != '127.0.0.1' or not target.port or target.path or target.query or target.fragment or target.username:
            raise LocalCliExchangeError('CLI exchange requires an owned loopback upstream')
        upstream_port = target.port
        exchange = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(1)
                with exchange._io_lock:
                    exchange._incoming = self.request
                    if exchange._stop.is_set():
                        self.request.close()
                        raise LocalCliExchangeError('CLI exchange is stopped')
                super().setup()

            def log_message(self, *_args):
                pass  # Neither request paths nor error bodies enter console logs.

            def parse_request(self):
                parsed = super().parse_request()
                exchange.attempts += 1
                if (not parsed or self.command not in ('GET', 'POST', 'PUT', 'DELETE')
                        or self.command in ('PUT', 'DELETE') and event['method'] != self.command):
                    exchange.failure = LocalCliExchangeError('unexpected CLI request or method')
                    if parsed:
                        self.send_error(405)
                    return False
                return True

            def do_GET(self):
                self.exchange()

            def do_POST(self):
                self.exchange()

            def do_PUT(self):
                self.exchange()

            def do_DELETE(self):
                self.exchange()

            def exchange(self):
                try:
                    if (exchange.attempts != 1 or self.command != event['method']
                            or self.path != event['path']
                            or self.headers.get_all('Authorization') != expected_authorization
                            or any(self.headers.get(name) is not None for name in
                                   ('Cookie', 'X-BCS-Service-Key', 'X-BCS-Bot-Token', 'X-Avernet-Principal',
                                    'Transfer-Encoding', 'X-Request-ID'))):
                        raise LocalCliExchangeError('CLI request differs from the finite admitted operation')
                    lengths = self.headers.get_all('Content-Length', [])
                    if (len(lengths) > 1 or lengths and (not lengths[0].isdigit() or int(lengths[0]) > 8192)):
                        raise LocalCliExchangeError('CLI request body exceeds the finite contract')
                    raw = exchange._read_bounded(self.rfile, int(lengths[0]) if lengths else 0)
                    parsed = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys) if raw else None
                    if (exchange.body is None and raw
                            or not exchange._body_matches(parsed)):
                        raise LocalCliExchangeError('CLI request body differs from the admitted pair or decision')
                    case._active()
                    event['dispatch_status'] = 'INTENDED'
                    case._save()  # Durable actual wire correlation before upstream dispatch.
                    case._active()
                    headers = {'Accept': 'application/json', 'Content-Type': 'application/json',
                               'X-Request-ID': event['submitted_request_id']}
                    if expected_authorization is not None:
                        headers['Authorization'] = expected_authorization[0]
                    client = self.headers.get('X-BCS-Client')
                    if client is not None and len(client) <= 128:
                        headers['X-BCS-Client'] = client
                    # Dual-environment profiles pin the workstation origin and
                    # the reviewed preprod station ingress honors the browser
                    # Origin/Referer pair; the owned loopback forwards that same
                    # finite pair so Singlebox rehearses the exact ingress
                    # contract. No fallback and no rewriting exists.
                    for ingress_name in ('Origin', 'Referer'):
                        ingress = self.headers.get(ingress_name)
                        if ingress is not None:
                            if len(ingress) > 2048:
                                raise LocalCliExchangeError(
                                    'CLI request ' + ingress_name + ' exceeds the finite contract')
                            headers[ingress_name] = ingress
                    upstream = HTTPConnection('127.0.0.1', upstream_port, timeout=1)
                    with exchange._io_lock:
                        exchange._upstream = upstream
                    try:
                        upstream.connect()
                        with exchange._io_lock:
                            exchange._upstream_socket = upstream.sock
                        # Cancellation and the dispatch claim share one lock. Once
                        # claimed, an interrupted write has an UNKNOWN outcome.
                        with exchange._dispatch_lock:
                            exchange._require_running()
                            case._active()
                            exchange.forwarded += 1
                            upstream.request(event['method'], event['path'], body=raw if raw else None, headers=headers)
                        response = upstream.getresponse()
                        with response:
                            status = response.status
                            payload = exchange._read_bounded(response, 65_537, exact=False)
                            if response.length not in (None, 0):
                                raise LocalCliExchangeError('CLI upstream body ended before its declared length')
                            event['http_status'] = status
                            record_response_id(event, response.headers.get('x-request-id'), source='HEADER',
                                               sensitive_values=((exchange.token,) if exchange.token else ()))
                    finally:
                        upstream.close()
                    if len(payload) > 65_536:
                        raise LocalCliExchangeError('CLI upstream response exceeds its byte budget')
                    exchange.response = json.loads(payload.decode('utf-8'), object_pairs_hook=unique_keys)
                    if event.get('operation') == 'request' and isinstance(exchange.response, dict):
                        data = exchange.response.get('data')
                        if isinstance(data, dict) and isinstance(data.get('id'), str) and data['id']:
                            from .profile import identifier
                            event['response_resource_id'] = identifier(data['id'], 'CLI returned request candidate')
                    event['dispatch_status'] = 'RESPONSE_RECEIVED'
                    exchange._require_running()
                    case._active()
                    case._save()
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except BaseException as error:
                    exchange.failure = error
                    try:
                        self.send_response(502)
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                    except (OSError, ValueError):
                        pass

        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.server.timeout = 0.1
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=self._serve, name='e2e-local-cli-exchange', daemon=True)

    @staticmethod
    def _admitted_wire_body(path: str, method: str, value: object) -> object:
        """Materialize the product CLI's explicit Group-creation default.

        `bcs-cli create-group` now serializes `human_mention_notify_mode: "all"`.
        The finite pair/decision contract still owns every other field and will
        not accept an unrelated addition.
        """
        if (path == '/groups' and method == 'POST' and isinstance(value, dict)
                and value.get('human_mention_notify_mode') is None):
            return {**value, 'human_mention_notify_mode': 'all'}
        return value

    def _body_matches(self, parsed: object) -> bool:
        expected = self._admitted_wire_body(self.event['path'], self.event['method'], self.body)
        if expected is None or parsed is None:
            return expected is None and parsed is None
        return (json.dumps(parsed, sort_keys=True, allow_nan=False)
                == json.dumps(expected, sort_keys=True, allow_nan=False))

    def _serve(self):
        try:
            while not self._stop.is_set():
                self.server.handle_request()
        except BaseException as error:
            self.failure = error

    def __enter__(self):
        try:
            self.thread.start()
        except BaseException:
            self.server.server_close()
            raise
        return self

    def _require_running(self):
        if self._stop.is_set() or time.monotonic() >= self.deadline:
            raise LocalCliExchangeError('CLI exchange was cancelled or exceeded its absolute deadline')

    def _read_bounded(self, stream, limit, *, exact=True):
        chunks = bytearray()
        while len(chunks) < limit:
            self._require_running()
            chunk = stream.read1(min(4096, limit-len(chunks)))
            if not chunk:
                if exact:
                    raise LocalCliExchangeError('CLI body ended before its declared length')
                break
            chunks.extend(chunk)
        self._require_running()
        return bytes(chunks)

    def __exit__(self, error_type, *_args):
        with self._dispatch_lock:
            self._stop.set()
            if error_type is not None:
                self.case.invalidated = True
        # Closing only the listener is insufficient: interrupt accepted and
        # upstream sockets before waiting, including a trickling response.
        with self._io_lock:
            for connection in (self._incoming, self._upstream_socket):
                if connection is not None:
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
        self.thread.join(timeout=10)
        self.server.server_close()
        self.token = ''
        if self.thread.is_alive():
            self.case.invalidated = True
            error = LocalCliExchangeError('owned CLI exchange shutdown is unconfirmed')
            error.child_shutdown_status = 'UNCONFIRMED'
            raise error
