# BCN create registration implementation plan

**Goal:** Register every created Bot and route OpenClaw personal/desktop through plugin.
**Architecture:** Move shared registration ahead of BotService early returns. Wire Desktop creation through its existing lifecycle hooks and a deferred BotService provider; register after final local persistence so ext writes cannot erase the success marker. Keep the existing BCN HTTP service, credential checks and best-effort failure semantics.
**Spec:** 001-spec-output.md

1. Update creation tests to require registration for every engine/template, disabled DRM/capabilities, desktop and deferred creation. Run to observe missing attempts and wrong desktop mode.
2. Move registration into BotService.create_bot after capability initialization; remove provisioning-only registration and obsolete eligibility helper/imports. Extend resolver for desktop. Verify targeted create/start/restart tests.
3. Add desktop lifecycle tests observing real BotService registration through injected provider, including failure and safe diagnostic logs. Wire required provider through DI and existing hooks after binding persistence, skip retries already marked registered. Verify desktop and DI tests.
4. Run affected suites, lint and diff checks; record exact results and remaining independent full-suite validation in 002-code-report.md. Do not commit or push from this worker.
