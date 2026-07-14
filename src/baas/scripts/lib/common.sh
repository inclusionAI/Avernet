# Shared configuration, colors, logging, and helpers.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BAAS_DIR="$PROJECT_DIR"
WORKSPACE_DIR="$(cd "$PROJECT_DIR/../.." && pwd)"
REPORT_DIR="$BAAS_DIR/pytest_report"
LOG_FILE="$BAAS_DIR/tmp/test.log"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

FAILED_STAGES=()
FAILED_TESTS=()

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_stage() { echo -e "\n${CYAN}════════════════════════════════════════════════════════════${NC}"; }
log_sub()   { echo -e "${CYAN}---${NC} $*"; }

_init_log() {
    mkdir -p "$(dirname "$LOG_FILE")"
    : > "$LOG_FILE"
}

_elapsed() {
    local secs="${1:-$SECONDS}"
    if (( secs >= 60 )); then
        echo "$((secs / 60))m $((secs % 60))s"
    else
        echo "${secs}s"
    fi
}

_clean_skipped_from_report() {
    local xml_file="$1"
    if [[ ! -f "$xml_file" ]]; then return 0; fi
    python3 -c "import xml.etree.ElementTree as ET,sys
t=ET.parse('$xml_file'); r=t.getroot()
for s in r.findall('.//testsuite'):
  sk=0
  for c in list(s.findall('testcase')):
    if c.find('skipped') is not None:
      s.remove(c); sk+=1
  s.set('tests',str(int(s.get('tests',0))-sk))
  for a in ('skipped','skip'): s.attrib.pop(a,None)
r.set('tests',str(sum(int(s.get('tests',0)) for s in r.findall('testsuite'))))
t.write('$xml_file',encoding='utf-8',xml_declaration=True)" 2>/dev/null || true
}

_run() {
    echo "  $ $*"
    set +e
    "$@"
    local rc=$?
    set -e
    return $rc
}

# Run a pytest command, tee output to the shared log file, and collect failures.
_run_pytest() {
    mkdir -p "$(dirname "$LOG_FILE")"
    echo "  $ $*" | tee -a "$LOG_FILE"
    set +e
    "$@" 2>&1 | tee -a "$LOG_FILE"
    local rc=${PIPESTATUS[0]}
    set -e
    return $rc
}

_summary() {
    local elapsed_fmt="$(_elapsed "$1")"
    echo ""
    echo "==========================================="
    echo "          BAAS CI Test Summary"
    echo "  Duration: $elapsed_fmt  |  Log: $LOG_FILE"
    echo "==========================================="

    local log_failures=0
    FAILED_TESTS=()
    if [[ -f "$LOG_FILE" ]]; then
        local tempfile
        tempfile=$(mktemp /tmp/ci-failures.XXXXXX 2>/dev/null || echo "/tmp/ci-failures.$$")
        grep 'FAILED.*::' "$LOG_FILE" > "$tempfile" || true
        while IFS= read -r line; do
            FAILED_TESTS+=("$line")
            ((log_failures++))
        done < "$tempfile"
        rm -f "$tempfile"
    fi

    local total_failures=$(( ${#FAILED_STAGES[@]} + log_failures ))

    if [[ $total_failures -eq 0 ]]; then
        echo -e "${GREEN}All stages passed!${NC}"
        return 0
    fi

    echo -e "${RED}FAILED: ${#FAILED_STAGES[@]} stage(s), $log_failures test(s)${NC}"

    if [[ ${#FAILED_STAGES[@]} -gt 0 ]]; then
        echo -e "${RED}Failed stage(s):${NC}"
        for stage in "${FAILED_STAGES[@]}"; do
            echo "  - $stage"
        done
    fi

    if [[ $log_failures -gt 0 ]]; then
        echo -e "${RED}Failed test(s):${NC}"
        for test_line in "${FAILED_TESTS[@]}"; do
            echo "  $test_line"
        done
    fi

    return 1
}