# ADR-0003: SPI-based plugin backend selection

## Status

accepted

## Date

2026-10-26

## Applies to

all

## Context

BaaS runs both as an open-source single-box service (needs file/sqlite backends
and stubs) and as an enterprise mesh service (needs ZDAS/ZCache/Buservice and
real credentials). Core logic must not know which world it is in, and the
enterprise backends must stay out of the open-source repo.

## Decision

Give every infrastructure capability a Service Provider Interface in
`spi/` and swappable implementations in `plugins/`, selected by config:

- contracts live in `community: src/secbaas/community/spi/<domain>/`
  (`secret`, `sandbox`, `auth`, `database`, `cache`, `runner`, `tracer`,
  `file_transfer`, `logger`, `http_header`, `bot`, `bot_service`, `eval_env`);
- implementations live in `plugins/<domain>/<option>/` (community ships
  `stub` and self-contained options like `sqlite`/`mariadb` databases and
  `docker`/`k8s` local sandboxes);
- the selection switch is `user_config.plugins.<name>` in
  `configs/application.yaml` (e.g. `auth: "stub"`), resolved by the plugin
  container in `bootstrap/`;
- the enterprise overlay contributes additional options — not forks — via
  `plugin_registry.register_plugin_option(...)`: deferred factories (so
  enterprise imports only happen when actually selected) injected into each
  `PluginContainer` selector at container creation
  (`real` database/cache/secret, `buservice` auth, mesh sandbox options).

Adding a backend = new `plugins/<domain>/<option>/` + one config switch.
Replacing a backend never touches `core/`.

## Consequences

**Positive:** one platform binary runs in both editions; enterprise secrets and
endpoints never appear in the OSS tree; local dev is stub-driven and fully
reproducible; contract-level conformance tests can target `spi/` directly.

**Negative:** every capability needs a contract/impl split (more files); a
mis-selected option fails only at composition time.

**Risks:** contract drift — if an implementation grows behavior beyond the
SPI, the enterprise option becomes un-testable in the community build; guarded
by keeping plugin protocols documented, versioned, and conformance-tested per
the monorepo's architecture rules.

## Evidence

- community: `src/secbaas/community/spi/` — contract tree (auth, secret, sandbox, database, cache, …)
- community: `src/secbaas/community/plugins/` — implementation tree (stub/sqlite/mariadb/docker/k8s, …)
- community: `src/secbaas/community/plugin_registry.py` — `register_plugin_option` deferred-factory registry
- community: `configs/application.yaml` — `user_config.plugins.auth: "stub"` switch
- enterprise: `src/secbaas/enterprise/plugins/{database/real, cache/real, secret/real, auth/buservice}` — enterprise-injected options