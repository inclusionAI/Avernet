# Context boundary

- Role: pure implementation of `OutputProjectionCoreService`.
- Owns: deterministic JSON/text/parts projection, ordered display encoding,
  exact logical output byte accounting and frozen profile checks.
- Consumes: data contracts from `bcs-dist-model` and the public Service API.
- Does not own: reference IO, event identity deduplication, checkpoint writes,
  timers, terminal decisions, archive, provider commands or runtime assembly.
- Callers: the Worker processor and TaskFeed provider through the core trait.
- [Contract, limits and verification](README.md). Changes affect event folding,
  draft generation, restored views and archive snapshot interpretation together.
