# Cron detail absence contract

`GET /api/cron/{task_id}` retains the existing 404 `detail` string. A
confirmed missing job may additionally carry the top-level code
`cron.task_not_found.v1`. This marker is emitted only when the OpenClaw
gateway advertises `cron.get` and responds to that request with the exact
`INVALID_REQUEST` / `cron job not found: <task_id>` error. It assumes the
gateway and intermediate transport faithfully implement that protocol.

An unmarked 404 is inconclusive: it may be an unsupported endpoint, an old
runtime, or an upstream error. Consumers must not infer the job is missing
from the status or text alone. The public routines detail endpoint maps only
the marked response to a fixed 404; other transport failures retain their
existing error handling. Other operations and engines keep their existing
contracts. A gateway that cannot confirm absence may still return an existing
job via the established list fallback.

Deploy the Backend consumer before the adapter producer. Old adapters continue
to return an unmarked 404, which the public endpoint reports as a relay failure.
Removing either side restores the old behavior. The marker does not prove the
cause of an earlier incident whose response body was not recorded.
