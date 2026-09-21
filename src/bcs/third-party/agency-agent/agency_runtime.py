"""Local filesystem, HTTP and process boundaries for the agency launcher."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from concurrent.futures import CancelledError
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from agency_console import report

PLUGIN_ID = 'openclaw-channel-bcn'
# Public bootstrap source explicitly chosen for the agency launcher.
AGENCY_REPOSITORY = 'https://github.com/msitarzewski/agency-agents.git'


def private_dir(path: Path) -> None:
    if path.is_symlink():
        raise ValueError('runtime directories must not be symlinks')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def write_private(path: Path, content: str) -> None:
    private_dir(path.parent)
    if path.is_symlink():
        raise ValueError('runtime files must not be symlinks')
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.writing-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, value: dict) -> None:
    write_private(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        raise ValueError(f'cannot read runtime JSON: {path.name}; repair it before retrying') from None
    if not isinstance(value, dict):
        raise TypeError(f'{path.name} must contain a JSON object')
    return value


@contextmanager
def state_lock(root: Path):
    private_dir(root)
    path = root / '.launcher.lock'
    if path.is_symlink():
        raise ValueError('launcher lock must not be a symlink')
    with path.open('a+') as lock:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another launcher is using this state directory') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def endpoint_urls(value: str) -> tuple[str, str]:
    url = urlsplit(value)
    try:
        port = url.port
    except ValueError:
        raise ValueError('invalid BCS endpoint port') from None
    if (url.scheme not in ('http', 'https') or not url.hostname or url.username
            or url.password or url.query or url.fragment or (port is not None and port == 0)):
        raise ValueError('BCS endpoint must be an http(s) URL without credentials, query or fragment')
    host = '127.0.0.1' if url.hostname == 'localhost' else url.hostname
    host = f'[{host}]' if ':' in host else host
    authority = host + (f':{port}' if port is not None else '')
    path = url.path.rstrip('/')
    http = urlunsplit((url.scheme, authority, path, '', ''))
    ws = urlunsplit(('wss' if url.scheme == 'https' else 'ws', authority, path + '/ws/bot', '', ''))
    return http, ws


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward registration or bot credentials to a redirect.


class RequestFailed(RuntimeError):
    def __init__(self, status: int = 0):
        self.status = status
        # Do not include a URL, response body, or underlying exception (may contain tokens).
        super().__init__(f'BCS request failed (HTTP {status})' if status else 'BCS request failed or timed out')


def post_json(url: str, payload: dict, token: str = '') -> dict:
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = Request(url, data=json.dumps(payload).encode(), headers=headers, method='POST')
    try:
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            if response.status not in (200, 201):
                raise RequestFailed(response.status)
            content = response.read(1024 * 1024 + 1)
            if len(content) > 1024 * 1024:
                raise RequestFailed()
            value = json.loads(content)
    except HTTPError as error:
        raise RequestFailed(error.code) from None
    except (URLError, OSError, ValueError):
        raise RequestFailed() from None
    if not isinstance(value, dict):
        raise RequestFailed()
    result = value.get('data', value)
    if not isinstance(result, dict):
        raise RequestFailed()
    return result


def load_session(state: Path, ws_url: str) -> dict:
    session = read_json(state / '.bcs/session.json')
    if any(not isinstance(session.get(key), str) or not session[key].strip()
           for key in ('bot_uuid', 'token', 'bcs_url')):
        raise ValueError('invalid BCS session; repair it rather than registering another Bot')
    if session['bcs_url'] != ws_url:
        raise ValueError('saved session belongs to a different BCS endpoint; use a new state directory')
    return session


def registration_credentials(endpoint: str, name: str, register_token: str) -> dict:
    result = post_json(endpoint + '/register?' + urlencode({'token': register_token, 'bot-name': name}), {})
    if any(not isinstance(result.get(key), str) or not result[key].strip()
           for key in ('bot_uuid', 'bot_token')):
        raise ValueError('registration returned invalid credentials; outcome unknown, not retrying')
    return result


def register_bot(state: Path, endpoint: str, ws_url: str, name: str, register_token: str) -> dict:
    session_path = state / '.bcs/session.json'
    pending = state / 'registration.pending.json'
    if session_path.exists():
        session = load_session(state, ws_url)
        pending.unlink(missing_ok=True)
        return session
    if pending.exists():
        raise ValueError(f'{state.name}: previous registration outcome is unknown; '
                         'recover credentials or reconcile registration.pending.json before retrying')
    if not register_token:
        raise ValueError('new instances require --token, --token-file or BCS_REGISTER_TOKEN')
    write_json(pending, {'endpoint': endpoint, 'bot_name': name})
    try:
        result = registration_credentials(endpoint, name, register_token)
    except RequestFailed as error:
        if error.status in (400, 401, 403):
            pending.unlink()  # An explicit refusal before registration is safe to retry.
        raise
    session = {'bot_uuid': result['bot_uuid'], 'token': result['bot_token'],
               'bot_name': name, 'bcs_url': ws_url}
    write_json(session_path, session)
    pending.unlink()
    return session


def reregister_bot(state: Path, endpoint: str, ws_url: str, name: str,
                   register_token: str) -> dict:
    old_session = load_session(state, ws_url)
    pending = state / 'bcs-reregistration.pending.json'
    if pending.exists():
        raise ValueError(f'{state.name}: previous BCS re-registration outcome is unknown; '
                         'recover the pending credentials before retrying')
    if not register_token:
        raise ValueError('BCS re-registration requires --token, --token-file or BCS_REGISTER_TOKEN')
    write_json(pending, {'endpoint': endpoint, 'old_bot_uuid': old_session['bot_uuid'],
                         'bot_name': name})
    try:
        result = registration_credentials(endpoint, name, register_token)
    except RequestFailed as error:
        if error.status in (400, 401, 403):
            pending.unlink()
        raise
    session = {'bot_uuid': result['bot_uuid'], 'token': result['bot_token'],
               'bot_name': name, 'bcs_url': ws_url}
    # Keep the new credential in the recovery record until every selected Bot has
    # registered and the local session swap can be committed as one batch.
    write_json(pending, {'endpoint': endpoint, 'old_bot_uuid': old_session['bot_uuid'],
                         'bot_name': name, 'new_session': session})
    return session


def commit_reregistrations(items: list[tuple[Path, dict, dict]]) -> None:
    """Back up and replace all old sessions only after every registration succeeds."""
    stamp = str(time.time_ns())
    for state, old_session, new_session in items:
        backup = state / '.bcs' / f'session.previous.{stamp}.json'
        write_json(backup, old_session)
        write_json(state / '.bcs/session.json', new_session)
    for state, _, _ in items:
        (state / 'bcs-reregistration.pending.json').unlink(missing_ok=True)


def child_environment(state: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('OPENCLAW_', 'BCS_', 'BOT_', 'MOLTIS_'))}
    env.update({
        'OPENCLAW_STATE_DIR': str(state), 'OPENCLAW_CONFIG_PATH': str(state / 'openclaw.json'),
        'OPENCLAW_DATA_DIR': str(state), 'BOT_DATA_DIR': str(state),
        'BCS_IGNORE_CREDENTIALS': '1', 'OPENCLAW_NO_RESPAWN': '1',
        'OPENCLAW_AGENT_DIR': str(state / 'agents/main/agent'),
        'PI_CODING_AGENT_DIR': str(state / 'agents/main/agent'),
    })
    return env


def signal_group(process: subprocess.Popen, sig: signal.Signals) -> bool:
    # Reap before signalling, but also handle exit between poll() and killpg().
    # Retry only after confirming the leader exited; real permission errors fail.
    process.poll()
    try:
        try:
            os.killpg(process.pid, sig)
        except PermissionError:
            if process.poll() is None:
                raise
            os.killpg(process.pid, sig)
    except ProcessLookupError:
        return False
    return True


def terminate(process: subprocess.Popen) -> None:
    if not signal_group(process, signal.SIGTERM):
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    signal_group(process, signal.SIGKILL)
    process.wait()


def run_git(executable: str, args: list[str], root: Path) -> str:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('BCS_', 'GIT_'))}
    env['GIT_TERMINAL_PROMPT'] = '0'
    process = subprocess.Popen([executable, *args], cwd=root, env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, _ = process.communicate(timeout=300)
    except BaseException:
        terminate(process)
        raise
    if process.returncode:
        operation = 'clone' if args[0] == 'clone' else 'cache validation'
        raise RuntimeError(f'agency-agents {operation} failed; check Git/network or supply --agency-dir '
                           '(Git output withheld because it may contain credentials)')
    return stdout.strip()


def ensure_agency_checkout(root: Path) -> Path:
    """Atomically create the default checkout; never pull or overwrite a cache."""
    executable = shutil.which('git')
    if not executable:
        raise ValueError('git is required to clone/reuse the default repository; or supply --agency-dir')
    destination = root / 'agency-agent'
    if destination.is_symlink():
        raise ValueError('agency-agents cache must not be a symlink; supply --agency-dir instead')
    if destination.exists():
        top = run_git(executable, ['-C', str(destination), 'rev-parse', '--show-toplevel'], root)
        origin = run_git(executable, ['-C', str(destination), 'remote', 'get-url', 'origin'], root)
        if Path(top).resolve() != destination.resolve() or origin != AGENCY_REPOSITORY:
            raise ValueError('existing agency-agents cache is not the expected repository; supply --agency-dir')
        return destination
    report('Cloning agency-agents into the state directory (first use only)...')
    with tempfile.TemporaryDirectory(prefix='.agency-clone-', dir=root) as temporary:
        checkout = Path(temporary) / 'checkout'
        run_git(executable, ['clone', '--depth', '1', '--', AGENCY_REPOSITORY, str(checkout)], root)
        checkout.rename(destination)
    private_dir(destination)
    return destination


class OpenClawCommandFailed(RuntimeError):
    def __init__(self, state: Path, args: list[str], log: Path, config_conflict: bool):
        self.config_conflict = config_conflict
        super().__init__(f'{state.name}: OpenClaw {args[0]} {args[1]} failed; '
                         f'private diagnostic log: {log} (may contain credentials; do not share raw)')


class OpenClaw:
    def __init__(self, executable: str):
        self.executable = executable
        self.gateways: list[tuple[Path, subprocess.Popen]] = []
        self._cancelled = Event()
        self._spawn_lock = Lock()

    def cancel(self) -> None:
        with self._spawn_lock:
            self._cancelled.set()

    def check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise CancelledError('launcher stopped')

    def pause(self, seconds: float) -> None:
        if self._cancelled.wait(seconds):
            raise CancelledError('launcher stopped')

    def command(self, state: Path, args: list[str], timeout: float = 120) -> dict:
        with self._spawn_lock:
            self.check_cancelled()
            process = subprocess.Popen([self.executable, *args], env=child_environment(state),
                                       cwd=state, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, start_new_session=True)
        deadline = time.monotonic() + timeout
        try:
            while True:
                self.check_cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(process.args, timeout)
                try:
                    stdout, stderr = process.communicate(timeout=min(0.2, remaining))
                    self.check_cancelled()
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            terminate(process)
            raise
        if process.returncode:
            log = state / f'openclaw-{args[0]}-{args[1]}.log'
            write_private(log, f'exit_code={process.returncode}\n--- stdout ---\n{stdout}'
                          f'\n--- stderr ---\n{stderr}')
            raise OpenClawCommandFailed(state, args, log,
                                        'config changed since last load' in stdout + stderr)
        if '--json' not in args:
            return {}
        try:
            value = json.loads(stdout)
        except ValueError:
            raise RuntimeError(f'{state.name}: OpenClaw returned invalid JSON') from None
        if not isinstance(value, dict):
            raise TypeError('OpenClaw JSON response must be an object')
        return value

    def install(self, state: Path, package: str) -> None:
        result = self.command(state, ['plugins', 'list', '--json'])
        if any(p.get('id') == PLUGIN_ID for p in result.get('plugins', [])):
            return
        try:
            self.command(state, ['plugins', 'install', package], timeout=300)
        except OpenClawCommandFailed as error:
            if not error.config_conflict:
                raise
            # BCN's setup-entry can update channels.bcs during OpenClaw's install
            # config transaction. Never retry the install or bypass its safety scan.
            # Recover only when that exact conflict left a loadable plugin behind.
            result = self.command(state, ['plugins', 'list', '--json'])
            if not any(p.get('id') == PLUGIN_ID and p.get('status') == 'loaded'
                       for p in result.get('plugins', [])):
                raise
            self.command(state, ['plugins', 'enable', PLUGIN_ID])
            report(f'[{state.name}] recovered BCN setup configuration conflict; '
                   'plugin load and enable verified.', 'warning')
        result = self.command(state, ['plugins', 'list', '--json'])
        if not any(p.get('id') == PLUGIN_ID and p.get('status') == 'loaded'
                   for p in result.get('plugins', [])):
            raise RuntimeError(f'{state.name}: BCN plugin installation could not be verified')

    def start(self, state: Path) -> None:
        log = state / 'gateway.log'
        # Start a fresh log for this attempt, never infer readiness from old output.
        write_private(log, '')
        with log.open('a') as output, self._spawn_lock:
            self.check_cancelled()
            process = subprocess.Popen([self.executable, 'gateway', 'run'],
                                       env=child_environment(state), cwd=state,
                                       stdin=subprocess.DEVNULL, stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            self.gateways.append((state, process))

    def check_alive(self) -> None:
        self.check_cancelled()
        with self._spawn_lock:
            gateways = list(self.gateways)
        for state, process in gateways:
            if process.poll() is not None:
                raise RuntimeError(f'{state.name}: Gateway exited ({process.returncode}); inspect its private gateway.log')

    def wait_connected(self, state: Path, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.check_alive()
            try:
                value = self.command(state, ['channels', 'status', '--probe', '--json', '--timeout', '2000'],
                                     timeout=max(0.1, min(10, deadline - time.monotonic())))
                accounts = value.get('channelAccounts', {}).get('bcs', [])
                for account in accounts:
                    probe = account.get('probe', {})
                    token = probe.get('sessionToken')
                    # BCN sets connected when the socket opens, before bot.connect.
                    # Only a nonempty session token proves the handshake completed.
                    if (account.get('accountId') == 'default' and probe.get('connected') is True
                            and isinstance(token, str) and token.strip()):
                        self.check_alive()
                        return
            except (RuntimeError, subprocess.TimeoutExpired):
                pass
            self.pause(min(0.3, max(0, deadline - time.monotonic())))
        raise RuntimeError(f'{state.name}: BCS connection timed out; inspect its private gateway.log')

    def close(self) -> None:
        self.cancel()
        # run_parallel has joined all workers: no command/spawn can outlive this.
        with self._spawn_lock:
            gateways, self.gateways = self.gateways, []
        # Each group receives TERM once, then is reaped before moving on. Sending
        # TERM in advance and again in terminate races with short-lived children.
        for _, process in reversed(gateways):
            terminate(process)
