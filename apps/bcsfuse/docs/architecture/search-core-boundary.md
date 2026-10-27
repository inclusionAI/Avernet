# Shared search: first extraction

The BCSFuse module owns the shared search package at
`apps/bcsfuse/packages/avernet-search`. TC and Agent Search install it as an
independent distribution; its location does not introduce a dependency on TC
business code. OCB supplies internal providers and deployment
composition. The core has no dependency on TC, Agent Search, OCB, HTTP, databases,
models or environment variables.

Both applications belong to the BCSFuse module:

```text
apps/bcsfuse/
├── packages/avernet-search/   Shared, independently installable package
├── src/                      Existing TC application, management and Fuse
└── agent-search/              Independently composed Agent application
    └── src/agent_search/
```

Agent Search keeps its own package and application factory. It depends on
`avernet-search`, and does not import the sibling TC `src` package. Internal
providers and deployment selection remain OCB's responsibility.

| Owner | Implemented in this batch | Intentionally retained / deferred |
| --- | --- | --- |
| Shared package | Neutral request/result contracts; reciprocal rank fusion; bounded union of ranked candidates; authenticated, optional HTTP v2 routes | Production asynchronous retrieval, provider adapters, fragment aggregation and reranker validation follow in the retrieval extraction |
| TC | Maps Profile candidates to the neutral result; uses shared ranking and selection algorithms; optionally mounts v2 | Full Profile identity, fragment type weights, eligibility, exact-ID priority, score normalization, Worker lifecycle, groups and Fuse remain TC behavior |
| Agent Search | Independent factory, option validation, trusted scope, same HTTP routes; synthetic catalog tested with shared ranking | Real Card conversion, storage, model calls, operations policies and updates are not implemented yet |
| OCB | Checks out and installs the shared package from the same Avernet revision as TC, including older revisions without the new package | No automatic enabling of v2 or Agent deployment in this batch |

The synchronous TC candidate service is bridged by an injected executor and a
bounded admission semaphore for v2. A timeout/cancellation does not release a
permit while synchronous work is still executing. The composition root owns
executor creation and shutdown; this is not a global thread-pool setting. Existing
v1 execution and business failure/degradation semantics are unchanged. Some legacy
providers already turn failures into empty results; the v2 adapter cannot recover
failure information that they discard. Explicit provider outcomes are deferred
to retrieval extraction, so v2 is not declared ready for production cutover.

Filters never set authority. Only the authentication callback supplies the
trusted allowed item IDs: `None` is explicitly unrestricted, an empty set means
no access. TC item IDs are complete Profile keys; Agent item IDs are opaque Agent
IDs. Application adapters must map permissions into these namespaces. Final ID
checks supplement the required retrieval prefilter.

Default v2 `search` and `recommend` share the same query-based candidate flow.
Personalized or no-query recommendation is not introduced implicitly. Unsupported
scenes, filters or extension keys return an error; future application capabilities
must define their own validated options rather than silently accepting a map.

## Compatibility verification

The migration baseline covers colon-containing Worker IDs, multiple Profiles,
exact-ID priority, thresholds, and preservation of fragment evidence. Existing
keyword and fragment-selection suites exercise the extracted algorithms through
TC. Core boundary tests prohibit application/framework imports; a fresh process
starts Agent Search with TC and internal SDK imports blocked. Synthetic catalog
tests prove shared ranking and independent application instances, not production
retrieval quality or throughput.

No index format, existing endpoint, default activation, or caller changes are
required. A full rollback must use the previous Avernet revision as a unit; the
new TC modules import the new package and cannot be copied independently.
