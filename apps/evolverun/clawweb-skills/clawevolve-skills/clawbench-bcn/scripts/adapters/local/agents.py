"""Per-run OpenClaw team. Same initialization mechanism as Singlebox, no script dependency."""
import copy
import json
import os
from pathlib import Path
import secrets
import shutil
from urllib.parse import urlsplit
from core.profile_loader import validate_profile, profile_digest
from .processes import Processes, listening, wait_ready
from .bcn import BcnAdapter


class LocalTeam:
    def __init__(self, config, source, run_dir):
        self.config, self.run_dir = config, Path(run_dir)
        self.root = self.run_dir / 'openclaw'
        self.mapping, self.display_names, self.ports = {}, {}, []
        self.processes = Processes()
        self.digest = profile_digest(source)
        self.manifest = validate_profile(source)
        self.source = Path(source).resolve()
        if self.run_dir.resolve().is_relative_to(self.source):
            raise ValueError('Run directory must be outside Profile')
        self.env = os.environ.copy()
        for key in ('BCN_BOT_TOKEN', 'BOT_DATA_DIR', 'OPENCLAW_DATA_DIR', 'OPENCLAW_STATE_DIR',
                    'OPENCLAW_CONFIG_PATH', 'OPENCLAW_WORKSPACE_DIR', 'OPENCLAW_GATEWAY_TOKEN',
                    'BCSFUSE_FORCE_ENABLE_FUSION', 'BCN_BENCH_DEFAULT_JUDGE_KEY'):
            self.env.pop(key, None)
        self.env['PATH'] = str(config['repository'] / 'apps/bcs/target/debug') + os.pathsep + self.env.get('PATH', '')
        for i, bot in enumerate(self.manifest['bots']):
            alias = bot['profile']
            self.mapping[alias] = f'bench-{self.run_dir.name}-{alias}'
            self.display_names[alias] = f"{bot['name']} [{self.run_dir.name}]"
            port = config['port_start'] + i * 10
            if port > 65535 or listening(port):
                raise ValueError(f'Bot port unavailable: {port}')
            self.ports.append(port)

    def prepare(self, bot, port):
        alias = bot['profile']
        profile = self.root / 'profiles' / self.mapping[alias]
        workspace = self.root / 'workspaces' / self.mapping[alias]
        shutil.copytree(self.source / bot['source'], workspace)
        repo = self.config['repository']
        shared = workspace / 'skills/bcs-coordination'
        if shared.exists():
            raise ValueError('Profile must not override shared bcs-coordination in this POC')
        shutil.copytree(repo / 'apps/bcs/crates/tools/bcs-cli/bcs-coordination', shared)
        profile.mkdir(parents=True)
        model = self.config['model_data']
        # Only model fields are imported; never inherit user's personal tools/channels/workspace.
        defaults = copy.deepcopy(model.get('agents', {}).get('defaults', {}))
        defaults = {k: v for k, v in defaults.items() if k in ('model', 'models', 'contextTokens')}
        defaults.update(workspace=str(workspace), compaction={'mode': 'safeguard'}, maxConcurrent=4)
        endpoint = urlsplit(self.config['bcs_url'])
        csv = lambda value: [x.strip() for x in value.split(',') if x.strip()]
        data = {
            'models': copy.deepcopy(model['models']),
            'agents': {'defaults': defaults, 'list': [{'id': 'main'}]},
            'skills': {'allowBundled': []},
            'tools': {'profile': 'coding', 'alsoAllow': ['bcs_route', 'bcs_assign_task', 'bcs_task_status', 'bcs_cancel_task', 'bcs_task_complete']},
            'session': {'dmScope': 'per-channel-peer'},
            'channels': {'bcs': {'enabled': True, 'bcsUrl': f'ws://{endpoint.netloc}/ws/bot',
                'botId': self.display_names[alias], 'botName': self.display_names[alias],
                'capabilities': {'summary': bot.get('summary', ''), 'domains': csv(bot.get('domains', '')),
                    'skills': csv(bot.get('skills', '')), 'scopes': csv(self.manifest.get('scopes', 'production'))},
                'heartbeatIntervalMs': 60000, 'reconnectIntervalMs': 5000, 'connectionTimeoutMs': 30000}},
            'gateway': {'port': port, 'mode': 'local', 'bind': 'loopback',
                        'auth': {'mode': 'token', 'token': secrets.token_urlsafe(32)}},
            'plugins': {'load': {'paths': [str(self.config['plugin_path'])]},
                        'entries': {'openclaw-channel-bcn': {'enabled': True}}},
        }
        path = profile / 'openclaw.json'
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        path.chmod(0o600)
        env = self.env | {'BOT_DATA_DIR': str(profile), 'OPENCLAW_DATA_DIR': str(profile),
            'OPENCLAW_STATE_DIR': str(profile), 'OPENCLAW_CONFIG_PATH': str(path),
            'OPENCLAW_WORKSPACE_DIR': str(workspace), 'BCS_API_BASE_URL': self.config['bcs_url'],
            'BCS_IGNORE_CREDENTIALS': '1'}
        return profile, env

    def command(self, action):
        if action == 'stop':
            self.processes.stop()
            return
        if action != 'start':
            raise ValueError('Unknown team action')
        for bot, port in zip(self.manifest['bots'], self.ports):
            profile, env = self.prepare(bot, port)
            proc = self.processes.start([self.config['openclaw'], '--profile', self.mapping[bot['profile']],
                'gateway', 'run', '--port', str(port)], self.config['repository'], env,
                self.run_dir / f"agent-{bot['profile']}.log")
            def ready():
                try:
                    session = json.loads((profile / '.bcs/session.json').read_text())
                    return listening(port) and bool(session.get('bot_uuid') and session.get('token'))
                except (OSError, ValueError):
                    return False
            wait_ready(proc, ready, self.config['startup_timeout_seconds'], bot['profile'])
            # WebSocket connection only issues credentials. Onboard registers the
            # searchable Bot; Singlebox performs this distinct step before grouping.
            client = BcnAdapter(self.config['repository'] / 'apps/bcs/target/debug/bcs-cli',
                                self.config['bcs_url'], env, self.run_dir)
            client.call('onboard', '--name', self.display_names[bot['profile']],
                        '--summary', bot.get('summary', ''), '--domains', bot.get('domains', ''),
                        '--skills', bot.get('skills', ''), '--scopes', self.manifest.get('scopes', 'production'), as_json=False)
            client.call('visibility', 'set', '--value', 'public', as_json=False)

    def members(self):
        members = {alias: json.loads((self.root / 'profiles' / name / '.bcs/session.json').read_text())['bot_uuid']
                   for alias, name in self.mapping.items()}
        if len(set(members.values())) != len(members):
            raise ValueError('Duplicate BCN identities')
        return members

    def bcn_env(self):
        return self.env | {'BOT_DATA_DIR': str(self.root / 'profiles' / self.mapping[self.config['entry_member']])}

    def verify_stopped(self):
        if any(listening(port) for port in self.ports):
            raise RuntimeError('Owned Bot port remains active; inspect process metadata')
