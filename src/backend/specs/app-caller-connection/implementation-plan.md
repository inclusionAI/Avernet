# Implementation plan — revised application policy

1. Update the spec to the explicitly approved policy: any verified app can access any existing caller instance in its tenant, including private bots and nonmember callers, without app grants.
2. Write failing service tests for two app identities and a private nonowner caller with no grants; retain tenant/Bot/instance rejection tests.
3. Remove only this task's grant/collaborator gates and now-unused constructor dependencies. Preserve the nonadmin existing-instance lifecycle and security logs.
4. Update Protocol/README, framework endpoint fixtures and live acceptance to prove no-grant app success and missing-instance denial. Keep the manifest endpoint and coverage thresholds.
5. Run focused API/domain/framework/architecture checks, update the code report, and create a local commit only. Parent agent owns remote push and CI.
