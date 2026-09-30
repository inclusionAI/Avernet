# Implementation Plan

## 1. Contract and boundaries

- Define an immutable three-field `TcResourceReadyEvent` beside an outbound
  `TcResourceReadyPublisherPort`.
- Expose the inbound observation shape as `TcResourceReadyObserverProtocol` and
  re-export it from the Service API package.
- Move the concrete HTTP endpoint/client behavior into an HTTP adapter.

## 2. Bounded coordination

- Replace upload-intent context capture with READY-only observation.
- Reserve resource IDs before task creation to suppress concurrent duplicates.
- Cap in-flight work and store completed attempts in a TTL/LRU bounded cache.
- Log and contain scheduling and publication failures.

## 3. Production entrypoints

- Invoke the observer after legacy upload completion and status lookup.
- Invoke it after an applied internal materialization callback.
- Invoke it after formal OpenAPI session-file completion and status lookup.
- Remove sidecar-only browser context fields from the upload-intent schema.

## 4. Wiring and governance

- Add typed config for timeout, executor size, in-flight cap, TTL, and capacity.
- Bind the HTTP publisher to the outbound port and the coordinator to the
  observer protocol in the composition root.
- Update context-boundary metadata, conformance registries, and the truthful
  temporary Singlebox exemption.

## 5. Verification

- Add event, coordinator, HTTP publisher, legacy router, OpenAPI adapter, DI,
  configuration, port, Service API, and module-boundary tests.
- Run focused tests, Backend SAST/unit/changed-line coverage, and unified
  Singlebox coverage before amending and pushing PR #2218.
