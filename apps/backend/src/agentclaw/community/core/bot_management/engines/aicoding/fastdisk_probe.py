"""Shared read-only fastdisk probe; callers own their failure policy."""
from __future__ import annotations

import base64
import json
import shlex
import zlib
from pathlib import Path, PurePosixPath
from typing import Any


class FastdiskDetectionError(RuntimeError):
    """Invalid or unavailable runtime detection, never proof of NAS-only data."""


DETECTION = Path(__file__).parent / 'fastdisk_detection'


def detection_command() -> str:
    gate = (DETECTION / 'persist_mount_gate.py').read_text()
    # Avoid the CLI entrypoint; run the same gate function with runtime paths.
    gate = gate.split("if __name__ == '__main__':", 1)[0]
    layout = (DETECTION / 'runtime_layout.py').read_text()
    source = gate + '\n' + layout + '\n' + (
        'layout = runtime_layout()\n'
        'status = check_mounts(layout["storage"], layout["home"], '
        'layout["storage"] + "/.fastdisk.ready", "/proc/self/mountinfo")\n'
        'print(json.dumps(dict(status=status, **layout)))\n'
    )
    encoded = base64.b64encode(zlib.compress(source.encode())).decode()
    command = 'python3 -c ' + shlex.quote(
        f'import base64,zlib; exec(zlib.decompress(base64.b64decode({encoded!r})))'
    )
    if len(command.encode()) > 8192:
        raise FastdiskDetectionError('embedded detector exceeds command size limit')
    return command


def parse_detection_result(result: Any) -> dict:
    exit_code = result.get('exit_code') if isinstance(result, dict) else getattr(result, 'exit_code', None)
    if exit_code != 0:
        raise FastdiskDetectionError('fastdisk detection failed')
    try:
        stdout = result.get('stdout') if isinstance(result, dict) else result.stdout
        data = json.loads(stdout)
        if not isinstance(data, dict) or data.get('status') not in ('run', 'skip_no_fastdisk'):
            raise ValueError('invalid gate result')
        for key in ('storage', 'home'):
            path = PurePosixPath(data[key])
            if not path.is_absolute() or '..' in path.parts:
                raise ValueError('invalid runtime path')
    except (AttributeError, ValueError, TypeError, KeyError) as error:
        raise FastdiskDetectionError('invalid fastdisk detection result') from error
    return data
