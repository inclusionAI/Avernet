#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT
source "$ROOT/scripts/utils.sh"
# Arguments with shell syntax must remain data, and setsid/exec must preserve PID.
start_in_detached_session python3 -c '
import json, os, sys
with open(sys.argv[1], "w") as out:
    json.dump({"pid": os.getpid(), "sid": os.getsid(0), "arg": sys.argv[2]}, out)
' "$TEMP/result.json" 'literal ; $(not-a-command)' &
pid=$!
wait "$pid"
python3 - "$TEMP/result.json" "$pid" <<'PY'
import json, sys
with open(sys.argv[1]) as src:
    result = json.load(src)
assert result == {"pid": int(sys.argv[2]), "sid": int(sys.argv[2]), "arg": 'literal ; $(not-a-command)'}
print('PASS: detached session, PID ownership and literal argv')
PY
