# Search architecture and implementation boundary

## Package layout

The TC application uses the Python package `app`. The independently installable
shared distribution is `search-core`, imported as `search_core`. Both are owned
by the public BCSFuse module. Enterprise-specific applications live in their
host repository and install the shared package without installing TC.

```text
apps/bcsfuse/
├── pyproject.toml                 TC application project
├── src/app/
│   ├── application/               TC search, management and Fuse workflows
│   ├── domain/                    Worker/Profile models and rules
│   ├── adapters/http/             TC delivery, including existing v1 APIs
│   ├── bootstrap/                 Public composition and startup
│   ├── infra/                     Existing TC infrastructure implementations
│   └── providers/                 Provider registry and lifecycle
├── packages/search-core/
│   ├── pyproject.toml             Independently installable library
│   └── src/search_core/
│       ├── api/                   Neutral requests/results and service contracts
│       ├── core/                  Ranking fusion and candidate selection
│       └── adapters/http/         Shared HTTP search/recommendation routes
├── tests/
└── docs/
```

`app` contains the complete TC application; `application` and `domain` are layers
inside it. TC-specific exact Worker ID priority, Profile fragment rules,
lifecycle, group behavior and Fuse remain in `app`. There is no second nested
TC application project and no enterprise Agent application in this public tree.

`search_core.api` and `search_core.core` import only standard-library code and
shared contracts. HTTP is an optional adapter. Importing the contracts opens no
model, database or background task. Public code cannot import an enterprise host.
The public TC entrypoint remains independently runnable with public providers.

## Ownership and current scope

| Owner | Implemented | Remaining work |
| --- | --- | --- |
| Shared package | Neutral search/recommendation contracts; reciprocal rank fusion; bounded candidate union; optional authenticated HTTP v2 routes | Extract actual asynchronous retrieval and model/storage contracts when used by both applications |
| TC `app` | Existing business code under its new namespace; neutral-result adapter; shared ranking and selection; optional v2 | Production retrieval still resides in TC; not all TC infrastructure is a shared library |
| Enterprise application | Host-owned application skeleton, option validation and trusted-scope handling using the same HTTP routes | Real Card conversion, storage, model orchestration, operational policies, updates and RPC |
| Enterprise composition | Separate TC and Agent factories; internal providers remain host-owned; core and TC staged from the same public revision | A separately deployable production Agent application and its lifecycle/configuration |

Future reusable storage/model contracts can live in `search_core.plugin_api`,
with public implementations in `search_core.providers`. Those directories and a
complete shared retrieval pipeline are not implemented by this directory move.
Private Card mapping, audience sources and operational rules belong to the host;
public core must not branch on a business or deployment name.

## Contracts and behavior

The shared search/recommendation contract covers common field semantics and
validated business extensions. It does not require every application to implement
TC Worker/Profile/Fuse management APIs. Application services own policy;
HTTP/RPC handlers translate requests, trusted identity and errors.

Only authentication supplies trusted allowed item IDs: `None` means explicitly
unrestricted; an empty set means no access. TC item IDs are complete Profile
keys, while Agent IDs are opaque. Retrieval applies the authorized scope before
ranking; final ID validation supplements that prefilter. Request filters and
extension fields cannot grant authority.

Default v2 search and recommend use the same query-based candidate flow.
Unsupported scenes, filters and extension keys fail validation; personalized or
no-query recommendation is not introduced by this refactor.

TC v2 bridges the synchronous candidate service through an injected executor and
a bounded admission semaphore. Cancellation or timeout does not release capacity
until underlying work finishes. The composition root owns executor startup and
shutdown. Existing v1 execution and degradation semantics are unchanged. Legacy
providers may still convert failures into empty results; explicit provider
outcomes remain part of later retrieval extraction, so v2 is not declared ready
for production cutover.

## Installation and compatibility

From `apps/bcsfuse`, run `uv sync`, or
`pip install -e packages/search-core -e .`. Public startup remains `python main.py`.
Python callers must use `app.*` and `search_core.*`; no `src.*` import alias is
provided. The business logger namespace also changes from `src` to `app`.
`LOG_LEVEL` behavior and existing HTTP paths remain unchanged.

An internal host must select the matching public layout. Its build checks
`src/app/bootstrap/app_factory.py` and stages the shared package from the same
immutable public SHA. Deploy the tested public/host pair; rollback uses the
previous complete image, not a mixture of old Python imports and new packages.
Applications can pin different tested core revisions and release independently.

The namespace migration changes no index format, model defaults, ranking rules,
HTTP route paths or default v2 activation. Contract tests retain colon-containing
Worker IDs, multi-Profile behavior, exact-ID priority, thresholds and fragment
evidence. Fresh-process tests check public startup without internal SDKs and
Agent startup without TC. Synthetic retrieval tests prove interface composition,
not production relevance, throughput or model capacity.
