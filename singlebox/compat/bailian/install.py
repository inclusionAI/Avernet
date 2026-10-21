"""Resolve latest once, install exact versions without model credentials."""
import argparse
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('Installation requires a GitHub-hosted runner')
    packages = {}
    for kind in ('ENGINE', 'PLUGIN'):
        package = os.environ.get(kind + '_PACKAGE')
        if package:
            version = subprocess.check_output(['npm', 'view', package + '@latest', 'version'], text=True).strip()
            packages[kind.lower()] = {'package': package, 'version': version}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({'commit': os.environ['GITHUB_SHA'], **packages}, indent=2))
    engine = packages['engine']
    subprocess.run(['npm', 'install', '--global', engine['package'] + '@' + engine['version']], check=True)
    if 'plugin' not in packages:
        return
    plugin = packages['plugin']
    spec = plugin['package'] + '@' + plugin['version']
    env = os.environ.copy()
    if os.environ['ENGINE'] == 'openclaw':
        # Install into a separate profile: production profile contains an env-key
        # reference and installation must never receive the model API key.
        env['OPENCLAW_STATE_DIR'] = str(args.profile / 'plugin-install')
        env['OPENCLAW_CONFIG_PATH'] = str(args.profile / 'plugin-install/openclaw.json')
        # The user selected this Avernet-owned npm plugin explicitly; latest
        # OpenClaw requires noninteractive acknowledgement for non-ClawHub sources.
        subprocess.run(['openclaw', 'plugins', 'install', '--force', 'npm:' + spec], env=env, check=True)
        plugin_dir = args.profile / 'plugin-install/extensions/openclaw-channel-bcn'
        if not plugin_dir.is_dir():
            raise RuntimeError('Published plugin installation directory missing')
        path = args.profile / 'openclaw/openclaw.json'
        config = json.loads(path.read_text())
        config['plugins'] = {'allow': ['openclaw-channel-bcn'], 'load': {'paths': [str(plugin_dir)]},
                             'entries': {'openclaw-channel-bcn': {'enabled': True}}}
        path.write_text(json.dumps(config))
    else:
        env['DSH_HOME'] = str(args.profile / 'dsh')
        subprocess.run(['dsh', 'plugin', '--profile', 'web', 'add', spec], env=env, check=True)


if __name__ == '__main__':
    main()
