"""Resolve selected versions once, install without model credentials."""
import argparse
import json
import os
import re
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
            selection = os.environ.get('ENGINE_VERSION_SPEC', 'latest') if kind == 'ENGINE' else 'latest'
            version = subprocess.check_output(['npm', 'view', package + '@' + selection, 'version'], text=True).strip()
            packages[kind.lower()] = {'package': package, 'version': version, 'selection': selection}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({'commit': os.environ['GITHUB_SHA'], **packages}, indent=2))
    engine = packages['engine']
    subprocess.run(['npm', 'install', '--global', engine['package'] + '@' + engine['version']], check=True)
    if 'plugin' not in packages:
        return
    plugin = packages['plugin']
    spec = plugin['package'] + '@' + plugin['version']
    def install_plugin(command, env):
        result = subprocess.run(command, env=env, text=True, capture_output=True)
        output = result.stdout + result.stderr
        print(output, flush=True)  # Installation has no runtime credentials.
        status = {'status': 'PASS' if result.returncode == 0 else 'FAIL',
                  'reason': 'installed' if result.returncode == 0 else 'plugin_install_failed'}
        if 'is incompatible with dsh' in output:
            status['reason'] = 'published_plugin_incompatible_with_latest_engine'
        args.report.with_name('install.json').write_text(json.dumps(status, indent=2))
        result.check_returncode()

    env = os.environ.copy()
    if os.environ['ENGINE'] == 'openclaw':
        # Install in the runtime profile so OpenClaw owns its version-specific
        # npm layout and capability-consent records. This placeholder only allows
        # config parsing; installation never receives the real model credential.
        env['OPENCLAW_STATE_DIR'] = str(args.profile / 'openclaw')
        env['OPENCLAW_CONFIG_PATH'] = str(args.profile / 'openclaw/openclaw.json')
        env['BAILIAN_API_KEY'] = 'ci-install-placeholder'
        # 2026.5.12 predates capability-consent flags and accepts plain npm specs.
        command = ['openclaw', 'plugins', 'install', '--force']
        if engine['version'] != '2026.5.12':
            command += ['--accept-capabilities', 'npm:' + spec]
        else:
            command += [spec]
        install_plugin(command, env)
        path = args.profile / 'openclaw/openclaw.json'
        config = json.loads(path.read_text())
        config['models']['providers']['bailian']['apiKey'] = '${BAILIAN_API_KEY}'
        config.setdefault('plugins', {}).setdefault('entries', {}).setdefault(
            'openclaw-channel-bcn', {})['enabled'] = True
        path.write_text(json.dumps(config))
    else:
        env['DSH_HOME'] = str(args.profile / 'dsh')
        try:
            install_plugin(['dsh', 'plugin', '--profile', 'web', 'add', spec], env)
        except subprocess.CalledProcessError:
            # This install phase has no model or BCS credentials. Surface bounded
            # package-manager diagnostics rather than losing the underlying error.
            for log in (args.profile / 'dsh').rglob('pnpm.log'):
                text = re.sub(r'[A-Za-z0-9_+/=-]{24,}', '[REDACTED]', log.read_text(errors='replace'))
                print(text[-6000:], flush=True)
            raise


if __name__ == '__main__':
    main()
