"""Thread-safe, terminal-only colors for human-facing launcher output."""
from __future__ import annotations

import os
import sys
from threading import Lock

_OUTPUT_LOCK = Lock()
_COLORS = {'info': '34', 'success': '32', 'warning': '33', 'error': '31'}


def style(message: str, level: str = 'info', *, error: bool = False) -> str:
    stream = sys.stderr if error else sys.stdout
    if not stream.isatty() or 'NO_COLOR' in os.environ or os.environ.get('TERM') == 'dumb':
        return message
    return f'\033[{_COLORS[level]}m{message}\033[0m'


def report(message: str, level: str = 'info', *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    with _OUTPUT_LOCK:
        stream.write(style(message, level, error=error) + '\n')
        stream.flush()
