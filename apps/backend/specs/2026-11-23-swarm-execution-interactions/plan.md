# Implementation slices

1. Read current Driver/Method/G1/G4/Event/HTTP/DI contracts and real SQLite tests.
   Establish that the existing queue `step` is not a one-Stage execution API.
2. Add transport-independent execution/interaction contracts, bounded stepping,
   per-axis/Collet coordination and atomic execution revision installation.
3. Add authenticated HTTP routes, graph/session/history projection, conservative
   injectable interaction planner and worker clarification/user reply journaling.
4. Test real DI/SQLite/artifacts, HTTP errors and envelopes, replay/concurrency,
   revision CAS, input waiting and late-report rejection. Evolve reserved Event
   schema tests and Service API conformance registry with the contract.
5. Promote the verified contract into L5 documentation/navigation. Compare broad
   failures with a clean baseline; record actual results and integration limits.

All slices are implemented locally. See [delivery](delivery.md) for verification;
no commit, merge, host integration or deployment is implied.
