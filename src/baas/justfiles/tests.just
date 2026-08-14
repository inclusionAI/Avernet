test-arch:
    source scripts/lib/test-stages.sh && run_arch_tests

test-ut:
    source scripts/lib/test-stages.sh && run_unit_tests

test-it:
    source scripts/lib/test-stages.sh && run_integration_tests

test-asgi:
    source scripts/lib/test-stages.sh && run_asgi_tests

test-e2e:
    source scripts/lib/test-stages.sh && run_e2e_tests
