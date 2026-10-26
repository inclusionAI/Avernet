# plugins/

Concrete implementations of the capability Protocols declared in
`agentclaw/community/plugin_api/`. The composition root selects a flavor at
bootstrap based on the deploy profile.

```text
plugins/
├── local/       singlebox / dev  (SQLite, in-memory cache, noop transports)
├── community/   the community distribution
├── unavailable/ explicit unavailable capability implementations
├── http_client.py
└── local_skill_storage.py
```

## What belongs here

A component belongs in the plugin layer when **different runtime profiles need
different implementations**. Rule 20 makes that concrete: every plugin contract
carries paired local and prod implementations, and `test_plugin_pairing` enforces
it.

Shared implementations may live at this level: `HttpxClient` in
`http_client.py` is the default HTTP client, overridden by the test
infrastructure column. `local_skill_storage.py` supplies the configured and
community-runtime Local Skill storage resolvers. Read the bindings under
`di/modules/` to determine which implementation a profile actually selects.

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

See [core repository](../core/repository/README.md) for the current contracts
and bodies. Historical migration specs explain the move, not current paths.

## Neighboring runtimes

Backend plugins provide client capabilities; they do not own engine or BCS
servers. The public adapter lives at repository `engine/adapter/`, and public BCS
at `apps/bcs/`. Enterprise runtime ownership and forwarding belong to the host's
current integration contract; do not infer them from old `ocb/src/*` paths.

Start at the [Backend development guide](../../../../README.md) for authority,
profile selection and checks. Enterprise implementations are supplied by the
host's `agentclaw.corp` package, not copied into this public tree.
