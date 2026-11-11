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
| `QDRANT_MODE` | `local` for direct dev; `server` for runtime and Docker |
| `QDRANT_URL` | Required in Server mode; HTTP(S) URL, no embedded credentials |
| `QDRANT_PREFER_GRPC` | `true`; use `false` to compare REST transport |
| `QDRANT_GRPC_PORT` | `6334` |
| `QDRANT_TIMEOUT_SECONDS` | `10`; finite positive transport timeout |
| `QDRANT_API_KEY` | Optional external Server authentication; supply as a secret |
| `QDRANT_LOCAL_PATH` | Local mode only |

Common package callers pass `QdrantConnection` explicitly. Application adapters
own environment/config loading. The same candidate filtering and sparse token
weights are used in Local, REST and gRPC modes. Generic search has no implicit
Worker-ID lookup; the TC adapter retains its existing exact Worker-ID priority.

## Startup, updates and failure handling

- The TC durable database remains authoritative. Startup reconstructs both dense
  and sparse indexes, removing records absent from the database, before readiness.
  Existing Local index files are not copied into Server; stored vectors are reused
  without calling Embedding again.
- Dense vectors use the configured collection; sparse keywords use
  `<collection>_keywords`. Keep the two collections under the same owner.
- Server queries may overlap. Writes and rebuilds remain exclusive within the
  process, so a full rebuild can still delay queries. This change does not add
  lock-free rebuilds, cross-process synchronization or automatic version rollback.
- A failed durable write or index update is returned as an error. TC index update
  failure marks the keyword index unready until reconstruction succeeds.
- `/health` reports process liveness. `/ready` probes both Server collections and
  reports 503 on failure. Server connection failures never switch storage modes.
- Generic `QdrantRetrievalIndex` retains its Server collections across reopen;
  its owning application must replay failed writes and manage recovery. It does
  not implement a durable catalog or update scheduler itself.

Use one application process per owned index for this release. Multiple independent
writers must not share a collection pair: process-local locks do not coordinate
rebuilds across instances. A shared cluster and multi-worker update ownership need
a separate design and validation. Server mode alone does not establish capacity.

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
