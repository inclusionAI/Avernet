# Retired Backend Worker HTTP location

Backend no longer mounts `/openapi/v1/evolution/jobs` or installs legacy
Evolution execution modules. The old router/builders are removed, not silently
forwarded to a second in-process runtime.

Use `avernet_evolution.adapters.jobs.build_job_app` / `build_job_router` for
explicit test composition. Production Worker HTTP is mounted by the independent
[Evolution service](../../../../../../../evolution/README.md). Its HMAC claim and fencing
protocol is separate from the existing user login and Bot authorization.

All 58 success/stale-fence/missing-grant/empty-fence HTTP cases are now owned by
[Evolution Worker contracts](../../../../../../../evolution/tests/contracts/test_worker_http.py).
The actual service graph, provider calls, persistence and failure effects remain
asserted. These are local fixture tests, not live Worker or 02 provider proof.
Public Bot-scoped control paths remain in Backend's normal authenticated router;
see [control ingress](../../../../../../docs/flows/bot-evolution-control.md).
