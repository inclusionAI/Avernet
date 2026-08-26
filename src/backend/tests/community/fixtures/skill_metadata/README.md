# Canonical SKILL.md parser fixtures

These fixtures pin the transport-neutral metadata parser contract for folder
uploads, Git imports, Draft validation, and publication validation. Consumers
should use the fixture bytes directly through `SkillMetadataParserProtocol`.

`boundary-limits.json` records persistence-safe limits, while the invalid
fixtures pin missing fields, empty values, invalid types, malformed YAML,
encoding and path failures to stable machine-readable error codes rather than
exception text.
