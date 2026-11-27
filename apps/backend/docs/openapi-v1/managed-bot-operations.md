# Managed Bot operations

These application-only operations require a verified application identity on the
deployment's `openapi.bot_operations.allowed_apps` list. Each entry must contain
exact `env`, `tenant`, and `app_name` strings. The empty default refuses everyone.
The existing Owner/delegated-grant restart and status routes are unchanged.
Mixed application-and-user principals are refused even when the application is
listed; these operations are for backend application credentials only.

| POST path under `/openapi/v1/bots` | Purpose |
| --- | --- |
| `/template-queries` | Discover paginated display-safe runtime rows by `template_key` |
| `/template-config-for-others` | Read the complete draft snapshot for server-side merging |
| `/restart-for-others` | Restart a checked draft address, optionally with a complete merged snapshot |
| `/status-for-others` | Read restart progress for at most 20 exact draft addresses |

The application is the caller; `owner_id` is a target address, not an assertion
of the calling human's identity. The trusted caller must enforce its own operator
authorization before issuing a restart. The stored template association, tenant,
environment and current Owner remain checked on this side. Desktop and separately
managed local runtimes are not supported. Published runtimes are read-only.

Restart and batch status accept only Bots whose persisted `active_engine` is
`claude_code` or `aicoding`. Both operations declare `AdmissionMode.ENGINE_CODING`
in the shared `ADMISSION` inventory in `admission_routes.py`, rather than `OPEN`.
The enum member owns the shared engine allowlist; inventory values are always
bare `AdmissionMode` members. The route-specific authorized Bot dependency
providers are wired in `admission_managed_bot_routes.py` and resolve lazily to
avoid importing the managed Bot router while the inventory is assembled.
No engine decorator or handler metadata is required. `PublicAPIRoute` calls
`admission.apply_admission` to attach the configured check; the application
principal guard reads the same policy through `admission_for`.
An engine-scoped route without an authorized Bot dependency fails startup, as do
different modes or Bot dependency providers across one route's methods.
The allowlist is a `frozenset` shared by all routes using the same enum member.
Existing modes such as `OPEN` and `USER_GATED` carry empty engine sets.
Extending the coding policy requires changing `ENGINE_CODING`'s engine tuple.
A route needing a different range would use a separate named enum member with
its own engine tuple; the admission gate reads the selected member's engines.
No wrapper class, callable mode or per-route engine decorator is required.
Other operations' admission and authorization policies remain unchanged.
The application trust and stored template scope are checked before engine
support. Other engines, including missing or unknown engine values, return
HTTP 501 / `501000` / `Not supported for this engine` using `ErrorEnvelope`.
A batch containing any resolved unsupported Bot is refused as a whole; missing
or out-of-scope targets retain the existing omission behavior. The status
handler reuses the aspect's request-cached batch result, so the check adds no
queries to status polling. Discovery and snapshot reads are not engine-limited.

```yaml
openapi:
  bot_operations:
    allowed_apps:
      - env: dev
        tenant: example-tenant
        app_name: example-manager
```

All responses use `Envelope`; list/status responses use `Envelope[Page[T]]`.
Restart returns HTTP 200 / `200000` / `OK` for acceptance or observation of an
active operation, not completion. Supply `extra_configs.template_config` and
`extra_configs.confirmed_template_update: "true"` and the snapshot read's
`extra_configs.expected_template_config_hash` together to apply a complete
snapshot. Persistence validates its hash and non-downgrade policy under the
existing template row lock inside `transactional_orm_session`, not the legacy
autocommit session; stale writes return 409. Preparation or persistence failure
aborts the guarded update, including recovery of an unbound Bot. An already active restart
still follows the existing deduplication path without replacing its snapshot.
The caller owns version selection and merging. Authorization resync freezes its
originating context snapshot before scheduling the worker (legacy callers without
one freeze their persisted snapshot synchronously) and conditionally removes only that snapshot's
marker, so an older cleanup cannot overwrite a newer configuration or clear a
newer operation's marker. Never forward the
snapshot response or application credentials to a browser.

The submission response includes `ext.aicoding.restart` only when the lifecycle
explicitly identifies the admitted or reused operation and its ID matches the
projected journal. An activation still in progress may return no restart receipt;
an older terminal journal is not evidence of this submission's completion.
The status query may still report the latest journal independently.

`ext.aicoding.restart` retains the existing `operation_id`, `status`, `phase`,
`message` and optional `warning`. Missing operation data means the extension is
absent. A failed operation is still a successful status query; an ACTIVE Bot may
have a failed backup-blocked restart. Enabled boolean `fastdisk_fail_block=true`
blocks on backup failure; otherwise a confirmed failure may continue with
`warning.code=backup_failed_continue`, including after success. No locks, backup
mechanisms, queues or operation journals are replaced by these adapters.

Duplicate active submissions reuse the current operation where the existing
engine lifecycle supports that behavior. Terminal operations release the active
key, so clients must not automatically retry an ambiguous network failure.
Poll batches every ten seconds, retain warnings, and correlate operation IDs.
Unknown or no-longer-associated status targets are omitted; storage errors fail
the query rather than masquerading as an empty success.
Each status batch uses one exact-address, tenant, environment and template-scoped
query. It projects readiness and public restart fields without loading complete
template snapshots, raw Bot configuration or device properties.

Discovery lists non-deleted source Bots in `ACTIVE` or `FAILED` state, scoped to the
requested template and deployment. This visibility rule does not apply to snapshot
reads or restart status polling. For service Bots, `draft`, `prestable` and `online`
are distinct rows; only the draft can be restarted through these operations.

All stages of a Bot display the same saved template version ID, label and optional
`template_status` (`bot_template_config.status`) from the source Bot's template record.
This is not the running container version or the service publication's own version.
Legacy metadata can be null. Search, stage and outdated filters apply before total
and pagination; an out-of-range page still returns the filtered total. Discovery
returns display fields only, without per-Bot reads or container probes.
