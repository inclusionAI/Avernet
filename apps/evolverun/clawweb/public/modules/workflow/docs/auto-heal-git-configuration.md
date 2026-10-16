# AutoHeal Git configuration

## Shared resolver and precedence

Public and internal AutoHeal routes use the workflow-owned resolver:

```ts
import { resolveAutoHealGitSettings } from "@avernet/workflow/server/config/auto-heal-git-config";
```

Its contract is:

```ts
resolveAutoHealGitSettings(
  db: IDatabase,
  fallbackGitConfig: ClawFlowsGitConfig,
  fallbackAuthor: GitAuthor,
): Promise<{ gitConfig: ClawFlowsGitConfig; gitAuthor: GitAuthor }>
```

Each call queries `cm_app_config` for `config_key = 'git'`. Only a row with
`enabled = 1` contributes overrides. Its YAML is a direct mapping of fields
(no enclosing `git:` key):

| DB YAML field | Result | Fallback supplied by bootstrap |
| --- | --- | --- |
| `remoteUrl` | `gitConfig.remoteUrl` | `autoHealConfig.clawflowsRemoteUrl` |
| `username` | `gitConfig.username` | `autoHealConfig.gitUsername` |
| `email` | `gitConfig.email` | `autoHealConfig.gitAuthorEmail` |
| `token` | `gitConfig.token` | `autoHealConfig.gitToken` |
| `authorName` | `gitAuthor.name` | `autoHealConfig.gitAuthorName` |
| `authorEmail` | `gitAuthor.email` | `autoHealConfig.gitAuthorEmail` |

Nonempty DB strings win **per field**. URL and identity strings are trimmed
before selection; tokens are never trimmed. Empty or non-string fields use
fallbacks. Git username/email do not implicitly replace commit-author fields.
Bootstrap's `resolveAutoHealConfig()` supplies environment-over-`application.yaml`
fallbacks, preserving the existing mapping.

Absent, disabled, empty, non-mapping, or malformed YAML uses fallbacks.
Malformed YAML produces only a generic warning: parser messages may contain
credentials and must not be logged. Database query failures propagate rather
than silently selecting fallbacks.

## Public and internal integration

The public bootstrap passes only environment/YAML fallbacks to
`createAutoHealRouter`; it does not read or cache the DB Git row. Inside each
save/deploy handler's `try` block, resolve settings immediately before creating
`AutoHealSaveService` or `AutoHealDeployService`, then pass `gitConfig` and
`gitAuthor` to that service. The sibling internal route must use the same import
and per-request sequence; it must not rely on the public bootstrap.

DB changes take effect on the next save/deploy request, including token rotation
and disabling a row. An in-flight request keeps its resolved snapshot. Changes
to bootstrap environment/YAML fallbacks still require restarting the host.

If the merged remote URL is missing or whitespace-only, resolution throws an
`Error` with `status = 503` and `error = "Service Unavailable"`. Configure
`remoteUrl` in the enabled `cm_app_config` Git row (`git.remoteUrl`),
`AUTO_HEAL_CLAWFLOWS_REMOTE_URL`, or `autoHeal.clawflowsRemoteUrl` in
`application.yaml`. Handlers must preserve this response and reject **before**
service construction, temporary directories, Git operations, deployment-history
reservation, workflow writes, or auto-run dispatch.

## Build and deployment order

Package subpath exports resolve to `dist`, not source. Build workflow dependencies
first, then `@avernet/workflow`, then the public app and the separately maintained
internal host. Deploy the updated workflow artifact before or together with
hosts importing the new resolver; restart those hosts to load the new code.
A DB edit alone cannot update an older host that still caches startup settings.
Focused local tests may alias package imports to source, but those tests do not
replace checking the built resolver export before deploying either host.

The app-level `server/auto-heal-config.ts` remains a compatibility re-export of
`resolveAutoHealGitConfigFromDb` and its `AutoHealDbGitConfig` type (and also
exports the merged resolver). Existing router dependency and service constructor
signatures are unchanged. No database migration or new configuration key is
required.
