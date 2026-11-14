# DingTalk CallerToken Implementation Plan

Goal: add an explicitly authenticated DeviceToken exchange branch while preserving IAM behavior.
Architecture: the HTTP adapter verifies the existing BaaS application principal; the application service resolves only the asserted caller instance and validates the verified upstream subject before reusing installation. A separate neutral provider capability leaves IAM implementations unchanged.
Spec: 001-spec-output.md

- [x] Add failing service tests for successful exchange, subject mismatch, missing identity, owner mismatch, target failure and upstream failure.
- [x] Add the neutral optional provider capability and implement service branch with no SSO fallback.
- [x] Add failing HTTP authentication/type/forwarding tests and implement HTTP dispatch.
- [x] Run affected API/core tests and architecture gates; record results.

Scope correction: remove all task-added BCN/BaaS wiring and related tests/report. Keep the Backend endpoint capability and existing signed application authentication. Verify an empty apps/baas PR diff and rerun unchanged caller-credential compatibility tests plus Backend/enterprise adapter regressions.
