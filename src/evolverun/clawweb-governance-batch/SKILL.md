---
name: clawinsight-batch
description: Run the bounded ClawInsight governance and verification batch through the shared AIStudio executor. Counts select investigation order; evidence and mechanical gates decide reviewable plans. The batch never approves, repairs, notifies users, or changes Bot/NAS state.
---

# ClawInsight batch package

This package is executed by the shared `clawweb-ais-base` entrypoint. It owns
only the bounded governance/verification policy and its independent batch
configuration; the shared entrypoint owns task lifecycle, NAS preparation,
artifact handling and callback reporting.

The package has no scheduler. A scheduled task supplies `input.mode` and
`input.batchConfigPath`. `dry-run` is the safe default. `apply` is allowed only
when the selected independent batch config has `allow_writes: true`; eligible
CREATE proposals are submitted as `ASSIGN_OWNER` items that stay pending admin
review. The process never calls approval, user notification, repair, stop-job,
or forced-close operations.
