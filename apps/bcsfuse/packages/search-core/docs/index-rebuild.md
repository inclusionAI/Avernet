# Shared index rebuild

`search-core` owns source repair, durable generation builds, immutable version
configuration, progress, retry and expired-owner continuation. Applications supply
their source data and document conversion. The shared implementation does not
import TC, Alipay, a platform SDK or an application Card model.

## Responsibilities

| Component | Responsibility |
| --- | --- |
| `core/index_builds.py` | Build execution, completion checks, retry/continuation and explicit completed-version selection |
| `core/source_rebuild.py` | Force preparation of one source, renewable ownership, guarded atomic publication and sanitized outcome logs |
| `config/index_builds.py` | Model, dimension, conversion version and filter fields bound to an immutable `index_key` |
| `providers/sql_index_builds.py` | Environment-scoped SQL task records using an injected DB-API connection factory |
| `adapters/http/index_builds.py` | Optional create/retry/status routes using an injected operator authenticator |
| Application | Read source revisions, convert business data into Docs, provide the atomic source/Doc publisher, and assemble model clients and runtime indexes |

## Business adapter

`IndexBuildService` takes an `IndexBuildSource`, an `IndexBuildRepository`, a model
factory, `conversion_version` and `filter_fields`. The source interface has three
operations:

- `page(after, limit)`: sorted source IDs and revisions, including deletion records.
- `prepared_revision(index_key, source_id)`: the source revision already published
  in this generation, or `None` when no artifact exists.
- `rebuild(source_id, config, model, lease_seconds)`: prepare and atomically publish
  this source's document group. Raise `IndexBuildConflict` for a changed source or
  competing producer; propagate all other errors. Never acknowledge a failed write.

The service reuses matching published revisions and verifies current source
revisions before marking a build completed. The model factory is a context manager
receiving model name and dimension; it owns model-client cleanup. Configuration
models use Pydantic, available through the package's `models` extra.

`rebuild_source` is also usable directly for a single-source repair. Supply a
`read_source(id)` callback returning a record with `revision`, and a
`prepare_documents(record)` callback that deliberately bypasses artifacts being
repaired. A missing source returns `None` and produces `KeyError`. Its publisher
provides `claim_preparation`, `renew_preparation`, `release_preparation` and
`publish_documents`: publication must check source revision and unexpired ownership
in the same transaction as writing the whole document group. The core never holds
a database transaction open while conversion/model work runs. Successful repair
returns `published`, `index_key`, and `saved_revision`; publication does not mean
every serving replica has applied it.

## Persistence and activation

The SQL task table remains `bcsfuse_index_builds`, with the existing
`(environment, build_id)` identity and serialized configuration. `SQLITE_SCHEMA`
is available for local host initialization; production provisioning belongs to
the deployment. Existing task/config serialization and `index_key` hashing remain
unchanged by this extraction.

A build writes independent Doc artifacts. It does not change any running query
model or Qdrant collection. `select(build_id)` only accepts completed builds with
the configured conversion version; the host binds model, dimension and collection
at startup and uses existing restoration/readiness before accepting traffic.
Source updates after completion are reconciled at startup and by normal sync.
No task, HTTP route, cleanup or background thread starts merely by importing these
modules. The host starts build polling explicitly. Old artifacts are not deleted.

## TC compatibility

TC's current tables, recommend/Fuse entrypoints, model defaults and task lifecycle
remain unchanged. To adopt this capability later, TC supplies its own source
adapter and conversion/publisher rather than importing an Alipay implementation.
The HTTP create/retry/status contract remains `PUT /api/v1/index-builds/{build_id}`
and `GET /api/v1/index-builds/{build_id}`; it is mounted only by opting-in hosts.
