# Qdrant deployment modes

| Deployment | Default | Durable records | Index ownership |
| --- | --- | --- | --- |
| Direct local `dev` / `dev_smoke` | Local | SQLite | One process, one local path |
| Public `runtime` | Server | MySQL | One application instance owns a collection pair |
| Public Docker / Compose | Server | SQLite in the smoke example; MySQL for runtime | Compose starts a separate Qdrant container |

FAISS is not an installed or maintained retrieval implementation. Existing FAISS
files are not imported automatically. TC Worker/Profile/Fuse behavior and HTTP
contracts are unchanged by the Qdrant selection.

## Configuration

Configuration is read at application composition, not inside the search algorithms.
Local development needs no Qdrant service. To test the Server path locally:

```bash
# Start an official Qdrant v1.20.0 service first, then run from apps/bcsfuse:
QDRANT_MODE=server QDRANT_URL=http://127.0.0.1:6333 \
  BCSFUSE_PROVIDER_MODE=dev_smoke python main.py
```

For the complete public smoke stack, run from the repository root:

```bash
docker compose -f apps/bcsfuse/docker-compose.yml up --build -d
```

The Compose example uses fake model providers; production model and database
settings remain separate. Qdrant ports are accessible only on the Compose network.
The app and Qdrant have separate persistent volumes. Never use `down -v` when
these volumes contain records you need to keep.

| Setting | Default / meaning |
| --- | --- |
| `QDRANT_MODE` | `local` for direct dev; `server` is required by runtime and Docker. Runtime assembly fails closed if it is explicitly set to anything other than `server` |
| `QDRANT_URL` | Required in Server mode; HTTP(S) URL, no embedded credentials |
| `QDRANT_PREFER_GRPC` | `true`; use `false` to compare REST transport |
| `QDRANT_GRPC_PORT` | `6334` |
| `QDRANT_TIMEOUT_SECONDS` | `10`; finite positive transport timeout |
| `QDRANT_API_KEY` | Optional external Server authentication; supply as a secret |
| `QDRANT_LOCAL_PATH` | Local mode only |
| `QDRANT_KEYWORD_URL` | Optional dedicated keyword/sparse Qdrant Server endpoint; omit it to use the dense endpoint |
| `QDRANT_KEYWORD_MODE` | Optional keyword cluster mode; defaults to `QDRANT_MODE` and must match it |
| `QDRANT_KEYWORD_PREFER_GRPC` | `true` for the optional keyword cluster |
| `QDRANT_KEYWORD_GRPC_PORT` | `6334` for the optional keyword cluster |
| `QDRANT_KEYWORD_TIMEOUT_SECONDS` | `10`; finite positive keyword transport timeout |
| `QDRANT_KEYWORD_API_KEY` | Optional keyword Server authentication; supply as a secret |

Common package callers pass `QdrantConnection` explicitly. Application adapters
own environment/config loading. The same candidate filtering and sparse token
weights are used in Local, REST and gRPC modes. Generic search has no implicit
Worker-ID lookup; the TC adapter retains its existing exact Worker-ID priority.

## Startup, updates and failure handling

- The TC durable database remains authoritative. Startup reconstructs both dense
  and sparse indexes, removing records absent from the database, before readiness.
  Existing Local index files are not copied into Server; stored vectors are reused
  without calling Embedding again.
- Dense and keyword Server collections create keyword payload indexes for the
  bounded v1 filter fields (`availability`, `runtime_state`, `domains`,
  `trust_level`, `worker_id`, `profile_key`) and for their internal external-ID
  fields. This avoids scanning every vector when default visibility filters are
  present. In the durable-lease composition, only the current owner performs this
  schema initialization; read-only replicas connect and probe without mutating
  Qdrant.
- Dense vectors use the configured collection; sparse keywords use
  `<collection>_keywords`. By default both collections use the same Qdrant
  service. Setting `QDRANT_KEYWORD_URL` (or the other `QDRANT_KEYWORD_*`
  settings) may move the sparse collection to a separate Server cluster. The
  optional keyword cluster must use the same Local/Server mode as the dense
  cluster; it may have a different URL, transport timeout, gRPC setting and
  credential. The durable lease still owns the logical dense/keyword collection
  pair, so both clusters publish one shared `index_version`.
- Server queries may overlap. Writes and rebuilds remain exclusive within the
  process, so a full rebuild can still delay queries. This change does not add
  lock-free rebuilds, cross-process synchronization or automatic version rollback.
- A failed durable write or index update is returned as an error. TC index update
  failure marks the keyword index unready until reconstruction succeeds.
