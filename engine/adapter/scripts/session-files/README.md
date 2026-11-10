# Session file publisher

The canonical runtime Skill, standalone CLI, Chinese installation guide and
standalone tests are maintained in the `security_release/aiworkbench` repository:

- Runtime package: `skills/infra/session-file-publish/`
- Repository: https://code.alipay.com/security_release/aiworkbench

Install and upgrade the Skill and CLI together from that repository. Start a new
Chat after replacing the installed package. This directory does not distribute
a second implementation.

Avernet owns the Engine `POST /api/session-files`, list and content contracts.
Contract tests live in
`engine/adapter/src/engine/community/api/session_files/tests/`.
The publisher requires per-exec runtime session context; installing the Skill
alone does not provide that context or guarantee model invocation.
