"""Own only child process groups created by this run; never kill by port/name."""
import os
import signal
import socket
import subprocess
import time


def listening(port):
    with socket.socket() as sock:
        sock.settimeout(.3)
        return sock.connect_ex(('127.0.0.1', port)) == 0


class Processes:
    def __init__(self):
        self.children = []

    def start(self, command, cwd, env, log):
        with log.open('a') as output:
            proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        self.children.append(proc)
        return proc

    def stop(self):
        failures = []
        for proc in reversed(self.children):
            # A live child is the ownership proof. Do not signal a recycled PID.
            if proc.poll() is not None:
                continue
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=5)
            except ProcessLookupError:
                pass
            except Exception:
                failures.append(proc.pid)
        if failures:
            raise RuntimeError(f'Owned processes did not stop: {failures}')


def wait_ready(proc, check, timeout, label):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f'{label} exited before ready; inspect its private log')
        if check():
            return
        time.sleep(.5)
    raise RuntimeError(f'{label} startup timed out; inspect its private log')
