"""Run long container transfers through bounded command calls.

Only invocation parameters and execution control are staged, never transfer
implementation. The caller blocks until completion. Remote detachment only avoids the command
transport's 300-second limit; it is not a fire-and-forget publication operation.
"""
import base64
import json
import math
import shlex
import time
import uuid


class ArtifactCommandError(RuntimeError):
    pass


def _python(code):
    return 'python3 -c ' + shlex.quote(code)


def run_artifact_command(executor, device_id, command, *, timeout_seconds=None):
    """Wait for exit status; timeout terminates the remote process group.

    None intentionally preserves the legacy publication's unbounded transfer.
    Restore callers pass the remaining budget of their original deadline.
    """
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    root = '/tmp/service-artifact-' + uuid.uuid4().hex

    def execute(code):
        budget = 30
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('artifact transfer deadline exceeded')
            budget = min(30, max(1, math.ceil(remaining)))
        result = executor.exec_shell_new(device_id=device_id, shell_cmd=_python(code),
                                         timeout_seconds=budget)
        if result.exit_code != 0:
            raise ArtifactCommandError('container transfer control command failed')
        return result.stdout.strip()

    execute(f'import os; os.mkdir({root!r},0o700)')
    try:
        # Keep every execute-command payload comfortably below 8192 characters.
        payload = base64.b64encode(command.encode()).decode()
        for offset in range(0, len(payload), 3000):
            chunk = payload[offset:offset + 3000]
            execute(f'import pathlib; p=pathlib.Path({root!r})/"command.b64"; '
                    f'p.open("a").write({chunk!r})')
        runner = '''import base64,json,os,pathlib,subprocess,sys
root=pathlib.Path(sys.argv[1])
command=base64.b64decode((root/'command.b64').read_text()).decode()
with open(os.devnull,'wb') as sink:
    child=subprocess.Popen(['bash','-c',command],stdout=sink,stderr=sink)
    code=child.wait()
tmp=root/'result.tmp'
tmp.write_text(json.dumps({'exit_code':code}))
os.replace(tmp,root/'result.json')
'''
        execute('import pathlib,subprocess,os; '
                f'root=pathlib.Path({root!r}); (root/"runner.py").write_text({runner!r}); '
                'sink=open(os.devnull,"wb"); '
                'p=subprocess.Popen(["python3",str(root/"runner.py"),str(root)],'
                'stdin=subprocess.DEVNULL,stdout=sink,stderr=sink,start_new_session=True); '
                '(root/"pid").write_text(str(p.pid))')
        while True:
            status = execute('import pathlib,os; '
                             f'root=pathlib.Path({root!r}); p=root/"result.json"; '
                             '\nif p.exists(): print(p.read_text())\n'
                             'else:\n'
                             ' os.kill(int((root/"pid").read_text()),0)\n'
                             ' print("pending")\n')
            if status != 'pending':
                result = json.loads(status)
                if type(result.get('exit_code')) is not int:
                    raise ArtifactCommandError('invalid container transfer result')
                if result['exit_code'] != 0:
                    raise ArtifactCommandError('container transfer failed '
                                               f"(exit={result['exit_code']})")
                return
            time.sleep(1)
    finally:
        # A transport failure is not success. Best-effort cancellation avoids
        # knowingly leaving a writer running after publication reports failure.
        cleanup = ('import pathlib,os,signal,shutil; '
                   f'root=pathlib.Path({root!r}); '
                   'pid=root/"pid"; result=root/"result.json"; '
                   '\nif pid.exists() and not result.exists():\n'
                   ' try: os.killpg(int(pid.read_text()),signal.SIGKILL)\n'
                   ' except ProcessLookupError: pass\n'
                   'shutil.rmtree(root)\n')
        try:
            executor.exec_shell_new(device_id=device_id, shell_cmd=_python(cleanup),
                                    timeout_seconds=30)
        except Exception:
            # Never mask the transfer's original failure with cleanup failure.
            pass
