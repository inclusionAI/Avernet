# Worker runtime application

`WorkerRuntimeApplication` implements the inbound `DistWorkerRuntimeService` over
an injected `DistRuntimeControlPort`. Delivery adapters must obtain the caller's
exact `Instance` from authentication. The application rejects a different source
instance or an obsolete target incarnation before dispatching a handoff.

The engine remains independent of application and HTTP types. Requests received
while draining can still confirm an existing handoff; new acceptance decisions
belong to the engine's admission gate. A successful notification does not imply
that any session has been subscribed or its outbox completed.
