# Clawevolve Skills

This directory is the source of truth for public ClawEvolve and ClawBench Skills.

- Each top-level directory containing `SKILL.md` has one source owner. Per-Skill `version` files are not used; releases use one bundle `release_version`.
- Runtime configuration is supplied through command arguments or environment variables; credentials must not be committed.
- Openversion ClawWeb uses this directory directly. It does not clone or copy a separate Skills repository.
- Internal-only Skills are maintained outside this public tree and are combined only in an internal release staging directory.

Validate the runtime entries with:

```bash
bash scripts/verify_public_skills.sh
```

Run the public regression suite with:

```bash
PYTHONPATH=. pytest -q tests scripts/tests
```
