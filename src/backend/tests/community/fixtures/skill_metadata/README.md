# SKILL.md metadata fixtures

These fixtures define the stable parser boundary shared by the Skill capability
upgrade tests. Each directory models one package root and intentionally uses a
single, canonical `SKILL.md` path. `boundary-limits.json` drives exact-limit
payload generation, `invalid-utf8.hex` is decoded as bytes, and
`invalid-path.txt` models an out-of-root manifest path so every consumer can
reproduce non-text and boundary cases without relying on local filesystems.