- A startup rebuild failure no longer terminates the application. The process stays
  alive, records `startup_rebuild_failed=true` and
  `startup_rebuild_error_type=<ExceptionName>` on the vector-store view, and
  `/ready` returns 503 with `degraded=true`. The composed startup provider keeps
  retrying the durable rebuild in a bounded background cycle; a successful retry
  clears both failure fields instead of leaving a sticky readiness failure. The
  durable vector backend can also supply the process-local literal snapshot without
  probing either Qdrant index.
  The snapshot precomputes normalized profile text once per published index
  version so the bounded literal path does not repeat NFKC normalization for
  every query. Runtime composition probes dense and keyword readiness before
  accepting search traffic; an explicit dual outage preopens the terminal
  circuit so its admission slots cannot be consumed by dead or hanging Server
  calls. Terminal snapshot lookup then executes through a dedicated candidate
  worker pool instead of consuming the semantic worker budget.
  If both dense and lexical retrieval fail, the bounded literal path first serves
  token-overlap results; when none overlap, it returns a stable default profile
  set that still honors filters, exclusions, registry state and trusted allowed
  IDs. Search responses mark this terminal path with
  `literal_fallback_default_used=true` and result reason
  `literal_fallback_default`; it is never reported as a normal healthy result.
- `/health` reports process liveness. `/ready` probes the dense and keyword
  Server collections separately. Dense Qdrant is the hard serving dependency:
  a dense outage returns 503, while a keyword-only outage returns 200 with
  `degraded=true` and `keyword_qdrant_unavailable=true` because dense search and
  the bounded literal fallback remain available. Existing single-cluster
  deployments remain ready only when their collection probe succeeds. Readiness
  exposes `qdrant_mode` and `keyword_qdrant_mode`, but never endpoint URLs,
  provider errors or credentials. Server connection failures never switch storage modes.
- Generic `QdrantRetrievalIndex` retains its Server collections across reopen;
  its owning application must replay failed writes and manage recovery. It does
  not implement a durable catalog or update scheduler itself.

Runtime mode adds a shared durable ownership catalog in MySQL
(`bcsfuse_index_states`). Only the active lease owner may mutate a dense/keyword
collection pair; graceful shutdown releases the lease and an expired lease permits
failsafe takeover. The lease owner publishes a revision after each successful
backend write. Revisions are monotonically increasing; the durable state store
rejects an equal or lower revision, and an owner that later resumes after failover
resumes its next publication from the shared revision rather than a stale local
counter. Non-owner replicas are read-only and `/ready` reports them stale
until reconstruction or sync reaches the shared revision. Direct reader rebuild
and incremental sync calls are rejected before Qdrant; startup uses
`synchronize_read_view`, which only acknowledges the shared revision and reloads
the process-local literal snapshot. After startup, the composed readiness check
also performs this bounded revision sync once when a read-only replica observes
a newer shared revision; it does not rebuild or mutate Qdrant. State-store
failure is a 503 rather than a write success or empty 200; readiness exposes
`index_state_unavailable: true`, and mutations fail closed with stable code
`INDEX_STATE_UNAVAILABLE` rather than a misleading `INDEX_READ_ONLY`. Set `BCSFUSE_INDEX_OWNER_ID` to a
stable pod/application identity; BCSFuse appends the uvicorn-worker process ID so two workers in one
Pod can never co-own the lease. Configure
`vector.index.stale_after_seconds` (default 20) and
`vector.index.lease_seconds` (default 30). The default owner heartbeat runs
every ten seconds, so a failed owner can be taken over after the bounded
30-second lease window without waiting two minutes. Local/test modes remain
single-store and do not claim the state row. Readiness and metrics also expose
`index_owner_changes`, `index_owner_takeover_seconds`,
`bcsfuse_index_owner_changes_total`, and
`bcsfuse_index_owner_takeover_seconds`; owner transitions are emitted to the
business audit log without endpoint or credential details. Server mode alone
still does not establish capacity; each layer must continue to use the bounded
degradation chain and its own durability/HA deployment.

## Verification

Start an isolated Qdrant v1.20.0 with dedicated test storage, then run:

```bash
QDRANT_TEST_URL=http://127.0.0.1:6333 QDRANT_TEST_GRPC_PORT=6334 \
  uv run pytest packages/search-core/tests/test_qdrant_modes.py \
  tests/contract/test_qdrant_server_recovery.py -q
```

Tests create and delete uniquely named collections. They verify Local/REST/gRPC
filtering, updates and deletion; TC priority and durable recovery; query concurrency;
and error/readiness behavior. They do not measure production throughput or model
quotas. Without `QDRANT_TEST_URL`, remote integration cases are explicitly skipped.

## HA refactor TDD acceptance

The shared HA refactor follows these transport-agnostic contracts before a
multi-replica deployment is enabled:

- A collection pair has one owner. A coordinator rejects a second live owner and allows
  another replica to acquire ownership only after the prior lease is released.
- Shared `IndexHealth` metadata records the collection, owner, index version, update
  timestamp, stale threshold and read-only state; any replica computes the same version,
  lag and stale decision from that mapping.
- A read-only retrieval index rejects mutations before reaching Qdrant or durable
  storage, with a stable `INDEX_READ_ONLY` delivery error.
- Readiness and metrics expose owner, version, lag and staleness so routing can remove
  stale readers without treating an empty or stale result as healthy.
- Static literal/regex fallback is the last bounded path. It applies filters and trusted
  allowed IDs before limiting and never treats the caller query as an untrusted regex.

These contracts are executable in
`packages/search-core/tests/test_ha_refactor_tdd.py` and
`tests/contract/test_ha_refactor_tdd.py`; production composition may only ship
while those tests and the Qdrant Server composition tests pass.
