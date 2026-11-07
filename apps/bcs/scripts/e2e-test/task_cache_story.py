#!/usr/bin/env python3
"""Isolated real BCS process with a shared MCP-producer cache fixture."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import urllib.request
import uuid
from caller_execution_story import free_port


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    args = parser.parse_args()
    binary = str(Path(args.binary).resolve())
    probe = Path(__file__).resolve().parent.parent / 'task_handle_runtime_probe.cjs'
    with tempfile.TemporaryDirectory(prefix='bcs-task-cache-') as directory:
        root = Path(directory)
        port, fixture_port = free_port(), free_port()
        config = json.loads(subprocess.check_output([binary, '--print-config']))
        config.update({'bind': '127.0.0.1', 'port': port, 'store_messages': True,
                       'strict_container_validation': False, 'bcs_endpoint': f'http://127.0.0.1:{port}',
                       'auth': {'chain': ['local'], 'allow_mock_headers': True, 'mock_user_id': '000000'},
                       'cache': {'type': 'task-cache-e2e'},
                       'database': {'type': 'sqlite', 'sqlite': {'path': str(root / 'bcs.db')}},
                       'llm': {'type': 'none'}, 'bcsfuse': {'enabled': False}})
        config['secret'] = {'provider': 'env', 'providers': {'env': {'prefix': 'BCS_SECRET_'}}}
        config['bots_base_dir'] = str(root / 'bots')
        config['session_files']['backend'] = {'data_dir': str(root / 'files')}
        config['collaboration']['templates']['base_dir'] = str(root / 'templates')
        config_path = root / 'bcs-config-local.json'
        config_path.write_text(json.dumps(config))
        env = dict(os.environ, SERVER_ENV='local', BCS_SECRET_BCN_GROUP_SESSION_WS_JWT=uuid.uuid4().hex, BCS_TASK_CACHE_DB=str(root / 'bcs.db'),
                   BCS_TASK_CACHE_FIXTURE=f'http://127.0.0.1:{fixture_port}',
                   AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE='avernet-dev-signing-key-NOT-FOR-PROD')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with (root / 'server.log').open('w') as log:
            process = subprocess.Popen([binary, '-c', str(config_path), '--cache-fixture-port', str(fixture_port)], cwd=root, env=env, stdout=log, stderr=log)
            try:
                for _ in range(150):
                    if process.poll() is not None:
                        raise RuntimeError('BCS exited during startup')
                    try:
                        opener.open(f'http://127.0.0.1:{port}/health', timeout=1).close()
                        break
                    except OSError:
                        time.sleep(0.2)
                else:
                    raise RuntimeError('BCS startup timed out')
                subprocess.run(['node', str(probe), f'http://127.0.0.1:{port}'], env=env, check=True, timeout=180)
            except Exception:
                print((root / 'server.log').read_text()[-6000:])
                raise
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    process.wait(timeout=30)


if __name__ == '__main__':
    main()
