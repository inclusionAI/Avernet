# Q7 Legacy Skill Compatibility Harness

This harness freezes the Legacy baseline before any Space/Center feature is
enabled. It deliberately creates `skills-repo`, `skills-local`, Bot-local and
`skill-center` under pytest's `tmp_path`; it never reads a developer workspace,
pre/prod database, or historical Bot data.

Run the fast baseline and report checks:

```bash
cd src/backend
uv run pytest tests/community/compatibility/test_legacy_skill_harness.py -q
```

Run the acceptance entrypoint (it is isolated and does not boot a backend):

```bash
RUN_ACCEPTANCE=1 uv run pytest tests/community/acceptance/legacy_skills/ -q
```

The matrix is intentionally limited to the supported combinations in final
Spec §13. Each non-Teclaw fixture asserts Legacy source locators and active
links; Teclaw asserts the unchanged v4 Legacy Artifact shape. Center content
is deliberately a sibling corpus and never converts a Legacy locator.

Use [legacy_skill_release_report.template.md](legacy_skill_release_report.template.md)
for release evidence. Any missing, failed, or blocked matrix cell is a hard
publish blocker.
