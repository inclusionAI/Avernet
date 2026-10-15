# Withdrawal operations / rollout

## Release gates — delivery remains OFF until all are satisfied

1. ECB confirms the versioned contract, service credential → tenant scope,
   durable intake ack, idempotency and deletion-before-upload tombstones.
2. Real integration proves response-loss retries, late READY suppression,
   A/B shared-content independence, and removed-reference denial in knowledge
   lists, summary and retrieval. Local tests do not prove these ECB behaviors.
3. Rehearse the additive SQL on the deployment's MySQL version. Verify both
   `ac_session_resource` and `ac_tc_resource_withdrawal` use transactional storage,
   writer UPDATE/INSERT are on the same database transaction, and concurrent
   claims/fencing work under the deployment's isolation level.
4. Assign TC/ECB owners, expected deletion-to-withdrawal SLA, oldest-event alert
   threshold and blocked/storage-error alert routing. None is implied here.
5. In a real Bot single-chat UI, capture its delete request/response and verify
   the matched event/ECB state. Static UI code and router tests are not browser
   evidence. Group chat is excluded; do not enable or modify its flow.

## Migration and deployment order

1. Back up/verify the database using the deployment's normal process. Apply
   `src/backend/src/agentclaw/community/core/session_resources/sql/ac_tc_resource_withdrawal.sql`
   **before** deploying TC, including when delivery is disabled. The table is
   additive, has no historical backfill and no shared-content mutation.
2. Check actual columns/indices after applying DDL: `IF NOT EXISTS` does not
   repair an incompatible table left by a previous manual installation.
3. Deploy TC with `enabled: false`. Successful new single-chat deletions now
   atomically create pending events, but cause no downstream call. Missing
   migration is a deletion failure, never silently ignored.
4. Observe stats for every relevant tenant. Configure one worker tenant and the
   credential belonging to that same ECB integration. A resource tenant is
   preserved, never replaced with the configured delivery tenant.
5. After the release gates, enable the worker and restart/roll the Backend.
   Config is read at process construction, not hot-reloaded. A small canary first
   should demonstrate receipt acceptance and independent ECB business state.

## Configuration

Top-level section in the same user config read by Backend's composition root:

```yaml
tc_resource_withdrawal:
  enabled: false
  base_url: ""       # ECB HTTPS origin only; no path, query or embedded credential
  secret_name: ""    # SecretResolver reference; never the value
  tenant: ""         # exact resource tenant, matched to the service credential
  timeout_seconds: 10
  lease_seconds: 60
  poll_seconds: 5
  retry_base_seconds: 10
  retry_max_seconds: 3600
  max_attempts: 20
```

Unknown keys/invalid types fail startup. Enabled mode requires all three strings.
Lease must exceed twice the transport timeout; a request that nevertheless
outlives its lease cannot save a stale result and may be delivered again.
Timeout is an HttpClient transport timeout, not a guaranteed total wall-clock
bound. Worker shutdown stops new claims and drains one in-flight call; process
termination leaves the event recoverable when its lease expires.

TEST/CORP_TEST fake is for tests only. Other profiles use real HTTP; no dummy
credential is accepted as a substitute by the resolver path. No new environment
variable reader is introduced; use the existing SecretResolver provisioning.

## Inspect / replay (trusted operator shell, not a public HTTP API)

Run inside the deployed Backend environment, with **the same profile, config,
secret setup and database** as the service. The CLI loads the normal composition
root but does not start an HTTP server or worker lifecycle. These are templates;
replace identifiers deliberately, never paste production credentials in shell
history. A CLI tenant argument is a scope filter, not an authentication system;
restrict OS/DB access to authorized operators.

```bash
python -m agentclaw.community.adapters.tc_resource_withdrawals stats \
  --tenant '<tenant>'
python -m agentclaw.community.adapters.tc_resource_withdrawals inspect \
  --tenant '<tenant>' --event-id 'tc.resource.withdrawn:<res_id>'
python -m agentclaw.community.adapters.tc_resource_withdrawals replay \
  --tenant '<tenant>' --event-id 'tc.resource.withdrawn:<res_id>' \
  --expected-attempts '<total attempts from inspect>' \
  --actor '<operator/change identity>' --reason '<fixed cause/change reference>' \
  --confirm
```

- Fix the root cause first (credential, endpoint/contract, receiver health).
- Only `blocked` events with matching tenant, ID and attempt count are changed.
  `accepted`, currently leased or pending events cannot be replayed.
- Exit 0 with `replayed: true` means re-queued, **not delivered**; exit 1 with
  `replayed: false` means the CAS did not match: inspect again, don't force SQL.
- Replay preserves event identity, age and lifetime attempts; the current retry
  count is reset. Actor/reason are mandatory and latest values persist. Keep the
  operator change ticket/output for a complete historical audit.
- Because config is validated eagerly, invalid enabled credentials can also
  prevent CLI bootstrap: repair the secret or run with delivery paused while
  preserving all other service config and the target database.
- There is no bulk replay/delete/purge command and no accepted-state reset.

## Observability and diagnosis

Worker logs use event codes `tc.withdrawal.paused`, `.delivery`, `.blocked`,
`.lease_lost`, `.storage_failure`, `.backlog`; backlog counts and oldest
unaccepted creation timestamp are logged every 60 seconds while running.
Counts distinguish pending/processing/blocked/accepted. The stats CLI works
while delivery is paused. Original age includes blocked events to avoid hiding
long-lived failures. No file content, response body or service token is logged.

| Signal | Action |
|---|---|
| pending rises while paused | Expected retention; size storage and complete enablement gates |
| `timeout`, `network_error`, 408/429/5xx | Restore downstream connectivity/capacity; bounded retry continues |
| `http_401`/`http_403` | Fix credential/integration scope; controlled replay only after verification |
| 3xx/other 4xx/`invalid_receipt` | Freeze/repair contract or origin; do not treat a redirect or 2xx alone as success |
| `retry_exhausted_*` | Inspect downstream + crash history; resolve cause before replay |
| `storage_failure` | Check table migration, privileges, connectivity and transactions; facts/leases remain durable |
| `lease_lost` | Inspect slow transport, clock/isolation assumptions and competing workers; old holder may not overwrite |
| accepted but knowledge still visible via removed A | Investigate ECB mapping/tombstone/access filtering; TC intake is not completion |

Alert thresholds and routing must be configured in the deployment's existing
observability system. This PR exposes logs/statistics; it does not provision an
alert service or invent an SLA. Never log secret values while diagnosing.

## Pause and rollback

Set `enabled: false` and restart workers; keep writer + table deployed. This is
the preferred reversible operational pause: new deletions continue recording
facts, and existing pending/blocked/processing/accepted rows remain intact.

Rolling TC back to a version without the writer stops recording **new** events.
Pause single-chat deletion during that interval or arrange an explicit audited
compensation plan before rollback; old code's successful delete is not evidence
of an event. Do not drop/truncate the outbox, reset accepted rows or purge shared
knowledge data. Redeploying resumes pending work and reclaims expired leases.
No automatic historical reconstruction is promised.
