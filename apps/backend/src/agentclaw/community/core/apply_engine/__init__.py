"""The apply engine: converge one bot to a typed desired state.

Everything that runs *after* a desired state exists lives here: the
``DesiredState`` model, the materialiser contract and the six materialisers,
the orchestrator, the delivery strategies and their ports, and the apply run
(lock, task, report). What produces a desired state lives elsewhere: the Bot
Config Manifest compiles a stored document, the Genome Registry compiles a
revision, both through ``DesiredStateCompiler``. This package never reads a
Manifest document and never fetches.

See ``README.md`` (the context boundary) and
``apps/backend/specs/2026-10-23-apply-engine-desired-state/``.
"""
