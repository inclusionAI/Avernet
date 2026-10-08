# Evolve application configuration API

`ce_app_config` stores generic JSON configuration. Manage it through the API;
there is no configuration management page. These routes belong to Clawevolve
and do not read or write Workflow's `cm_app_config`.

## Authorization

All routes, including list and detail reads, require the host's `req.isAdmin`
to be `true`; otherwise they return **403**. A missing host authentication
middleware also denies access. Clawevolve does not parse a second administrator
list or trust an `isAdmin` request field.

The internal host uses its existing `auth.admins` YAML configuration and admin
authentication middleware. The host currently seeds configured administrators
into its dynamic permission repository and falls back to YAML when that
repository is unavailable. This API preserves that existing behavior. A
`claw_evolve_admins` role alone does not grant configuration management access.
Singlebox does not grant this role by default.

Send the same authenticated identity/cookies required by the deployed ClawWeb
host. A `config_key`, `updated_by` or administrator flag in the payload cannot
grant permission. `updatedBy` is recorded from the host request identity
(`X-User-Id` or `staff_id` cookie), when available, rather than a payload value.

## Endpoints

Base path: `/api/evolve/app-config`

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | List all entries; `?enabled=true` lists only enabled entries |
| GET | `/:configKey` | Read one entry |
| POST | `/` | Create an entry, enabled by default, version 1 |
| PUT | `/:configKey` | Update JSON, enable/disable or description |
| DELETE | `/:configKey` | Delete an entry |

Create body:

```json
{
  "config_key": "skill_task_stage_bindings",
  "config_json": "{\"bindings\":[]}",
  "description": "Skill task Stage defaults"
}
```

`config_key` is 1–64 characters with no leading/trailing whitespace.
`config_json` is a **string containing valid JSON**, matching the table field.
The API validates JSON syntax; each consumer owns its value schema. For Stage
bindings follow [the binding contract](./evolve-host-contract.md).

PUT accepts one or more of `config_json`, `description`, `enabled` (integer
`0` or `1`). Omitted fields remain unchanged; it cannot rename the key.
Unknown fields and malformed values return **400** without changing the row.

```json
{"enabled": 0}
```

Responses use camelCase: `id`, `configKey`, `configJson`, `version`, `enabled`
(boolean), `description`, `updatedBy`, `gmtCreate`, `gmtModified`. Create returns
**201**; duplicate keys return **409**; absent entries return **404**. Successful
deletion returns `{"affected":true}`. Database write failures remain errors.
`version` counts revisions; it is not a concurrency precondition or history.

Example using an existing authenticated cookie file:

```bash
curl --fail-with-body --cookie "$COOKIE_FILE" \
  "$CLAWWEB_URL/api/evolve/app-config"

curl --fail-with-body --cookie "$COOKIE_FILE" \
  -H 'Content-Type: application/json' -X POST \
  --data-binary @config-create.json \
  "$CLAWWEB_URL/api/evolve/app-config"

curl --fail-with-body --cookie "$COOKIE_FILE" \
  -H 'Content-Type: application/json' -X PUT --data '{"enabled":0}' \
  "$CLAWWEB_URL/api/evolve/app-config/skill_task_stage_bindings"
```

The Stage default reader loads current configuration on each request. Changes
do not require a restart; existing tasks keep their frozen bindings. Disabling
or deleting the binding entry removes defaults for subsequent requests. It
does not modify running tasks or delete any Skill/Stage implementation.
