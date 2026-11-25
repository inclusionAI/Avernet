#!/usr/bin/env python3
"""Run exactly one ignored MySQL contract using the unit job's existing build."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def identifier(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9_:-]+', value):
        raise argparse.ArgumentTypeError('expected a Rust package/binary/test identifier')
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=identifier)
    parser.add_argument('--binary', required=True, type=identifier)
    parser.add_argument('--test', required=True, type=identifier)
    args = parser.parse_args()
    if not os.environ.get('BCS_TEST_MYSQL_URL'):
        parser.error('BCS_TEST_MYSQL_URL must name an isolated contract database')

    bcs = Path(__file__).resolve().parents[1]
    reports = bcs / 'testresult'
    metadata = reports / 'cargo-metadata.json'
    binaries = reports / 'test-binaries.json'
    if not metadata.is_file() or not binaries.is_file():
        parser.error('run ci_test.sh --coverage --reuse-build before MySQL contracts')
    # Match the full method or its module-qualified suffix, not a substring.
    expression = (f'package(={args.package}) & binary(={args.binary}) & '
                  f'test(/(^|::){args.test}$/)')
    command = ['cargo', 'nextest']
    reuse = ['--cargo-metadata', str(metadata), '--binaries-metadata', str(binaries),
             '--run-ignored', 'only', '-E', expression]
    profiles = reports / 'mysql-profraw'
    profiles.mkdir(exist_ok=True)
    # Listing also executes instrumented test binaries; isolate those profiles.
    env = {**os.environ, 'LLVM_PROFILE_FILE': str(profiles / '%m-%p.profraw')}
    listed = subprocess.run([*command, 'list', *reuse, '--message-format', 'json'],
                            cwd=bcs, env=env, capture_output=True, text=True)
    if listed.returncode:
        parser.exit(listed.returncode, listed.stderr)
    suites = json.loads(listed.stdout)['rust-suites'].values()
    matches = [case for suite in suites for case in suite.get('testcases', {}).values()
               if case['filter-match']['status'] == 'matches']
    if len(matches) != 1 or not matches[0]['ignored']:
        parser.error(f'expected exactly one ignored test; matched {len(matches)}')

    name = f'{args.package}-{args.binary}-{args.test}'.replace(':', '_')
    junit = reports / 'mysql-contracts' / f'{name}.xml'
    junit.parent.mkdir(parents=True, exist_ok=True)
    junit.unlink(missing_ok=True)
    # Do not mix contract executions into the already-finalized unit coverage.
    config = (bcs / '.config/nextest.toml').read_text()
    config += ('\n[profile.mysql]\nretries = 0\ntest-threads = 1\n'
               'slow-timeout = { period = "60s", terminate-after = 5 }\n'
               f'[profile.mysql.junit]\npath = {json.dumps(str(junit))}\n')
    with tempfile.TemporaryDirectory(prefix='bcs-mysql-nextest-') as temporary:
        config_path = Path(temporary) / 'nextest.toml'
        config_path.write_text(config)
        result = subprocess.run([*command, 'run', *reuse, '--config-file', str(config_path),
                                 '--profile', 'mysql', '--retries', '0', '--no-tests', 'fail',
                                 '--no-capture'], cwd=bcs, env=env)
    if result.returncode:
        return result.returncode
    # No zero-test, ignored, flaky retry, missing-result or partial-success runs.
    cases = ET.parse(junit).getroot().findall('.//testcase')
    if len(cases) != 1 or any(case.find(tag) is not None
                              for case in cases for tag in ['failure', 'error', 'skipped']):
        parser.error('expected one passed contract with no failed or ignored tests')
    # Preserve the existing workflow's strict aggregate assertions.
    print('test result: ok. 1 passed; 0 failed; 0 ignored')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
