# ── Shared test helpers ─────────────────────────────────────────────────

_init_log log_file='tmp/test.log':
    mkdir -p "$(dirname {{log_file}})"
    > "{{log_file}}"

_test_summary log_file='tmp/test.log' failed_groups='0' elapsed='0':
    #!/bin/bash
    if (( {{elapsed}} >= 60 )); then
        elapsed_fmt="$(({{elapsed}} / 60))m $(({{elapsed}} % 60))s"
    else
        elapsed_fmt="{{elapsed}}s"
    fi

    # Detect actual test failures in log — pytest writes "FAILED <path>::<test> - <error>"
    # This catches individual test failures that are masked by `|| true` in test runners.
    log_failures=$(grep -c 'FAILED.*::' "{{log_file}}" 2>/dev/null)
    log_failures=${log_failures:-0}

    echo ""
    echo "========== Test Summary =========="
    echo "Duration: $elapsed_fmt  |  Log: {{log_file}}"
    if [[ {{failed_groups}} -gt 0 || $log_failures -gt 0 ]]; then
        echo -e "\033[0;31mFAILED: {{failed_groups}} group(s), $log_failures test(s)\033[0m"
        exit 1
    else
        echo -e "\033[0;32mAll tests passed!\033[0m"
        exit 0
    fi
