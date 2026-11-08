# ADR-0013: Repository pattern with async SQLAlchemy and pluggable engines

## Status

accepted

## Date

2026-10-26

## Applies to

all

## Context

Every BaaS domain (bot, publish, device, session, tenant, api keys, queues,
locks, relay sessions…) needs persistence, and persistence is the layer most
coupled to the environment: the community must run dependency-free
(sqlite on a laptop, mariadb self-hosted) and the enterprise runs managed
MySQL via ZDAS behind the mesh. Services must not know which world they are
in, and repository protocols must be testable without a live database.

## Decision

Persist through per-aggregate repositories with a four-file quartet, on async
SQLAlchemy, with the engine selected by the `database` SPI (ADR-0003):

- each aggregate gets a repository package under `core/repository/<agg>/`
  with a stable quartet: `_orm_model.py` (SQLAlchemy table mapping),
  `_orm_repository.py` (query/insert implementations), `_protocol.py`
  (protocol the service depends on), `_record.py` (domain record shape) —
  e.g. `core/repository/publish/`;
- all repository I/O is async SQLAlchemy against the engine provided by the
  plugin container; services type-depend on `_protocol.py`, not on the ORM
  classes; tenant scoping lives here (ADR-0010);
- engine selection: community `plugins/database/{sqlite,mariadb}` (+ `seed`
  for demo data) vs enterprise `plugins/database/real` (ZDAS-managed MySQL);
  switch is `user_config.plugins.database` in `configs/application.yaml`;
- the same quartet pattern is repeated even for infra-relational state
  (`distributed_lock`, `ws_relay_session`, `file_transfer_ticket`), so
  persistence style is uniform across the codebase.

## Consequences

**Positive:** services are storage-agnostic and unit-testable against
protocols; swapping sqlite→mariadb→ZDAS is a config line; every aggregate's
storage surface is four files, review-predictable; ORM leakage into business
logic is structurally discouraged.

**Negative:** four files per aggregate is boilerplate for trivial stores;
async SQLAlchemy discipline is required everywhere (no blocking session use).

**Risks:** protocol/ORM drift when someone adds a method only to
`_orm_repository.py`; convention: new capability enters `_protocol.py`
first, `_orm_repository.py` implements it.

## Evidence

- community: `src/secbaas/community/core/repository/` — per-aggregate repository tree (bot, publish, device, distributed_lock, ws_relay_session, bot_run_queue, …)
- community: `src/secbaas/community/core/repository/publish/{_orm_model,_orm_repository,_protocol,_record}.py` — quartet exemplar
- community: `src/secbaas/community/plugins/database/{sqlite,mariadb,seed.py}` — community engines
- community: `configs/application.yaml` — `user_config.plugins` database switch
- enterprise: `src/secbaas/enterprise/plugins/database/real/` — ZDAS engine option