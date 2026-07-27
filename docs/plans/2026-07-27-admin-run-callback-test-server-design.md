# Admin Run Callback Test Server Design

## Goal

Provide a dependency-free local HTTP server for inspecting and testing callbacks
sent to a Provider's `admin_callback_url`.

The server is a developer test utility. It does not initiate admin runs or
change the production callback contract.

## Location and Runtime

Add the server under `src/bcs/scripts/` and implement it with Python standard
library modules only. By default it listens on `127.0.0.1:28081`, so it is not
exposed to other machines.

The callback URL configured on the Provider is:

```text
http://127.0.0.1:28081/callback
```

Local BCS configuration must allow private-network callback targets when this
utility is used.

## HTTP Interface

| Method | Path | Behavior |
| --- | --- | --- |
| `POST` | `/callback` | Validate, record, print, and acknowledge a callback |
| `GET` | `/callbacks` | Return all recorded callbacks and duplicate counts |
| `GET` | `/callbacks/{run_id}` | Return callbacks recorded for one run |
| `POST` | `/reset` | Clear all in-memory callback records |
| `GET` | `/health` | Return server health and callback count |

Unknown paths return `404`. Invalid callback JSON returns `400` and is not
recorded as a valid callback.

## Validation and Recording

Each captured callback records:

- receive timestamp;
- HTTP method and path;
- request headers;
- parsed JSON body;
- callback `run_id`;
- whether the same `run_id` was seen previously.

Optional startup arguments validate:

- `Authorization: Bearer <expected token>`;
- `X-BCN-Provider-Id: <expected provider id>`.

A token mismatch returns `401`; a Provider ID mismatch returns `403`. When an
expectation is omitted, that validation is disabled so the server remains easy
to use for initial inspection.

## Response Simulation

The server returns `200` by default. Startup arguments can change:

- callback response status to any valid HTTP status;
- response delay in milliseconds.

Validation errors take precedence over the simulated status. Query, reset, and
health endpoints continue to return their normal statuses.

This supports checking successful acknowledgement, non-2xx handling, and slow
receivers without changing server code. BCS currently logs failed callback
delivery and does not retry it.

## Output and State

Callbacks are held in memory and lost when the process exits. Every accepted
callback is printed as readable JSON to stdout, with the Authorization header
redacted. Query responses also redact Authorization so credentials are not
exposed through the inspection API.

The utility does not write runtime files and does not persist secrets.

## Testing

Use unit tests around the state and request handler. Cover:

1. completed callback capture;
2. failed callback capture;
3. duplicate `run_id` detection;
4. callback listing and per-run lookup;
5. reset and health behavior;
6. optional token and Provider ID validation;
7. malformed JSON;
8. configurable callback status;
9. configurable response delay without delaying inspection endpoints;
10. Authorization redaction.

Tests bind to an ephemeral loopback port and use Python standard library HTTP
clients.
