# plugins/

Concrete implementations of the capability Protocols declared in
`agentclaw/community/plugin_api/`. The composition root selects a flavor at
bootstrap based on the deploy profile.

```text
plugins/
├── local/       singlebox / dev  (SQLite, in-memory cache, noop transports)
├── community/   the community distribution
└── http_client.py
```

## What belongs here

A component belongs in the plugin layer when **different runtime profiles need
different implementations**. Rule 20 makes that concrete: every plugin contract
carries paired local and prod implementations, and `test_plugin_pairing` enforces
it.

`http_client.py` is the one implementation at this level rather than inside a
profile package: `HttpxClient` is the shared default, paired with
`local/http_client.py` and overridden again by
`di/modules/infrastructure/test/http_client.py`.

## What does not belong here — repositories

Repositories used to live here, and no longer do. They failed the test above on
both counts: each had exactly one implementation, and the only per-profile
difference was the `DatabasePlugin` injected into the constructor — one layer
below, where the swap actually belongs.

They now live in `core/repository/`, grouped by domain:

```text
core/repository/
├── protocols/        the contracts, @abstractmethod throughout
└── implementations/  the ORM bodies, each declaring its Protocol as a base
```

Do not add a repository here. If you are adding a component and are unsure which
layer it belongs to, ask whether a *second* implementation would ever be selected
by profile. If the answer is no, it is not a plugin.

See `specs/2026-08-05-core-repository-consolidation/` for the move, and its
`path-map.md` for old → new module paths.

## Out of scope (owned by other modules)

| Capability | Where it lives |
| --- | --- |
| engine | `ocb/src/engine/` (Adapter module) |
| bcs | `ocb/src/bcs/` (Rust coordination service) |
| bcn | engine-side BCN plugin |

The backend only ever holds *client-side* contracts for these in
`agentclaw/community/plugin_api/`; their servers are owned by those modules.
