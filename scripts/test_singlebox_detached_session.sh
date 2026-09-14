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

# The macOS branch must be a FUNCTIONAL python3 check, not `command -v`: the
# xcode-select stub on a CLT-less stock macOS resolves like a real binary but
# cannot execute, and no prerequisite gate elsewhere verifies python3. A
# stubbed python3 (exit 9 on any invocation, like the CLT shim) must make
# detached_python3_usable fail, so the launcher falls back to Perl instead of
# exec-ing a dead interpreter with the service PID recorded behind a
# readiness timeout.
STUB_BIN="$TEMP/stub-bin"
mkdir -p "$STUB_BIN"
printf '#!/bin/sh\nexit 9\n' > "$STUB_BIN/python3"
chmod +x "$STUB_BIN/python3"
if PATH="$STUB_BIN:/usr/bin:/bin" detached_python3_usable; then
    echo 'a broken python3 was reported usable' >&2
    exit 1
fi
if ! detached_python3_usable; then
    echo 'the real python3 must be reported usable' >&2
    exit 1
fi
printf 'PASS: detached launcher python3 probe is functional, not existence-only\n'
