# OpenClaw compatibility harness

This harness tests the in-repository `openclaw-channel-bcn` plugin against exact
published OpenClaw packages from the declared `openclaw.compat.pluginApi` floor
through npm's current non-beta `latest` release.

It deliberately keeps the fast plugin unit-test stub and the compatibility
proof separate. Each compatibility run:

1. installs an exact OpenClaw package in an isolated temporary directory;
2. imports the real SDK subpaths and required named exports;
3. copies plugin TypeScript sources without `src/typings/openclaw.d.ts` and
   records a non-blocking source type-drift diagnostic against the real SDK;
4. starts a deterministic OpenAI-compatible HTTP model and BCS WebSocket
   simulator;
5. starts a real OpenClaw gateway with the built plugin; and
6. verifies `chat.send -> model request -> exactly one final chat.event`.

The mock services bind only to loopback and require no model credentials.

## Commands

Run one or more exact versions:

```bash
scripts/openclaw_compat.sh --version 2026.3.28 --version 2026.7.1-2
```

Run the complete discovered matrix:

```bash
scripts/openclaw_compat.sh --max-workers 2
```

Run the fast harness tests without downloading OpenClaw packages:

```bash
python3 -m unittest discover -s scripts/compat/openclaw/tests -v
```

Default artifacts are written under
`scripts/.dependencies/compat/openclaw/`:

- `discovery.json`
- `results/<version>/result.json` and phase logs
- `reports/summary.json`
- `reports/summary.md`
- `reports/junit.xml`
- `reports/report.html`

The shared npm download cache is kept separately under
`scripts/.dependencies/cache/openclaw-npm/` so it is not included in CI report
artifacts. Override it with `--npm-cache` when needed.

`FAIL_SDK_ABI` identifies missing runtime SDK exports. A source-only type drift
is reported as `PASS_WITH_WARNINGS` because OpenClaw loads the built JavaScript
plugin rather than recompiling its source. `FAIL_LLM_PIPELINE` means OpenClaw
started but never reached the local model endpoint. Missing or
infrastructure-failed rows make the aggregate report incomplete and fail the
command.
