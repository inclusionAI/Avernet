# Clawevolve Skills

This directory is the source of truth for public ClawEvolve and ClawBench Skills.

- Each top-level directory containing `SKILL.md` has one source owner. Per-Skill `version` files are not used; releases use one bundle `release_version`.
- Runtime configuration is supplied through command arguments or environment variables; credentials must not be committed.
- Openversion ClawWeb uses this directory directly. It does not clone or copy a separate Skills repository.
- Internal-only Skills are maintained outside this public tree and are combined only in an internal release staging directory.

## Business output validation

The platform executor validates the declared result JSON, explicit Markdown file
references, and the common interaction/feedback envelope before cleaning up the
business Agent. References resolve relative to the result JSON directory; a file
in a subdirectory must include that prefix (for example `work/material.md`).

After a successful Agent process with invalid output, the executor sends the
validation error to the same Agent and session for **one** output correction.
The correction must preserve business decisions and resources. It does not
create another Step, advance a feedback Loop, answer an interaction, or report
an intermediate result. The platform never guesses paths or repairs JSON itself.
Both attempts use the same strict validation. Failed output snapshots and
validation records are retained under `output-validation/<session-id>/`.

Process failures, timeouts, candidate boundary violations, and report failures
are not retried by this mechanism. Stage-specific business validation remains
with the native Handler and ClawWeb. On success the temporary Agent is cleaned
up; on failure it is retained with its transcript for diagnosis. This behavior
applies to business calls through the platform executor, including extension
Steps and selected replacements; native business implementations are unchanged.

For internal `local_proc` runs, Plan unregisters its temporary discovery Agent
through `agents.delete` with `deleteFiles: false`: the candidate workspace and
result files belong to the task. A failed unregister is recorded without a
destructive CLI fallback. Container and openversion cleanup remain unchanged.

Validate the runtime entries with:

```bash
bash scripts/verify_public_skills.sh
```

Run the public regression suite with:

```bash
PYTHONPATH=. pytest -q tests scripts/tests
```
