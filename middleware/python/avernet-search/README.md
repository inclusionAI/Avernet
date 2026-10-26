# Avernet Search

Shared in-process search contracts and ranking. TC and Agent Search own their
business rules; this package does not import either application. HTTP support is
an optional extra. No database, model, environment or background task is opened
on import.

Install from this repository: `uv pip install -e 'middleware/python/avernet-search[http,test]'`.
Run this package's tests from its directory with `python -m pytest tests`.
Applications must install this package from the **same source revision** until
there is a published distribution; do not resolve an unpinned remote branch.

## Context Boundary

```yaml
purpose: Shared search contracts, pure ranking and optional HTTP delivery.
provides: [SearchApplication, RetrievalService, reciprocal_rank_fusion, create_search_router]
consumes: []
internal_dependencies: []
```

`api/` is the application-facing Service API; `core/` implements pure ranking;
`http/` translates the protocol and receives its application and authentication
dependency from a composition root. Provider-specific search execution is not
implemented in this first extraction. The shared HTTP contract is described in
`docs/search-api.md`; TC v1 remains a separate compatibility surface.
