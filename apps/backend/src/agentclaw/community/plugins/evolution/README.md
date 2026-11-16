# `plugins.evolution`

Runtime adapters for isolated RSI strategy execution.

- `sandbox_worker.py` adapts the platform sandbox runtime client to the
  evolution worker sandbox port.
- `job_http.py` implements the worker transport over the evolution Job HTTP API.
- `entrypoint.py` loads the registered worker strategies and drains assigned
  jobs using the explicitly allowed job endpoint, token and worker ID.

Strategy execution belongs in the isolated worker, not the backend process.
Keep credentials out of the worker environment beyond the narrowly scoped job
credentials, and keep platform SDK dependencies behind these adapters.

See [`core/evolution_run/README.md`](../../core/evolution_run/README.md) for the
host/worker lifecycle and [`docs/bot-evolution/README.zh-CN.md`](../../../../../docs/bot-evolution/README.zh-CN.md)
for the current overall status and known verification gaps.
