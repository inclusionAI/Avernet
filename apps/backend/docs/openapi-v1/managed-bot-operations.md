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
`claude_code` or `aicoding`. Both operations declare `AdmissionMode.ENGINE` in
`ADMISSION`, rather than `OPEN`. Each operation uses `AdmissionMode.ENGINE(...)` with
an engine allowlist and authorized Bot dependency provider, declared in
`admission_routes.py` in the shared inventory. The route-specific Bot dependency
providers remain in `admission_managed_bot_routes.py` to avoid importing the
managed Bot router while the inventory is assembled.
No engine decorator or handler metadata is required. `PublicAPIRoute` calls
`admission.apply_admission` to attach the configured check; the application
principal guard reads the same policy through `admission_for`.
ENGINE without a non-empty allowlist or Bot dependency fails startup, as do
engine parameters on another mode or different rules across one route's methods.
Each call stores an immutable copy of the allowlist for that operation; it does
not change the shared enum member. Existing modes such as `OPEN` and `USER_GATED`
remain parameterless. Extending a route to another engine requires changing its
`engines` argument, not adding a decorator or a second policy declaration.
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

Discovery filters before count/pagination in the database and projects only
display fields. It does not load whole snapshots or probe containers. Platform
environment differs from service stage: verification is `prestable`, production
publication is `online`, and the editable source is `draft`. A publication's
version is unknown unless that publication recorded its own snapshot version;
it never inherits the draft version. No schema migration or cache is required.
