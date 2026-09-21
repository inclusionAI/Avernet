#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["PyYAML==6.0.3"]
# ///
"""Launch local agency profiles as isolated OpenClaw Gateways connected to BCS."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from agency_console import report, style
from agency_parallel import run_parallel
from agency_profiles import (
    Profile,
    build_config,
    identity_files,
    load_model_config,
    load_profile_snapshot,
    load_profiles,
)
from agency_runtime import (
    PLUGIN_ID,
    OpenClaw,
    RequestFailed,
    commit_reregistrations,
    endpoint_urls,
    ensure_agency_checkout,
    load_session,
    post_json,
    private_dir,
    read_json,
    register_bot,
    reregister_bot,
    state_lock,
    write_json,
    write_private,
)


class AgentSelection(argparse.Action):
    """Keep --team and --profile in their original command-line order."""

    def __call__(self, parser, namespace, values, option_string=None):
        selections = list(getattr(namespace, self.dest, None) or [])
        selections.append((self.const, values))
        setattr(namespace, self.dest, selections)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False, epilog=(
        'Runs in the foreground; Ctrl+C stops owned Gateways but keeps credentials. '
        'Model credentials must be supplied via provider environment variables or the model JSON.'))
    parser.add_argument('--agency-dir', type=Path,
                        help='local checkout; default: clone/reuse <state-dir>/agency-agent')
    parser.add_argument('--engine', default='openclaw', choices=['openclaw'],
                        help='agent engine (currently only openclaw is supported)')
    parser.add_argument('--profile', dest='selections', action=AgentSelection, const='profile',
                        metavar='TEAM/PROFILE', help='repeat to select team/profile (optional .md suffix)')
    parser.add_argument('--team', dest='selections', action=AgentSelection, const='team',
                        metavar='TEAM', help='start a team; deduplicated totals over 5 require interactive confirmation')
    parser.add_argument('--model-config', type=Path, default=Path.home() / '.openclaw/openclaw.json',
                        help='model JSON (default: ~/.openclaw/openclaw.json); other settings are ignored')
    parser.add_argument('--bcs-endpoint', required=True, help='HTTP(S) BCS base URL, including deployment prefix if needed')
    credentials = parser.add_mutually_exclusive_group()
    credentials.add_argument('--token', help='Human/User registration token, as in install.sh')
    credentials.add_argument('--token-file', type=Path, help='registration token file; otherwise BCS_REGISTER_TOKEN')
    parser.add_argument('--overwrite-profile', action='store_true', help='accept all changed profile overwrites without prompting')
    parser.add_argument('--reregister', action='store_true', help='re-register every selected instance that already has a BCS session')
    parser.add_argument('--state-dir', type=Path, default=Path.home() / '.avernet/bcs/agency-agent',
                        help='dedicated persistent directory (default: ~/.avernet/bcs/agency-agent; instances go under <engine>)')
    parser.add_argument('--parallel', type=int, default=4,
                        help='maximum concurrent installation/startup tasks (default: 4; use 1 for serial)')
    parser.add_argument('--base-port', type=int, default=19000)
    parser.add_argument('--port-step', type=int, default=20, help='spacing for new Gateways, at least 20')
    parser.add_argument('--startup-timeout', type=float, default=90, help='seconds to verify each BCS connection')
    parser.add_argument('--bcn-plugin', default='@avernet-plugin/openclaw-channel-bcn@1.0.23',
                        help='OpenClaw plugin install spec (pinned npm package, local built directory or tarball)')
    args = parser.parse_args()
    if args.parallel < 1:
        parser.error('--parallel must be a positive integer')
    if not args.selections:
        parser.error('at least one --profile or --team is required')
    if args.port_step < 20 or not 1024 <= args.base_port <= 65515:
        parser.error('--port-step must be >=20 and --base-port must be 1024..65515')
    if not 0 < args.startup_timeout <= 3600:
        parser.error('--startup-timeout must be in (0, 3600]')
    if args.bcn_plugin.startswith('-'):
        parser.error('--bcn-plugin must not be an option')
    local_plugin = Path(args.bcn_plugin).expanduser()
    if local_plugin.exists():
        args.bcn_plugin = str(local_plugin.resolve())
    return args


def port_available(port: int) -> None:
    # Check the reserved range, not just the main port (OpenClaw derives helper ports).
    for candidate in range(port, port + 20):
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', candidate))
            except OSError:
                raise ValueError(f'port {candidate} is unavailable; stop its owner or choose another --base-port') from None


@dataclass
class LaunchPlan:
    profile: Profile
    state: Path
    port: int
    record: dict | None
    existing_session: dict | None
    overwrite: bool = False
    reregister: bool = False


def interactive_input() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def ask_yes_no(question: str, default: bool = False) -> bool:
    suffix = ' [Y/n] ' if default else ' [y/N] '
    answer = input(style(question + suffix, 'warning')).strip().lower()
    if not answer:
        return default
    return answer in {'y', 'yes'}


def confirm_team_size(selections: list[tuple[str, str]], agent_count: int) -> bool:
    """Ask once about a large team-expanded launch, before touching any instance."""
    if agent_count <= 5 or not any(kind == 'team' for kind, _ in selections):
        return True
    report(f'WARNING: selected {agent_count} agents in total after deduplication. '
           'Launching this many agents uses more processes/memory and may incur model costs.', 'warning')
    if not interactive_input():
        raise ValueError('launching a team with more than 5 agents requires confirmation '
                         'in an interactive terminal; no instances were changed')
    try:
        return ask_yes_no(f'Continue launching all {agent_count} agents?')
    except EOFError:
        return False


def prepare_plans(args, profiles, root: Path, ws_url: str, registration_proof: str) -> list[LaunchPlan]:
    existing = {path.parent.name: read_json(path) for path in root.glob('*/instance.json')}
    occupied: list[int] = []
    for record in existing.values():
        port = record.get('port')
        if type(port) is not int or not 1024 <= port <= 65515:
            raise ValueError('invalid saved Gateway port; repair instance.json')
        occupied.append(port)
    plans: list[LaunchPlan] = []
    for profile in profiles:
        state = root / profile.instance_id
        if state.is_symlink():
            raise ValueError('instance directory must not be a symlink')
        record = existing.get(profile.instance_id)
        if record:
            if record.get('engine', 'openclaw') != args.engine:
                raise ValueError(f'{profile.instance_id}: saved engine differs; refusing to reuse its state')
            expected = {'profile_path': profile.path, 'bcs_url': ws_url, 'plugin': args.bcn_plugin}
            if any(record.get(key) != value for key, value in expected.items()):
                raise ValueError(f'{profile.instance_id}: endpoint or plugin changed; use a new --state-dir')
            port = record['port']
        else:
            if state.exists():
                raise ValueError('unrecognized instance directory; refusing to overwrite it')
            port = args.base_port
            while any(abs(port - used) < 20 for used in occupied):
                port += args.port_step
            if port > 65515:
                raise ValueError('not enough Gateway ports for the selected profiles')
            occupied.append(port)
        session = load_session(state, ws_url) if (state / '.bcs/session.json').exists() else None
        if (state / 'registration.pending.json').exists():
            raise ValueError(f'{profile.instance_id}: registration outcome unknown; '
                             'recover credentials or reconcile registration.pending.json before retrying')
        if (state / 'bcs-reregistration.pending.json').exists():
            raise ValueError(f'{profile.instance_id}: BCS re-registration outcome unknown; '
                             'recover credentials or reconcile bcs-reregistration.pending.json before retrying')
        if session is None and not registration_proof:
            raise ValueError('new instances require --token, --token-file or BCS_REGISTER_TOKEN')
        port_available(port)
        plans.append(LaunchPlan(profile, state, port, record, session))
    return plans


def choose_profile_actions(plans: list[LaunchPlan], args, registration_proof: str) -> None:
    """Prompt per changed source, then once for all existing BCS sessions."""
    for plan in plans:
        if not plan.record or plan.record.get('source_sha256') == plan.profile.digest:
            continue
        if args.overwrite_profile:
            plan.overwrite = True
        elif interactive_input():
            plan.overwrite = ask_yes_no(
                f'[{plan.profile.name}] Source profile changed. Overwrite the local profile?')
        else:
            raise ValueError(f'{plan.state.name}: profile changed; rerun with --overwrite-profile to allow overwrite')
        if not plan.overwrite:
            snapshot = plan.state / 'profile.md'
            saved = load_profile_snapshot(snapshot, plan.record['profile_path'])
            if saved.digest != plan.record.get('source_sha256'):
                raise ValueError(f'{plan.state.name}: saved profile snapshot does not match instance metadata')
            plan.profile = saved
    existing = [plan for plan in plans if plan.existing_session is not None]
    if not existing:
        return
    if args.reregister:
        do_reregister = True
    elif interactive_input():
        report('\nExisting BCS sessions detected:')
        for plan in existing:
            report(f'  {plan.profile.name}: Bot ID {plan.existing_session["bot_uuid"]}')
        do_reregister = ask_yes_no('Re-register all existing BCS sessions as new Bots?')
    else:
        do_reregister = False
    if do_reregister and not registration_proof:
        raise ValueError('BCS re-registration requires --token, --token-file or BCS_REGISTER_TOKEN')
    for plan in existing:
        plan.reregister = do_reregister


def prepare_workspace(profile, state: Path, port: int, args, model: dict, ws_url: str, overwrite_profile: bool = False) -> None:
    private_dir(state)
    for directory in ('workspace', 'agents/main/agent', '.bcs'):
        private_dir(state / directory)
    write_json(state / 'instance.json', {
        'profile_path': profile.path, 'source_sha256': profile.digest,
        'source_root': str(args.agency_dir.resolve()), 'bcs_url': ws_url,
        'port': port, 'plugin': args.bcn_plugin, 'engine': args.engine,
    })
    snapshot = state / 'profile.md'
    if overwrite_profile and snapshot.exists():
        backup = state / f'profile.previous.{time.time_ns()}.md'
        write_private(backup, snapshot.read_text(encoding='utf-8'))
    if overwrite_profile or not snapshot.exists():
        write_private(snapshot, profile.source)
    for name, content in identity_files(profile).items():
        target = state / 'workspace' / name
        # Preserve files edited by the running agent; rerun is restart, not reset.
        if overwrite_profile or not target.exists():
            write_private(target, content)
    config_path = state / 'openclaw.json'
    old = read_json(config_path) if config_path.exists() else {}
    gateway_auth_value = old.get('gateway', {}).get('auth', {}).get('token') or secrets.token_urlsafe(32)
    config = build_config(profile, state, port, model, gateway_auth_value)
    # Retain meaningful channel settings on restart/recovery. Removing them makes
    # BCN setup-entry write during `plugins list --json`, corrupting JSON stdout
    # and potentially conflicting with the CLI's next config transaction.
    if old.get('channels', {}).get('bcs'):
        config['channels'] = {'bcs': {
            'enabled': False, 'bcsUrl': ws_url, 'heartbeatIntervalMs': 30000,
        }}
    if 'plugins' in old:
        # Preserve the CLI-owned installation receipt so reruns do not reinstall.
        plugins = old['plugins']
        config['plugins'] = {
            'allow': [PLUGIN_ID], 'entries': {PLUGIN_ID: {'enabled': True}},
            'installs': {key: value for key, value in plugins.get('installs', {}).items() if key == PLUGIN_ID},
        }
    write_json(config_path, config)


def configure_channel(state: Path, session: dict, ws_url: str, profile) -> None:
    # Installation may have written receipts to config: read them back first.
    config = read_json(state / 'openclaw.json')
    plugins = config.setdefault('plugins', {})
    plugins['allow'] = [PLUGIN_ID]
    plugins['entries'] = {PLUGIN_ID: {'enabled': True}}
    config['channels'] = {'bcs': {
        'enabled': True, 'bcsUrl': ws_url, 'botId': session['bot_uuid'],
        'botName': profile.name, 'heartbeatIntervalMs': 30000,
    }}
    write_json(state / 'openclaw.json', config)


CAPABILITY_ATTEMPTS = 3
CAPABILITY_DELAYS = (0.5, 1.0)


def publish_capabilities(runtime: OpenClaw, endpoint: str, profile, state: Path,
                         session: dict) -> bool:
    """Best-effort metadata convergence after authenticated network readiness."""
    pending = state / 'bcs-onboard-last-response.json'
    payload = {
        'name': profile.name, 'summary': profile.description,
        'domains': [Path(profile.path).parts[0]] if '/' in profile.path else [],
        'skills': [], 'scopes': [],
    }
    diagnostic: dict = {'bot_uuid': session['bot_uuid'], 'attempts': 0}
    for attempt in range(1, CAPABILITY_ATTEMPTS + 1):
        runtime.check_alive()
        diagnostic['attempts'] = attempt
        try:
            result = post_json(endpoint + '/bots/onboard', payload, session['token'])
            runtime.check_alive()
            returned_id = result.get('bot_uuid')
            confirmed = (result.get('onboarded') is True
                         and (returned_id is None or returned_id == session['bot_uuid']))
            if confirmed:
                pending.unlink(missing_ok=True)
                return True
            diagnostic.pop('error', None)
            diagnostic['response'] = result
        except RequestFailed as error:
            diagnostic.pop('response', None)
            diagnostic['error'] = str(error)
        if attempt < CAPABILITY_ATTEMPTS:
            runtime.pause(CAPABILITY_DELAYS[attempt - 1])
    runtime.check_alive()
    write_json(pending, diagnostic)
    return False


def run(args) -> None:
    model = load_model_config(args.model_config.expanduser())
    endpoint, ws_url = endpoint_urls(args.bcs_endpoint)
    executable = shutil.which('openclaw')
    if not executable:
        raise ValueError('openclaw is not installed or not on PATH')
    if args.token is not None:
        registration_proof = args.token.strip()
    elif args.token_file:
        registration_proof = args.token_file.expanduser().read_text(encoding='utf-8').strip()
    else:
        registration_proof = os.environ.get('BCS_REGISTER_TOKEN', '').strip()
    root = args.state_dir.expanduser().resolve()
    if any(root.glob('*/instance.json')):
        raise ValueError('legacy flat instance layout detected; run migrate-layout.sh before launching')
    if args.agency_dir is None:
        # Serialize only shared checkout setup, not the lifetime of another engine.
        with state_lock(root):
            args.agency_dir = ensure_agency_checkout(root)
    else:
        args.agency_dir = args.agency_dir.expanduser()
    engine_root = root / args.engine
    with state_lock(engine_root):
        profiles = load_profiles(args.agency_dir, args.selections)
        if not confirm_team_size(args.selections, len(profiles)):
            report('Launch cancelled; no instances were changed.', 'warning')
            return
        plans = prepare_plans(args, profiles, engine_root, ws_url, registration_proof)
        choose_profile_actions(plans, args, registration_proof)
        runtime = OpenClaw(executable)
        try:
            # Finish all plugin installs before any registration. Confirmation has
            # already happened for every profile, so a declined overwrite still runs
            # with its saved local snapshot and can participate in the global BCS choice.
            def prepare(plan: LaunchPlan) -> None:
                runtime.check_cancelled()
                report(f'[{plan.profile.name}] PREPARING (port {plan.port})')
                prepare_workspace(plan.profile, plan.state, plan.port, args, model, ws_url, plan.overwrite)
                runtime.check_cancelled()
                report(f'[{plan.profile.name}] INSTALLING BCN plugin')
                runtime.install(plan.state, args.bcn_plugin)
                report(f'[{plan.profile.name}] PREPARED', 'success')

            run_parallel(plans, prepare, args.parallel, runtime.cancel)

            sessions: dict[Path, dict] = {}
            reregistration_items: list[tuple[Path, dict, dict]] = []
            for plan in plans:
                if plan.reregister:
                    new_session = reregister_bot(plan.state, endpoint, ws_url, plan.profile.name, registration_proof)
                    reregistration_items.append((plan.state, plan.existing_session, new_session))
                    sessions[plan.state] = new_session
                else:
                    sessions[plan.state] = (plan.existing_session
                                            if plan.existing_session is not None
                                            else register_bot(plan.state, endpoint, ws_url, plan.profile.name, registration_proof))
            if reregistration_items:
                commit_reregistrations(reregistration_items)
            for plan in plans:
                configure_channel(plan.state, sessions[plan.state], ws_url, plan.profile)
            def start_and_connect(plan: LaunchPlan) -> bool:
                runtime.check_cancelled()
                report(f'[{plan.profile.name}] STARTING (port {plan.port})')
                runtime.start(plan.state)
                runtime.wait_connected(plan.state, args.startup_timeout)
                # A successful handshake may rotate the reconnect token.
                session = load_session(plan.state, ws_url)
                confirmed = publish_capabilities(runtime, endpoint, plan.profile, plan.state, session)
                capability_status = 'confirmed' if confirmed else 'pending'
                if not confirmed:
                    report(f'WARNING [{plan.profile.name}]: capability metadata pending; private diagnostic: '
                           f'{plan.state / "bcs-onboard-last-response.json"}', 'warning')
                report(f'[{plan.profile.name}] CONNECTED: bot_id={session["bot_uuid"]}, port={plan.port}, '
                       f'capabilities={capability_status}, state={plan.state}', 'success')
                return confirmed

            statuses = run_parallel(plans, start_and_connect, args.parallel, runtime.cancel)
            pending_capabilities = statuses.count(False)
            suffix = (f' capability metadata pending for {pending_capabilities} instance(s);'
                      if pending_capabilities else '')
            report(f'ALL CONNECTED —{suffix} model replies not tested. '
                   'Ctrl+C stops these Gateways; state is retained.',
                   'warning' if pending_capabilities else 'success')
            while True:
                runtime.check_alive()
                time.sleep(0.5)
        finally:
            runtime.close()


def main() -> int:
    args = arguments()
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        run(args)
    except KeyboardInterrupt:
        report('Stopped owned Gateways; profile state and BCS credentials retained.')
        return 0
    except (ValueError, TypeError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        # Values raised by helpers are sanitized. OSError may contain sensitive paths;
        # subprocess timeouts can contain command output, so do not stringify either.
        message = str(error) if isinstance(error, (ValueError, TypeError, RuntimeError)) else type(error).__name__
        report(f'ERROR: {message}', 'error', error=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
