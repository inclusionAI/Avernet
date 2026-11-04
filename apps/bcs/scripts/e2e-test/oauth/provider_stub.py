"""Loopback-only, non-forwarding HTTPS OAuth provider simulator.

Real provider clients retain their compiled URLs and TLS verification. A fresh
CA is trusted ONLY by the child process/browser. Unknown CONNECT destinations
are rejected; this server never connects to the Internet or logs credentials.
"""
import base64
from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import ssl
import subprocess
import threading
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import ProxyHandler
import uuid

HOSTS = {
    'github.com': 'github', 'api.github.com': 'github',
    'accounts.google.com': 'google', 'oauth2.googleapis.com': 'google',
    'openidconnect.googleapis.com': 'google', 'open.weixin.qq.com': 'wechat',
    'api.weixin.qq.com': 'wechat', 'openauth.alipay.com': 'alipay',
    'openapi.alipay.com': 'alipay',
}
ROUTES = {
    ('github.com', '/login/oauth/authorize', 'GET'): 'authorize',
    ('github.com', '/login/oauth/access_token', 'POST'): 'token',
    ('api.github.com', '/user', 'GET'): 'userinfo',
    ('accounts.google.com', '/o/oauth2/v2/auth', 'GET'): 'authorize',
    ('oauth2.googleapis.com', '/token', 'POST'): 'token',
    ('openidconnect.googleapis.com', '/v1/userinfo', 'GET'): 'userinfo',
    ('open.weixin.qq.com', '/connect/qrconnect', 'GET'): 'authorize',
    ('api.weixin.qq.com', '/sns/oauth2/access_token', 'GET'): 'token',
    ('api.weixin.qq.com', '/sns/userinfo', 'GET'): 'userinfo',
    ('openauth.alipay.com', '/oauth2/publicAppAuthorize.htm', 'GET'): 'authorize',
    ('openapi.alipay.com', '/gateway.do', 'POST'): 'alipay',
}



class LoopbackProxyHandler(ProxyHandler):
    """Never let a developer's NO_PROXY=* bypass the provider simulator."""
    def __init__(self, proxy_url):
        parsed = urlsplit(proxy_url)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1':
            raise ValueError('OAuth simulator proxy must be loopback HTTP')
        super().__init__({'https': proxy_url})

    def proxy_open(self, request, proxy, scheme):
        if request.type != 'https':
            raise ValueError('provider requests must use verified HTTPS')
        request.set_proxy(urlsplit(proxy).netloc, 'http')
        return None


def openssl(*args, **kwargs):
    return subprocess.run(['openssl', *args], check=True, capture_output=True, **kwargs).stdout


