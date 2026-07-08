"""E2E tests for mock PaaS failure scenarios.

Tests verify system behavior when PaaS operations fail, using env vars
to configure MockPaasService failure modes at the server level.

Each test group requires specific env vars to be set when starting the server.
Run via justfile commands:
- just test-e2e-mock-failure-hook
- just test-e2e-mock-failure-create
- just test-e2e-mock-failure-destroy
- just test-e2e-mock-failure-device-not-found
"""
