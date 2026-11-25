#!/usr/bin/env bash
# Real subprocess regression: SIGTERM must retain execution after the USR1 flush.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="$ROOT/apps/backend/.venv/bin/python"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
unset COVERAGE_RCFILE
LOG_DIR="$tmp"
source "$ROOT/singlebox/modules/backend.sh"
mkdir -p "$tmp/project" "$tmp/raw"
cd "$tmp/project"
cat > pyproject.toml <<'TOML'
[tool.coverage.run]
branch = true
source = ["probe"]
omit = ["unrelated/*"]
[tool.coverage.report]
fail_under = 17
TOML
config="$(backend_coverage_config "$PYTHON" "$tmp/raw")"
"$PYTHON" - "$config" <<'PY'
import sys
from coverage import Coverage
c = Coverage(config_file=sys.argv[1])
assert c.get_option("run:sigterm") is True
assert c.get_option("run:branch") is True
assert c.get_option("run:source") == ["probe"]
assert c.get_option("run:omit") == ["unrelated/*"]
assert c.get_option("report:fail_under") == 17
PY
cat > probe.py <<'PY'
import os
import signal
from pathlib import Path

captured = []
old_handler = signal.signal(signal.SIGTERM, lambda sig, frame: captured.append(sig))
os.kill(os.getpid(), signal.SIGUSR1)
os.kill(os.getpid(), signal.SIGTERM)
# Model Uvicorn: real graceful shutdown runs before restoring/re-raising SIGTERM.
Path("shutdown-marker").write_text("closed")
signal.signal(signal.SIGTERM, old_handler)
signal.raise_signal(captured.pop())
PY
"$PYTHON" - "$PYTHON" "$config" <<'PY'
import os
from pathlib import Path
import signal
import subprocess
import sys
from coverage import CoverageData

for name, config, shutdown_saved in (
    ("before", "pyproject.toml", False),
    ("after", sys.argv[2], True),
):
    data_file = str(Path(f".coverage.{name}").absolute())
    env = {**os.environ, "COVERAGE_FILE": data_file}
    result = subprocess.run(
        [sys.argv[1], "-m", "coverage", "run", "--rcfile", config,
         "--save-signal=USR1", "probe.py"], env=env, check=False,
    )
    assert result.returncode == -signal.SIGTERM, result.returncode
    assert Path("shutdown-marker").read_text() == "closed"
    data = CoverageData(basename=data_file)
    data.read()
    assert (10 in data.lines(str(Path("probe.py").absolute()))) == shutdown_saved

PY
# Also preserve INI coverage settings and explicit COVERAGE_RCFILE selection.
cat > custom.ini <<'INI'
[run]
branch = true
omit =
    unrelated/*
[paths]
source =
    src
    */src
INI
config="$(COVERAGE_RCFILE="$tmp/project/custom.ini" backend_coverage_config "$PYTHON" "$tmp/raw")"
"$PYTHON" - "$config" <<'PY'
import sys
from coverage import Coverage
c = Coverage(config_file=sys.argv[1])
assert c.get_option("run:sigterm") is True
assert c.get_option("run:branch") is True
assert c.get_option("run:omit") == ["unrelated/*"]
assert c.get_option("paths") == {"source": ["src", "*/src"]}
PY
cat > setup.cfg <<'INI'
[coverage:run]
branch = true
omit = untouched/*
INI
config="$(COVERAGE_RCFILE="$tmp/project/setup.cfg" backend_coverage_config "$PYTHON" "$tmp/raw")"
"$PYTHON" - "$config" <<'PY'
import sys
from coverage import Coverage
c = Coverage(config_file=sys.argv[1])
assert c.get_option("run:sigterm") is True
assert c.get_option("run:branch") is True
assert c.get_option("run:omit") == ["untouched/*"]
PY
rm setup.cfg
rm pyproject.toml
config="$(backend_coverage_config "$PYTHON" "$tmp/raw")"
# The runner combines .coverage* files; configuration is not coverage data.
case "$(basename "$config")" in .coverage*) exit 1 ;; esac
"$PYTHON" - "$config" <<'PY'
import sys
from coverage import Coverage
c = Coverage(config_file=sys.argv[1])
assert c.get_option("run:sigterm") is True
assert c.get_option("run:source") is None
assert c.get_option("run:omit") == []
PY
# The real launch command must consume the generated config without dropping
# source, parallel collection or the existing pre-shutdown snapshot signal.
grep -F 'backend_coverage_config "$venv_python" "$coverage_dir"' "$ROOT/singlebox/modules/backend.sh"
grep -F -- '--rcfile="$coverage_config"' "$ROOT/singlebox/modules/backend.sh"
grep -F -- '--save-signal=USR1' "$ROOT/singlebox/modules/backend.sh"
grep -F -- '--source="${community_src}"' "$ROOT/singlebox/modules/backend.sh"
printf 'PASS: Backend coverage retains real shutdown statements after SIGTERM\n'