class State:
    def __init__(self, root):
        self.root = root
        (root / "empty-roots").mkdir()
        self.codes, self.tokens = {}, {}
        self.calls = Counter()
        self.lock = threading.Lock()
        self.secret = uuid.uuid4().hex
        self.errors = []
        (root / 'ca.conf').write_text('[req]\ndistinguished_name=dn\nx509_extensions=ca\n[dn]\n[ca]\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n')
        openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                '-subj', '/CN=BCS OAuth E2E ephemeral CA', '-keyout', str(root / 'ca.key'),
                '-out', str(root / 'ca.pem'), '-config', str(root / 'ca.conf'))
        openssl('req', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=github.com',
                '-keyout', str(root / 'server.key'), '-out', str(root / 'server.csr'))
        (root / 'extensions').write_text('subjectAltName=' + ','.join('DNS:' + h for h in HOSTS)
            + '\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n')
        openssl('x509', '-req', '-in', str(root / 'server.csr'), '-CA', str(root / 'ca.pem'),
                '-CAkey', str(root / 'ca.key'), '-CAcreateserial', '-days', '1',
                '-extfile', str(root / 'extensions'), '-out', str(root / 'server.pem'))
        self.public_key = openssl('pkey', '-in', str(root / 'server.key'), '-pubout').decode()
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(root / 'server.pem', root / 'server.key')

    def count(self, action):
        with self.lock:
            return self.calls[action]

    def handle(self, host, path, method, params, headers):
        provider = HOSTS[host]
        route = ROUTES.get((host, path, method))
        if route == 'alipay':
            route = {'alipay.system.oauth.token': 'token',
                     'alipay.user.info.share': 'userinfo'}.get(params.get('method'))
        if route is None:
            return 404, {'error': 'unknown_provider_endpoint'}, {}
        if route == 'authorize':
            callback = params['redirect_uri']
            assert urlsplit(callback).hostname == '127.0.0.1', 'non-local callback rejected'
            code = uuid.uuid4().hex
            with self.lock:
                self.calls['authorize'] += 1
                self.codes[code] = (provider, params.get('login_hint', 'alice'), callback)
            return 302, {}, {'Location': callback + '?' + urlencode({
                'auth_code' if provider == 'alipay' else 'code': code, 'state': params['state']})}
        if route == 'token':
            with self.lock:
                self.calls['exchange'] += 1
                record = self.codes.pop(params.get('code', ''), None)
            if not record or record[0] != provider:
                return 400, {'error': 'invalid_grant'}, {}
            _, user, callback = record
            valid = params.get('grant_type') == 'authorization_code'
            if provider in {'github', 'google'}:
                valid &= params.get('client_secret') == self.secret and params.get('redirect_uri') == callback
            if provider == 'wechat':
                valid &= params.get('secret') == self.secret
            if not valid:
                return 400, {'error': 'invalid_grant'}, {}
            if user == 'token-error':
                return 502, {'error': 'provider_unavailable'}, {}
            token = uuid.uuid4().hex
            with self.lock:
                self.tokens[token] = (provider, user)
            body = {'access_token': token, 'token_type': 'Bearer', 'expires_in': 3600,
                    'openid': user, 'open_id': user, 'user_id': user}
            return self.reply(provider, 'alipay_system_oauth_token_response', body)
        token = params.get('access_token') or params.get('auth_token') or headers.get('Authorization', '').removeprefix('Bearer ')
        with self.lock:
            self.calls['userinfo'] += 1
            record = self.tokens.get(token)
        if not record or record[0] != provider:
            return 401, {'error': 'invalid_token'}, {}
        _, user = record
        if user == 'info-error':
            return 502, {'error': 'userinfo_unavailable'}, {}
        name = None if user == 'nameless' else 'OAuth ' + user
        avatar = f'/avatars/{provider}/{user}.png'
        body = {'id': int.from_bytes(user.encode(), 'little') % 100000000,
                'sub': user, 'login': user, 'name': name, 'email': None,
                'picture': avatar, 'avatar_url': avatar, 'openid': user,
                'nickname': name, 'headimgurl': avatar, 'open_id': user,
                'nick_name': name, 'avatar': avatar}
        return self.reply(provider, 'alipay_user_info_share_response', body)

    def reply(self, provider, key, body):
        if provider != 'alipay':
            return 200, body, {}
        body['code'] = '10000'
        raw = json.dumps(body, separators=(',', ':')).encode()
        signature = openssl('dgst', '-sha256', '-sign', str(self.root / 'server.key'), input=raw)
        return 200, {key: body, 'sign': base64.b64encode(signature).decode()}, {}


class OAuthHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def respond(self):
        try:
            url = urlsplit(self.path)
            params = parse_qs(url.query)
            if self.command == 'POST':
                params.update(parse_qs(self.rfile.read(int(self.headers.get('Content-Length', 0))).decode()))
            params = {key: values[0] for key, values in params.items()}
            host = self.headers['Host'].split(':')[0]
            status, body, headers = self.server.state.handle(host, url.path, self.command, params, self.headers)
            data = json.dumps(body, separators=(',', ':')).encode()
        except Exception as error:
            # Category only, never code/token/body in errors or logs.
            self.server.state.errors.append(type(error).__name__)
            status, data, headers = 500, b'{"error":"stub_contract_failure"}', {}
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(data)


class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_CONNECT(self):
        if self.path not in {host + ':443' for host in HOSTS}:
            self.send_error(403, 'unknown destination')
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.flush()
        self.close_connection = True
        try:
            with self.server.state.context.wrap_socket(self.connection, server_side=True) as tls:
                OAuthHandler(tls, self.client_address, self.server)
        except (ssl.SSLError, ConnectionError, TimeoutError):
            # Untrusted-client negative controls can reject the TLS handshake.
            pass


@contextmanager
def provider_stub(root):
    state = State(root)
    server = ThreadingHTTPServer(('127.0.0.1', 0), ProxyHandler)
    server.daemon_threads = True
    server.state = state
    state.proxy_url = f'http://127.0.0.1:{server.server_port}'
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
