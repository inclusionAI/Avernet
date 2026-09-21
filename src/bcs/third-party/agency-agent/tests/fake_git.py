#!/usr/bin/env python3
"""Fake only the remote Git boundary; exercise real cache directory handling."""
import json
import os
import shutil
import sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ['FAKE_GIT_CALLS'], 'a') as output:
    output.write(json.dumps({'args': args, 'has_registration_proof': 'BCS_REGISTER_TOKEN' in os.environ}) + '\n')
if args[0] == 'clone':
    destination = Path(args[-1])
    if os.environ.get('FAKE_CLONE_FAIL'):
        destination.mkdir(parents=True)
        (destination / 'partial').touch()
        print('unsafe-server-output-test-token', file=sys.stderr)
        sys.exit(1)
    shutil.copytree(os.environ['FAKE_AGENCY_SOURCE'], destination)
    (destination / '.git').mkdir()
elif args[0] == '-C' and args[2:] == ['rev-parse', '--show-toplevel']:
    if not (Path(args[1]) / '.git').is_dir():
        sys.exit(1)
    print(str(Path(args[1]).resolve()))
elif args[0] == '-C' and args[2:] == ['remote', 'get-url', 'origin']:
    print('https://github.com/msitarzewski/agency-agents.git')
else:
    print('unexpected git operation', file=sys.stderr)
    sys.exit(2)
