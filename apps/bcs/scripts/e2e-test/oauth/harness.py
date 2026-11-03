"""Processes and browser jars; all auth state is created through HTTP login."""
import base64
from contextlib import contextmanager
import copy
import hashlib
import hmac
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import signal
import socket
import ssl
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, parse_qs
from urllib.request import (Request, build_opener, ProxyHandler, HTTPSHandler,
                            HTTPCookieProcessor, HTTPRedirectHandler)
import uuid
from provider_stub import LoopbackProxyHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def jwt(claims, key, header=None):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).rstrip(b'=').decode()
    data = encode(header or {'alg': 'HS256', 'typ': 'JWT'}) + '.' + encode(claims)
    return data + '.' + base64.urlsafe_b64encode(hmac.new(key.encode(), data.encode(), hashlib.sha256).digest()).rstrip(b'=').decode()


def claims(token):
    part = token.split('.')[1]
    return json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))


class Browser:
    def __init__(self, stub, checks):
        self.jar = CookieJar()
        self.checks = checks
        self.local = build_opener(ProxyHandler({}), HTTPCookieProcessor(self.jar), NoRedirect())
        self.provider = build_opener(LoopbackProxyHandler(stub.proxy_url),
            HTTPSHandler(context=ssl.create_default_context(cafile=str(stub.root / 'ca.pem'))), NoRedirect())

    def request(self, method, url, expected=200, headers=None, body=None, external=False):
        request = Request(url, method=method, headers=headers or {},
                          data=None if body is None else json.dumps(body).encode())
        try:
            response = (self.provider if external else self.local).open(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            raw, status, received = response.read(), response.code, response.headers
        allowed = (expected,) if isinstance(expected, int) else expected
        assert status in allowed, f'{method} {urlsplit(url).path}: expected {expected}, got {status}: {raw[:200]!r}'
        self.checks.append({'method': method, 'path': urlsplit(url).path, 'status': status})
        try:
            body = json.loads(raw)
        except ValueError:
            body = raw.decode()
        return body, received, status

    def cookie(self, name='bcs_session'):
        return next((cookie.value for cookie in self.jar if cookie.name == name), None)

    def urls(self, base, family='/openapi/v1/auth'):
        body, headers, _ = self.request('GET', base + family + '/url')
        assert headers['Cache-Control'] == 'no-store'
        assert self.cookie('bcs_oauth_login')
        return (body.get('data') or body)['providers']

    def authorize(self, url, user='alice'):
        url = urlsplit(url)
        query = parse_qs(url.query)
        query['login_hint'] = [user]
        location = url._replace(query=urlencode(query, doseq=True), fragment='').geturl()
        _, headers, _ = self.request('GET', location, 302, external=True)
        return headers['Location']

    def login(self, base, provider='github', user='alice', family='/openapi/v1/auth'):
        urls = self.urls(base, family)
        url = next(p['url'] for p in urls if p['name'] == provider)
        callback = self.authorize(url, user)
        _, headers, _ = self.request('GET', callback, 302)
        cookies = headers.get_all('Set-Cookie')
        assert len(cookies) == 2 and any('Max-Age=0' in c for c in cookies)
        assert headers['Cache-Control'] == 'no-store'
        assert self.cookie() and not self.cookie('bcs_oauth_login')
        return self.cookie()


class Environment:
    def __init__(self, binary, root, stub):
        self.binary, self.root, self.stub = binary, root, stub
        self.defaults = json.loads(subprocess.check_output([binary, '--print-config']))
        self.key, self.gateway_key = uuid.uuid4().hex, uuid.uuid4().hex
        self.checks = []
        self.number = 0

    def config(self, mode='mixed', chain=None):
        self.number += 1
        root = self.root / str(self.number)
        root.mkdir()
        base = f'http://127.0.0.1:{free_port()}'
        config = copy.deepcopy(self.defaults)
        config.update(bind='127.0.0.1', port=urlsplit(base).port, bots_base_dir=str(root / 'bots'),
            strict_container_validation=False, bcs_endpoint=base,
            database={'type': 'sqlite', 'sqlite': {'path': str(root / 'bcs.db')}},
            llm={'type': 'none'}, bcsfuse={'enabled': False},
            secret={'provider': 'env', 'providers': {'env': {'prefix': 'OAUTH_E2E_'}}},
            cors={'allowed_origins': [base]},
            auth={'chain': ['oauth_session'], 'oauth': {'jwt_secret': self.key, 'base_url': base,
                'cookie_secure': False, 'idle_timeout_minutes': 30,
                'providers': {name: {'client_id': 'e2e-client', 'client_secret': self.stub.secret}
                              for name in ('github', 'google', 'wechat')}}})
        config['auth']['oauth']['providers']['alipay'] = {'client_id': 'e2e-client',
            'private_key': (self.stub.root / 'server.key').read_text(), 'alipay_public_key': self.stub.public_key}
        config['logging'].update(console=True, default_level='warn')
        config['session_files']['backend'] = {'data_dir': str(root / 'files')}
        config['collaboration']['templates']['base_dir'] = str(root / 'templates')
        if mode != 'compat':
            auth = {'chain': chain or (['gateway'] if mode == 'gateway' else
                ['github', 'google', 'wechat', 'alipay', 'gateway']),
                'gateway': {'signing_key_secret': 'gateway'}}
            if mode != 'gateway':
                auth.update(public_base_url=base + '/openapi/v1/auth',
                    session_signing_key_secret='session', success_redirect_path='/',
                    trusted_browser_origins=[base])
                for name in ('github', 'google', 'wechat'):
                    auth[name] = {'client_id': 'e2e-client', 'client_secret_secret': 'client'}
                auth['alipay'] = {'client_id': 'e2e-client', 'private_key_secret': 'private',
                                  'alipay_public_key_secret': 'public'}
            config['api'] = {'auth': auth}
        return config, root, base

    def env(self):
        env = {key: value for key, value in os.environ.items() if key.lower() not in
               {'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'ssl_cert_file', 'ssl_cert_dir'}
               and not key.startswith(('OAUTH_E2E_', 'REAL_SERVER_ENV', 'ALIPAY_APP_ENV'))}
        # Auth callback query parameters must not enter the shared endpoint debug logger.
        env.update(SERVER_ENV='local', BCS_DEBUG='false', HTTPS_PROXY=self.stub.proxy_url, HTTP_PROXY=self.stub.proxy_url,
            NO_PROXY='127.0.0.1,localhost,::1', SSL_CERT_FILE=str(self.stub.root / 'ca.pem'),
            SSL_CERT_DIR=str(self.stub.root / 'empty-roots'),
            OAUTH_E2E_SESSION=self.key, OAUTH_E2E_GATEWAY=self.gateway_key,
            OAUTH_E2E_CLIENT=self.stub.secret,
            OAUTH_E2E_PRIVATE=(self.stub.root / 'server.key').read_text(),
            OAUTH_E2E_PUBLIC=self.stub.public_key,
            OAUTH_E2E_BCN_GROUP_SESSION_WS_JWT=uuid.uuid4().hex,
            AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE=self.gateway_key)
        return env

    def browser(self):
        return Browser(self.stub, self.checks)

    def gateway(self, user='gateway-user'):
        now = int(time.time())
        return jwt({'iss': 'gateway', 'aud': 'bcs', 'iat': now, 'exp': now + 300,
            'principals': [{'type': 'user', 'subject': {'id': user, 'username': user}}]},
            self.gateway_key, {'alg': 'HS256', 'typ': 'JWT', 'kid': 'bare'})

    @contextmanager
    def server(self, config, root, base, memory=False, *, process_env=None):
        path = root / 'bcs-config.json'
        path.write_text(json.dumps(config))
        with (root / 'server.log').open('w') as log:
            command = [self.binary, '-c', str(path)] + (['--memory'] if memory else [])
            process = subprocess.Popen(command, cwd=root,
                env=self.env() if process_env is None else process_env, stdout=log, stderr=log)
            succeeded = False
            try:
                browser = self.browser()
                for _ in range(150):
                    if process.poll() is not None:
                        raise AssertionError('BCS startup failed: ' + (root / 'server.log').read_text()[-1200:])
                    try:
                        browser.request('GET', base + '/health')
                        break
                    except (URLError, OSError):
                        time.sleep(0.1)
                else:
                    raise AssertionError('BCS startup timed out')
                yield base
                succeeded = True
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                        raise AssertionError('BCS did not shut down gracefully; profile incomplete')
                if succeeded:
                    assert process.returncode == 0, f'BCS exited with {process.returncode}'
