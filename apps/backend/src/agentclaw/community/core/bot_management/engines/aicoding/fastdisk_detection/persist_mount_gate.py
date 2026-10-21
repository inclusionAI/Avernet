#!/usr/bin/env python3
"""Check live bind mounts, not just a possibly stale fastdisk marker."""
import os
import re
import sys


def check_mounts(storage, home, marker, mountinfo):
    targets = set()
    with open(mountinfo, encoding='utf-8') as stream:
        for line in stream:
            fields = line.split()
            if len(fields) < 10 or '-' not in fields:
                raise ValueError('invalid mountinfo')
            target = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4])
            targets.add(os.path.normpath(target))
    mounted = []
    for name in ('sessions', 'workspace'):
        source = os.path.join(storage, name)
        target = os.path.join(home, name)
        present = os.path.normpath(target) in targets
        mounted.append(present)
        if present and (not os.path.isdir(source) or not os.path.isdir(target)
                        or os.path.islink(source) or os.path.islink(target)
                        or not os.path.samefile(source, target)):
            raise ValueError('mount source mismatch: ' + name)
    if all(mounted):
        return 'run'
    if any(mounted) or os.path.lexists(marker):
        raise ValueError('partial mounts or ready marker conflicts with live mounts')
    fastdisk_pending = False
    for fastdisk_marker_name in ('.fastdisk.seeded',
                                 '.fastdisk.new-sessions-only.request'):
        fastdisk_marker = os.path.join(os.path.dirname(marker), fastdisk_marker_name)
        if not os.path.lexists(fastdisk_marker):
            continue
        if os.path.islink(fastdisk_marker) or not os.path.isfile(fastdisk_marker):
            raise ValueError('unsafe fastdisk pending marker')
        fields = {}
        try:
            with open(fastdisk_marker, encoding='utf-8') as stream:
                for line in stream:
                    key, separator, value = line.partition('=')
                    if separator:
                        fields[key] = value.strip()
        except PermissionError:
            # Root-owned state is enough when no mounts are live. The helper will
            # own the next cutover; never restore NAS over this pending state.
            fastdisk_pending = True
            continue
        if fields.get('version') != '1' or fields.get('storage_root') != storage:
            raise ValueError('invalid fastdisk pending marker')
        fastdisk_pending = True
    if fastdisk_pending:
        for name in ('sessions', 'workspace'):
            source = os.path.join(storage, name)
            if not os.path.isdir(source) or os.path.islink(source):
                raise ValueError('fastdisk pending marker does not match complete storage layout')
        return 'run'
    # A matching inode without the expected mountpoint is ambiguous (e.g. a
    # symlink or an ancestor bind); never treat it as confirmed NAS-only data.
    for name in ('sessions', 'workspace'):
        source, target = os.path.join(storage, name), os.path.join(home, name)
        if os.path.exists(source) and os.path.exists(target) and os.path.samefile(source, target):
            raise ValueError('fastdisk paths alias without expected mountpoints')
    return 'skip_no_fastdisk'


if __name__ == '__main__':
    try:
        print(check_mounts(*sys.argv[1:]))
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
