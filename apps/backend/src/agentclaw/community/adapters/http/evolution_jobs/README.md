# `adapters.http.evolution_jobs`

HTTP adapter for the 06-evolution-run worker Job Protocol.

It exposes the transport layer for worker claim, heartbeat, workspace-file,
candidate-submission, verdict and completion operations. Domain rules remain in
`core/evolution_run`; this package only maps HTTP requests to the injected
service ports.

The route is registered from the backend HTTP composition root and assembled
through the evolution DI modules. Validate HMAC/worker ownership and error
mapping with the focused evolution-run tests before changing the wire contract.
