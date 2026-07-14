test-e2e-baseline mode='bare' overlay='e2e-sqlite':
    source scripts/lib/test-stages.sh && run_e2e_baseline {{mode}} {{overlay}}

test-e2e-mock-hook mode='bare' overlay='e2e-sqlite':
    source scripts/lib/test-stages.sh && run_e2e_mock_failure_hook {{mode}} {{overlay}}

test-e2e-mock-create mode='bare' overlay='e2e-sqlite':
    source scripts/lib/test-stages.sh && run_e2e_mock_failure_create {{mode}} {{overlay}}

test-e2e-mock-destroy mode='bare' overlay='e2e-sqlite':
    source scripts/lib/test-stages.sh && run_e2e_mock_failure_destroy {{mode}} {{overlay}}

test-e2e-mock-device-not-found mode='bare' overlay='e2e-sqlite':
    source scripts/lib/test-stages.sh && run_e2e_mock_failure_device_not_found {{mode}} {{overlay}}

test-e2e-full mode='bare' overlay='e2e-sqlite':
    source scripts/lib/test-stages.sh && run_e2e_tests {{mode}} {{overlay}}
