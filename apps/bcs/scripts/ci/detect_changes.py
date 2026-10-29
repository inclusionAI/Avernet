#!/usr/bin/env python3
"""Resolve the exact BCS CI diff and fail closed if its base cannot be read."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def git(*args: str, stdin: str | None = None) -> str:
    return subprocess.run(['git', *args], input=stdin, check=True,
                          capture_output=True, text=True).stdout.strip()


def empty_tree() -> str:
    return git('hash-object', '-w', '-t', 'tree', '--stdin', stdin='')


def resolve_base(event: str) -> str:
    if event == 'pull_request':
        # checkout's synthetic merge parent is the precise target tree.
        return git('rev-parse', '--verify', 'HEAD^1^{commit}')
    if event == 'push':
        before = os.environ.get('BCS_PUSH_BEFORE', '')
        if not before:
            raise ValueError('push event is missing its before SHA')
        if set(before) == {'0'}:
            return empty_tree()
        return git('rev-parse', '--verify', f'{before}^{{commit}}')
    if event == 'workflow_dispatch':
        parents = git('rev-list', '--parents', '-n', '1', 'HEAD').split()
        return parents[1] if len(parents) > 1 else empty_tree()
    raise ValueError(f'unsupported CI event: {event!r}')


def relevant(path: str) -> bool:
    return (path.startswith('apps/bcs/')
            or path == '.github/workflows/unit-tests.yml'
            or path == 'singlebox/ci/tests/test_unit_test_workflow_diff.py'
            or (path.startswith('singlebox/ci/tests/')
                and Path(path).name.startswith('test_bcs_')))


def main() -> None:
    root = git('rev-parse', '--show-toplevel')
    os.chdir(root)
    event = os.environ['GITHUB_EVENT_NAME']
    base = resolve_base(event)
    # NUL-delimited names keep whitespace/newlines in paths intact. A failed
    # diff must fail the step, never become an empty successful test selection.
    changed = git('diff', '--name-only', '-z', base, '--').split('\0')
    files = [path for path in changed if relevant(path)]
    force = event == 'workflow_dispatch'
    plugin = force or any(path.startswith(
        'apps/bcs/crates/plugins/deepseek-harness-channel-bcn/') for path in files)
    values = {'base-ref': base,
              'bcs-changed-files-count': str(len(files)),
              'skip-bcs': str(not (force or files)).lower(),
              'dsh-bcn-changed': str(plugin).lower()}
    output = '\n'.join(f'{key}={value}' for key, value in values.items()) + '\n'
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream:
        stream.write(output)
    print(output, end='')
    for path in files:
        print(f'  - {path!r}')


if __name__ == '__main__':
    main()
