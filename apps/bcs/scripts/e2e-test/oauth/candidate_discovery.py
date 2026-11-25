"""BCS-only discovery/approval via real OAuth cookies and the production HTTP API.

Only the external recommendation provider is a loopback stub. Bot registration,
visibility, authorization and approved edges all use real BCS HTTP operations.
This runs in the existing instrumented OAuth companion before coverage merge.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from urllib.parse import quote, urlencode

PUBLIC = '/openapi/v1/collaboration'
INTERNAL = '/api/v1/collaboration'


@contextmanager
def recommendations():
    state = {'ids': [], 'calls': [], 'mode': 'semantic', 'batches': [], 'errors': []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, body):
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or b'{}')
            if self.path == '/api/v1/recommend':
                state['calls'].append(body)
                if state['mode'] == 'failure':
                    return self.reply(503, {'error': 'recommendation unavailable'})
                ids = [] if state['mode'] == 'empty' else state['ids']
                return self.reply(200, {'recommendations': [
                    {'worker_id': bot, 'profile_key': f'{bot}:default',
                     'score': 0.99 - i * 0.01, 'short_profile': 'candidate profile'}
                    for i, bot in enumerate(ids)]})
            if self.path == '/v1/workers/batch':
                state['batches'].append(body['worker_ids'])
                return self.reply(200, {'success': True, 'data': {
                    bot: {'name': bot, 'profile_tags': {'specialty': 'planning'}}
                    for bot in body['worker_ids']}, 'not_found_ids': []})
            if self.path.startswith('/v1/workers/') and self.path.endswith('/sync'):
                return self.reply(200, {'worker_id': self.path.split('/')[3],
                    'created': True, 'profile_activated': True})
            state['errors'].append(self.path)
            self.reply(404, {})

        def do_PUT(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            self.reply(200, {})

        def do_GET(self):
            self.reply(200, {'status': 'ok'})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def request(browser, base, method, path, body=None, expected=200):
    result, _, _ = browser.request(method, base + path, expected,
        {'Origin': base, 'Content-Type': 'application/json'}, body)
    return result['data']


def register(browser, base, name, visibility, user_visibility):
    token = request(browser, base, 'GET', PUBLIC + '/register/token')['token']
    bot = request(browser, base, 'POST', PUBLIC + '/register?' +
        urlencode({'token': token, 'bot_name': name}), expected=201)
    bot_id = bot['bot_uuid']
    request(browser, base, 'PATCH', PUBLIC + '/bots/' + quote(bot_id, safe=''), {
        'visibility': visibility, 'user_visibility': user_visibility,
        'descriptor': {'summary': 'Planning specialist'},
    })
    return bot_id


def search(browser, base, actor, query='Planning', purpose='discovery', expected=200):
    return request(browser, base, 'GET', INTERNAL + '/bots/' + quote(actor, safe='') +
        '/candidates/search?' + urlencode({'q': query, 'purpose': purpose}), expected=expected)


def assert_hits(result, mode, ids, friends=()):
    assert result['search_mode'] == mode, result
    hits = result['items']
    assert [hit['bot']['bot_id'] for hit in hits] == ids, result
    for hit in hits:
        assert hit['is_friend'] == (hit['bot']['bot_id'] in friends), hit
        if mode == 'semantic':
            assert isinstance(hit['score'], (int, float)), hit
            assert hit['short_profile'] == 'candidate profile', hit
            assert hit['tags'] == {'specialty': 'planning'}, hit
        else:
            assert 'score' not in hit and 'short_profile' not in hit, hit


def candidate_discovery(env):
    # Pure OAuth: no Gateway, Backend, fake Principal or SQL fixture writes.
    with recommendations() as (url, provider):
        config, root, base = env.config(chain=['github'])
        config['bcsfuse'].update(enabled=True, url=url, fusion_timeout_ms=2000,
                                sync_timeout_ms=2000, sync_retry_base_ms=1)
        with env.server(config, root, base):
            alice, bob = env.browser(), env.browser()
            alice.login(base, user='candidate-alice')
            bob.login(base, user='candidate-bob')
            user_id = request(alice, base, 'GET', '/openapi/v1/auth/user')['user_id']
            mine = request(alice, base, 'GET', PUBLIC + '/bots/mine')
            human = next(bot['bot_id'] for bot in mine['items'] if bot['kind'] == 'human')
            request(bob, base, 'GET', PUBLIC + '/bots/mine')
            acting = register(alice, base, 'Acting', 'private', 'private')
            human_only = register(bob, base, 'Planning Human', 'private', 'public')
            bot_only = register(bob, base, 'Planning Bot', 'public', 'private')
            protected = register(bob, base, 'Planning Protected', 'protected', 'protected')
            provider['ids'] = [human_only, bot_only, protected, human_only, acting]

            before = len(provider['calls'])
            assert_hits(search(alice, base, human, '  '), 'empty_query', [])
            assert len(provider['calls']) == before, 'empty search reached provider'
            search(alice, base, human_only, expected=403)
            env.browser().request('GET', base + INTERNAL + f'/bots/{human}/candidates/search?q=Planning', 401)
            assert len(provider['calls']) == before, 'unauthorized search reached provider'

            assert_hits(search(alice, base, human), 'semantic', [human_only, protected])
            assert provider['calls'][-1]['filters']['availability'] == ['public', 'protected', 'private']
            assert len(provider['calls']) == before + 1, 'unexpected top-up recall'
            assert_hits(search(alice, base, acting), 'semantic', [bot_only, protected])
            assert 'filters' not in provider['calls'][-1], 'Bot discovery changed recall scope'
            assert_hits(search(alice, base, human, purpose='collaboration'), 'semantic', [human_only])

            # A pending Human request does not confer friendship. The actual
            # target manager reads the inbox and approves, then both list and
            # semantic search independently read the committed Connect edge.
            created = request(alice, base, 'POST', PUBLIC + '/friend-connections/requests', {
                'from_actor': {'type': 'human', 'id': user_id},
                'to_actor': {'type': 'bot', 'id': protected},
            }, 201)
            assert created['status'] == 'pending', created
            assert_hits(search(alice, base, human, purpose='collaboration'), 'semantic', [human_only])
            inbox = request(bob, base, 'GET', PUBLIC + '/friend-connections/requests?' + urlencode({
                'actor_type': 'bot', 'actor_id': protected, 'direction': 'received', 'status': 'pending'}))
            request_id = created['request_ids'][0]
            assert request_id in [item['request_id'] for item in inbox['items']], inbox
            request(bob, base, 'POST', PUBLIC + f'/friend-connections/requests/{request_id}/accept', {})
            assert_hits(search(alice, base, human, purpose='collaboration'), 'semantic',
                        [human_only, protected], [protected])
            listed = request(alice, base, 'GET', PUBLIC + f'/bots/{human}/candidates?' +
                urlencode({'name': 'Planning Protected', 'purpose': 'collaboration'}))
            assert listed['total'] == 1 and listed['items'][0]['is_friend'], listed

            # Approved private friends must remain recalled for collaboration.
            request(bob, base, 'PATCH', PUBLIC + f'/bots/{protected}', {'user_visibility': 'private'})
            assert_hits(search(alice, base, human, purpose='collaboration'), 'semantic',
                        [human_only, protected], [protected])
            assert_hits(search(alice, base, human), 'semantic', [human_only])
            assert_hits(search(alice, base, acting, purpose='collaboration'), 'semantic', [bot_only])
            assert provider['calls'][-1]['filters']['availability'] == ['public', 'protected', 'private']

            # Genuine provider failure, empty recall and fully filtered recall
            # all fall back through the real viewer-scoped SQL name query.
            for mode in ('failure', 'empty', 'semantic'):
                provider['mode'] = mode
                provider['ids'] = [bot_only]  # invisible to Human
                before = len(provider['calls'])
                assert_hits(search(alice, base, human, 'Planning Human'),
                            'name_fallback', [human_only])
                assert len(provider['calls']) == before + 1
            provider['mode'] = 'empty'
            assert_hits(search(alice, base, human, 'NoSuchCandidate'), 'name_fallback', [])
            assert all(call['topK'] == 20 for call in provider['calls'])
            assert not provider['errors'], provider['errors']

    # No provider is a supported deployment, not a swallowed connection error.
    config, root, base = env.config(chain=['github'])
    with env.server(config, root, base):
        browser = env.browser()
        browser.login(base, user='standalone-candidate')
        mine = request(browser, base, 'GET', PUBLIC + '/bots/mine')
        human = next(bot['bot_id'] for bot in mine['items'] if bot['kind'] == 'human')
        bot = register(browser, base, 'Standalone Planning', 'private', 'public')
        assert_hits(search(browser, base, human, 'Standalone'), 'name_fallback', [bot])
