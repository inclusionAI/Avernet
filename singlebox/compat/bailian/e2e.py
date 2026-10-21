"""Real BCS -> published plugin/bridge -> engine -> Bailian -> BCS cases."""
import argparse
import json
import os
from pathlib import Path
import secrets
import re
import signal
import subprocess
import time
import tomllib
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

from evidence import marker_evidence, openclaw_evidence
from smoke import diagnostic_excerpt


class Harness:
    def __init__(self, root, engine, binaries, report):
        self.root, self.engine, self.binaries = root, engine, binaries
        self.report = report
        self.base = 'http://127.0.0.1:21000'
        self.ws = 'ws://127.0.0.1:21000/ws/bot'
        self.processes = []
        self.handles = []
        self.env = os.environ.copy()
        self.env.update(OPENCLAW_STATE_DIR=str(root / 'openclaw'),
                        OPENCLAW_CONFIG_PATH=str(root / 'openclaw/openclaw.json'),
                        DSH_HOME=str(root / 'dsh'), CLAUDE_CONFIG_DIR=str(root / 'claude'),
                        CODEX_HOME=str(root / 'codex'), BCS_IGNORE_CREDENTIALS='1',
                        BOT_DATA_DIR=str(root / 'openclaw'), DISABLE_AUTOUPDATER='1',
                        ANTHROPIC_AUTH_TOKEN=os.environ['BAILIAN_API_KEY'],
                        BRIDGE_CONFIG=str(root / 'bridge.toml'))
        self.env.pop('ANTHROPIC_API_KEY', None)
        self.model = json.loads((root / 'profile.json').read_text())['model']
        self.stage = 'setup'

    def api(self, path, body=None, token=None, method=None):
        headers = {'Content-Type': 'application/json', 'X-Mock-User-Id': '001',
                   'X-Mock-Nick-Name': 'admin'}
        if token:
            headers['X-BCS-Bot-Token'] = token
        request = Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                          headers=headers, method=method)
        with urlopen(request, timeout=25) as response:
            value = json.load(response)
        return value.get('data', value) if isinstance(value, dict) else value

    def start(self, command, name, cwd=None):
        handle = (self.root / (name + '.log')).open('a')
        self.handles.append(handle)
        process = subprocess.Popen(command, cwd=cwd or self.root / 'workspace', env=self.env,
                                   stdout=handle, stderr=handle, start_new_session=True)
        self.processes.append(process)
        return process

    def run(self, command, name):
        process = self.start(command, name)
        if process.wait(timeout=240):
            raise RuntimeError(name + '_failed')

    @staticmethod
    def stop(process):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()

    def wait(self, check, seconds=120):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                result = check()
                if result:
                    return result
            except (URLError, ConnectionError):
                pass
            time.sleep(2)
        raise TimeoutError(self.stage)

    def register(self, name):
        token = self.api('/register/token')['token']
        return self.api('/register?' + urlencode({'token': token, 'bot-name': name}), {})

    def server(self):
        import tomli_w
        self.stage = 'bcs_start'
        repo = Path(__file__).resolve().parents[3]
        directory = self.root / 'server'
        directory.mkdir()
        config = tomllib.loads((repo / 'apps/bcs/configs/bcs-config-local.toml').read_text())
        config['bcsfuse']['enabled'] = False
        config['collaboration']['templates']['base_dir'] = str(repo / 'apps/bcs/seeds/collaboration-templates')
        config['store_messages'] = True
        path = directory / 'bcs-config-local.toml'
        path.write_text(tomli_w.dumps(config))
        self.env.update(SERVER_ENV='local', BCS_DATA_DIR=str(directory), BCS_CONFIG_DIR=str(directory),
                        MOLTIS_BCS_CONFIG=str(path), BCS_AUTH_MOCK='1', BCS_MOCK_USER_ID='001',
                        BCS_MOCK_USER_NICK_NAME='admin', BCS_MOCK_USER_CHANNEL='mock',
                        AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE=secrets.token_hex(32))
        self.start([str(self.binaries / 'bcs'), '--config-dir', str(directory)], 'bcs', directory)
        self.wait(lambda: self.api('/me/ensure-human', {}))
        self.caller = self.register('CI caller')

    def connection(self):
        self.stage = 'engine_connection_setup'
        name = 'Bailian ' + self.engine
        if self.engine == 'openclaw':
            bot = self.register(name)
            session = self.root / 'openclaw/.bcs/session.json'
            session.parent.mkdir(parents=True, exist_ok=True)
            session.write_text(json.dumps({'bot_uuid': bot['bot_uuid'], 'token': bot['bot_token'],
                                           'bcs_url': self.ws}))
            config_path = self.root / 'openclaw/openclaw.json'
            config = json.loads(config_path.read_text())
            config['channels'] = {'bcs': {'enabled': True, 'bcsUrl': self.ws,
                                         'botId': bot['bot_uuid'], 'botName': name}}
            config['gateway'].update(port=18789, bind='loopback',
                                     auth={'mode': 'token', 'token': secrets.token_hex(24)})
            config_path.write_text(json.dumps(config))
            self.engine_command = ['openclaw', 'gateway', 'run']
        elif self.engine == 'deepseek-harness':
            import yaml
            self.env['BCN_ONBOARDING_TOKEN'] = self.api('/register/token')['token']
            patch = self.root / 'dsh/profiles/web/cordis.patch.yml'
            rows = yaml.safe_load(patch.read_text()) or []
            rows = [row for row in rows if row.get('id') not in
                    ('llm-pi-ai', 'agent-default-model', 'deepseek-harness-channel-bcn')]
            rows += json.loads((self.root / 'dsh/profiles/headless/cordis.patch.yml').read_text())
            rows.append({'id': 'deepseek-harness-channel-bcn',
                         'name': '@avernet-plugin/deepseek-harness-channel-bcn',
                         'config': {'enabled': True, 'endpoint': self.base + '/', 'botName': name,
                                    'onboardingTokenRef': 'BCN_ONBOARDING_TOKEN',
                                    'botSessionRef': 'BCN_BOT_SESSION'}})
            patch.write_text(yaml.safe_dump(rows, sort_keys=False))
            self.engine_command = ['dsh', '--profile', 'web']
        else:
            provider = self.api('/providers', {'name': name, 'auth': {'mode': 'static_bearer'},
                                               'protocol_version': '2.0',
                                               'coordination': {'mode': 'native_mcp'}})
            self.env['BRIDGE_REGISTER_TOKEN'] = self.api('/register/token?' + urlencode(
                {'provider_id': provider['provider_id']}))['token']
            self.run([str(self.binaries / 'bcs-bridge'), 'register', '--bot-name', name,
                      '--api-url', self.base, '--upstream-url', self.ws,
                      '--engine', self.engine, '--cwd', str(self.root / 'workspace'),
                      '--model', self.model], 'bridge-register')
            self.env.pop('BRIDGE_REGISTER_TOKEN')
            config = tomllib.loads((self.root / 'bridge.toml').read_text())
            bot = {'bot_uuid': config['bot'][0]['bot_id']}
            if self.engine == 'claude-code':
                path = self.root / 'claude/settings.json'
                settings = json.loads(path.read_text())
                settings['permissions'] = {'allow': ['Read']}
                path.write_text(json.dumps(settings))
            self.engine_command = [str(self.binaries / 'bcs-bridge'), 'start']
        self.engine_process = self.start(self.engine_command, 'engine')
        self.stage = 'plugin_connected'
        def online():
            rows = self.api('/bots/search?' + urlencode({'q': name, 'limit': 100}))['items']
            return next((item for item in rows if item['name'] == name and item['is_online']), None)
        connected = self.wait(online, 180)
        self.bot_id = connected['bot_uuid']
        self.report['bot_id'] = self.bot_id
        self.report['cases'].append({'name': 'registration_and_connection', 'status': 'PASS'})

    def chat(self, name, prompt, expected=None, session=None, forbidden=None):
        self.stage = name
        body = {'message': prompt, 'timeout_ms': 180000, 'response_mode': 'after-last-tool-call'}
        if session:
            body['session_id'] = session
        accepted = self.api('/bots/' + quote(self.bot_id, safe='') + '/chat-async', body,
                            self.caller['bot_token'])
        run_id = accepted['run_id']
        result = self.wait(lambda: self.terminal(run_id), 210)
        content = result.get('response', {}).get('content', '')
        row = {'name': name, 'run_id': run_id, 'session_id': accepted['session_id'],
               'terminal_state': result['state'], 'status': 'FAIL',
               'content_truncated': result.get('content_truncated', False)}
        if expected:
            row['content_evidence'] = marker_evidence(content, expected)
            if self.engine == 'openclaw':
                row['openclaw_evidence'] = openclaw_evidence(self.root, expected, content)
        self.report['cases'].append(row)
        if (result['state'] != 'completed' or result.get('content_truncated') or
                (expected and expected not in content) or (forbidden and forbidden in content) or not content):
            raise RuntimeError(name + '_assertion_failed')
        row['status'] = 'PASS'
        return accepted['session_id']

    def terminal(self, run_id):
        result = self.api('/chat/runs/' + quote(run_id, safe=''), token=self.caller['bot_token'])
        return result if result.get('is_terminal') else None

    def cases(self):
        marker = 'BCS_' + secrets.token_hex(16)
        path = self.root / 'workspace/probe.txt'
        path.write_text(marker + '\n')
        session = self.chat('file_tool_roundtrip',
                            'Use your file-reading tool to read probe.txt in the current workspace. '
                            'Reply with only its complete content.', marker)
        path.unlink()
        self.chat('same_session_memory', 'Repeat the complete file content you just read. '
                  'The file has been removed; use this conversation history.', marker, session)
        self.chat('new_session_isolation', 'What was the content of probe.txt in this conversation? '
                  'Do not use tools. If you have not read it in this conversation, answer UNKNOWN.',
                  expected='UNKNOWN', forbidden=marker)
        self.stage = 'connection_restart'
        self.stop(self.engine_process)
        self.wait(lambda: not any(item['bot_uuid'] == self.bot_id and item['is_online']
                  for item in self.api('/bots/search?limit=100')['items']), 60)
        self.engine_process = self.start(self.engine_command, 'engine')
        self.wait(lambda: any(item['bot_uuid'] == self.bot_id and item['is_online']
                  for item in self.api('/bots/search?limit=100')['items']), 120)
        self.chat('restart_session_recovery', 'Repeat the complete file content from earlier in this '
                  'conversation without using tools.', marker, session)

    def close(self):
        for process in reversed(self.processes):
            self.stop(process)
        for handle in self.handles:
            handle.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--binaries', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = {'scope': 'bcs_plugin_model_e2e', 'engine': args.engine, 'status': 'UNKNOWN',
              'commit': os.environ.get('GITHUB_SHA', ''), 'cases': []}
    harness = None
    try:
        if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
            raise RuntimeError('github_hosted_runner_required')
        harness = Harness(args.profile.resolve(), args.engine, args.binaries.resolve(), report)
        harness.server()
        harness.connection()
        harness.cases()
        report['status'] = 'PASS'
    except Exception as error:
        report.update(status='FAIL', stage=harness.stage if harness else 'runner_guard',
                      error_type=type(error).__name__)
        if isinstance(error, HTTPError):
            report['http_status'] = error.code
        if harness:
            for path in harness.root.glob('*.log'):
                print(json.dumps({'log': path.name, 'diagnostic': diagnostic_excerpt(
                    re.sub(r'[A-Za-z0-9_+/=-]{24,}', '[REDACTED]', path.read_text(errors='replace')), os.environ['BAILIAN_API_KEY'])}))
    finally:
        if harness:
            harness.close()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
