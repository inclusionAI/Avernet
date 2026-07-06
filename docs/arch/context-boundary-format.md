# Context Boundary Format (Rule 22)

Every boundary-significant module under `src/backend/src/agentclaw/`
declares its context in a `README.md` next to the module's source, with
a fixed `## Context Boundary` section. The arch test
`tests/architecture/test_module_boundaries.py` parses these and
enforces the rules below.

## Section template

````markdown
## Context Boundary

```yaml
purpose: One-line statement of what this module is responsible for.
provides:
  - PublicClassName       # class names, Protocol names, exported callables
  - another_function
consumes:
  - DatabasePlugin        # external Protocols / Service APIs called into
  - CachePlugin
internal_dependencies:
  - agentclaw.plugins     # other agentclaw.* packages this module imports
  - agentclaw.core.auth   # prefix matching: agentclaw.core.auth covers
                          # agentclaw.core.auth.models etc.
  - agentclaw.log
```

### Change impact

Free-form prose: what's affected when this module changes? Who notices?
Cross-cutting consequences? One paragraph is plenty.
````

## Required keys

| Key                     | Type        | Notes                                                          |
| ----------------------- | ----------- | -------------------------------------------------------------- |
| `purpose`               | `str`       | One sentence. Non-empty.                                       |
| `provides`              | `list[str]` | Public surface — names only on day 1.                          |
| `consumes`              | `list[str]` | Informational. External Protocols this module depends on.      |
| `internal_dependencies` | `list[str]` | Authoritative whitelist of allowed `agentclaw.*` imports.      |

## Semantics

- **Whitelist**: `internal_dependencies` is the set of allowed inbound
  edges. Any actual import this module makes into `agentclaw.*` that is
  not covered by a declared prefix fails the arch test.
- **Prefix matching**: declaring `agentclaw.core.auth` covers every
  submodule like `agentclaw.core.auth.models`. Use the broadest prefix
  that captures intent.
- **Declared-but-unused is OK**: stale entries don't fail. Clean them
  up opportunistically.
- **`consumes` is informational**: not machine-checked today. Documents
  *intent* (which Plugin Protocols / Service APIs the module relies on)
  rather than imports.
- **`dependents` is derived**: the arch test builds the reverse view
  and writes `docs/arch/generated/dependents.md`. Don't hand-author it.

## Anti-patterns

- Listing concrete impl modules under `internal_dependencies` when a
  Protocol prefix would do (e.g., listing `agentclaw.plugins_impl.local`
  in a `core/` module — likely a layering violation).
- Stuffing the YAML block with low-level imports (`agentclaw.log`,
  `agentclaw.utils.env_utils`) instead of declaring `agentclaw.log` /
  `agentclaw.utils` once as a broad prefix.
- Writing `change_impact` as a list of files. The point is *who notices
  the change*, not what the change touches.

## Example (`agentclaw.core.auth`)

````markdown
## Context Boundary

```yaml
purpose: Authentication models and user identity types.
provides:
  - BuserviceUser
consumes:
  - AuthPlugin          # used by api/auth/dependencies.py via DI
internal_dependencies:
  - agentclaw.log
```

### Change impact

Adding fields to `BuserviceUser` ripples through every endpoint that
injects `get_current_user`. Renaming or removing a field is a breaking
change for all `api/*` routes and any background job that operates on
authenticated requests.
````
