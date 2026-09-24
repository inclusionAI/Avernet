# Runner launch transport

ClawWeb calls the Runner through Message for ARCA and for both local Bot providers.
Online BaaS retains execute-command. Stage business contracts and pre/post execution
are unchanged by this transport protocol.

Message uses `bash <release>/clawevolve_async_runner.sh --launch-url '<signed URL>'
--launch-sha256 <SHA-256>`. ClawWeb first stores UTF-8 JSON in the host's existing
Artifact store under `runner-launches/<SHA-256>.json`. A GET-only signed URL expires
after one hour. Publication or signing errors fail dispatch; there is no inline
fallback. Storage is configured at the module composition root, alongside the
existing artifact bucket and public origin. Content addressing preserves distinct
HITL launches without overwriting earlier inputs.

The versioned platform-to-Runner transport envelope is:

```json
{
  "schemaVersion": "clawevolve.runner-launch.v1",
  "taskId": "EV-1",
  "stepId": "STEP-1",
  "stage": "clawevolve-hardening",
  "invocationId": "STEP-1:hitl:HITL-1",
  "runtimeMaintenance": false,
  "args": "--task-id EV-1 --step-id STEP-1 --goal '用户目标'"
}
```

The Runner's existing admin/local-process guard runs before download. The reader
permits HTTPS and, only in local-process mode, loopback HTTP. Redirects are rejected;
download is bounded to 128 KiB and a 30-second network timeout. SHA-256 is checked
before JSON parsing. Schema, IDs, stage, maintenance type, 64 KiB argument limit,
quoting, and exactly one matching Task/Step argument are validated. Failures do not
enter stage execution. Signed URLs are not included in download error messages.

After validation, code enters the existing `--stage / --invocation-id / --args-base64`
path with the original arguments and maintenance setting. The model never copies
the argument payload. Existing Runner locking, stale-launch checks, permissions,
HITL invocation deduplication and result callbacks remain authoritative. Copying a
reference incorrectly can still cause a rejected launch; it cannot silently alter
the verified arguments. This change does not remove the outer Message Agent.

The inline flags remain supported for online BaaS, stop commands and existing
callers. They cannot be combined with a frozen launch. A new ClawWeb deployment
requires the corresponding Runner release including `clawevolve_runner_launch.py`;
old Runner releases reject the new flags. No database migration or business Skill
change is required.

Conformance coverage: `tests/test_runner_launch.py`,
`tests/test_clawevolve_async_runner_release_layout.py`, and ClawWeb's dispatcher and
`runner-launch.test.ts` tests.
