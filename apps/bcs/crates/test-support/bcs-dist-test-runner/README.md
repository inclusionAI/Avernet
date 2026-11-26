# Distributed Worker test Runner

Deterministic Runner, workflow directory and input fixtures shared by the
[Singlebox companion](../../bootstrap/bcs/tests/worker_runtime/README.md) and
explicitly enabled DEV composition roots. The Runner supplies scripted actions
such as finish, append, interaction and isolation. The caller selects storage
and drives the public Worker and repository ports.

Use these fixtures only with isolated test input and a test namespace. The
library depends on contracts and has no production defaults, storage or
deployment configuration.
