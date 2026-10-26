# Agent Search

An independently composed search application. The host supplies a real
`RetrievalService`, authentication and (when needed) an ASGI lifespan that starts
and closes its resources. There is deliberately no implicit in-memory catalogue,
TC dependency, model connection or database connection.

This first batch verifies the shared API and business boundary. Agent Card
ingestion, operational writes, durable synchronization, policies and production
retrieval are subsequent work; this skeleton is not a production search backend.
Unsupported filters, extensions and scenes fail explicitly. Empty authorized
scope is never interpreted as unrestricted access.

Install from the repository root:
`uv pip install -e 'middleware/python/avernet-search[http]' -e 'apps/agent-search[test]'`.
From this directory run `python -m pytest tests`.

## Context Boundary

```yaml
purpose: Translate Agent search intent and trusted scope to shared retrieval.
provides: [AgentSearchApplication, create_agent_search_app]
consumes: [RetrievalService]
internal_dependencies: []
```

`search/` owns business validation. `bootstrap/` selects the implementation and
mounts the common HTTP routes. Internal authentication, models and persistence
remain the OCB composition root's responsibility. Filter field allowlists must
match the actual provider; request filters cannot widen authenticated scope.
