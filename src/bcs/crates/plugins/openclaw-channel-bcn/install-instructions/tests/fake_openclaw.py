#!/usr/bin/env python3
"""Executable boundary double; the launcher, HTTP and filesystem stay real."""
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path

state = Path(os.environ['OPENCLAW_STATE_DIR'])
config = json.loads(Path(os.environ['OPENCLAW_CONFIG_PATH']).read_text())
args = sys.argv[1:]
with open(os.environ['FAKE_CALLS'], 'a') as out:
    out.write(json.dumps({'args': args, 'state': str(state), 'config': config,
                          'bot_data': os.environ.get('BOT_DATA_DIR'),
                          'ignore_credentials': os.environ.get('BCS_IGNORE_CREDENTIALS'),
                          'has_registration_proof': 'BCS_REGISTER_TOKEN' in os.environ,
                          'pi_agent_dir': os.environ.get('PI_CODING_AGENT_DIR'),
                          'openclaw_agent_dir': os.environ.get('OPENCLAW_AGENT_DIR')}) + '\n')
if args[:2] == ['plugins', 'list']:
    section = config.get('channels', {}).get('bcs', {})
    if (os.environ.get('FAKE_CONFIG_CAS') and (state / 'installed').exists()
            and not any(key != 'enabled' for key in section)):
        config['channels'] = {'bcs': {'enabled': True, 'heartbeatIntervalMs': 60000}}
        Path(os.environ['OPENCLAW_CONFIG_PATH']).write_text(json.dumps(config))
        print('[BCS setup-entry] Auto-configured channels.bcs')
    plugins = [{'id': 'openclaw-channel-bcn', 'status': 'loaded'}] if (state / 'installed').exists() else []
    print(json.dumps({'plugins': plugins}))
elif args[:2] == ['plugins', 'install']:
    if os.environ.get('FAKE_FAIL_INSTALL'):
        print('simulated secret must not leak', file=sys.stderr)
        sys.exit(1)
    if os.environ.get('FAKE_CONFIG_CAS'):
        config['channels'] = {'bcs': {'enabled': True, 'heartbeatIntervalMs': 60000}}
        Path(os.environ['OPENCLAW_CONFIG_PATH']).write_text(json.dumps(config))
        if not os.environ.get('FAKE_CONFIG_CAS_MISSING'):
            (state / 'installed').touch()
        print('[openclaw] Reason: config changed since last load', file=sys.stderr)
        sys.exit(1)
    (state / 'installed').touch()
elif args[:2] == ['plugins', 'enable']:
    if os.environ.get('FAKE_FAIL_ENABLE'):
        print('enable failed', file=sys.stderr)
        sys.exit(1)
    config.setdefault('plugins', {}).setdefault('entries', {})['openclaw-channel-bcn'] = {'enabled': True}
    Path(os.environ['OPENCLAW_CONFIG_PATH']).write_text(json.dumps(config))
    (state / 'enabled').touch()
elif args[:2] == ['gateway', 'run']:
    exit_selector = os.environ.get('FAKE_EXIT_GATEWAY', '')
    if exit_selector and exit_selector in state.name:
        sys.exit(7)
    sock = socket.socket()
    sock.bind(('127.0.0.1', config['gateway']['port']))
    sock.listen()
    (state / 'gateway.pid').write_text(str(os.getpid()))
    session_path = state / '.bcs/session.json'
    session = json.loads(session_path.read_text())
    session['token'] = 'reconnected-' + session['bot_uuid']
    session_path.write_text(json.dumps(session))
    def stop(*_):
        (state / 'stopped').touch()
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while True:
        time.sleep(0.1)
elif args[:2] == ['channels', 'status']:
    connected = (state / 'gateway.pid').exists() and not os.environ.get('FAKE_OFFLINE')
    print(json.dumps({'channelAccounts': {'bcs': [{'accountId': 'default',
          'probe': {'connected': connected, 'sessionToken':
              None if os.environ.get('FAKE_UNAUTHENTICATED') else 'must-not-print-probe-token'}}]}}))
else:
    print('unexpected command: ' + repr(args), file=sys.stderr)
    sys.exit(2)
