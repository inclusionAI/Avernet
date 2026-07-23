# Organization Admin Run Create HTTP 200 Design

## Goal

Change the successful HTTP status of
`POST /organizations/{organization_code}/admin-runs` from `202 Accepted` to
`200 OK`.

## Contract

Only the successful HTTP status changes. The response keeps:

- the `Location` header pointing to the created admin run;
- envelope code `20000`;
- envelope message `"accepted"`;
- the existing `data` and `request_id` shapes.

Request validation, authorization, dispatch behavior, callback behavior, and
run status values do not change.

Request-level failures continue to use their existing HTTP error statuses and
error envelopes. A run that was created but could not be delivered continues
to return a successful create response with `data.status` set to
`"delivery_failed"`. Later execution failures continue to be reported by the
GET endpoint through `data.status` and `data.error`.

## Implementation

Update the admin invocation HTTP adapter to construct a `200 OK` response.
Update the focused bootstrap integration assertion and the BCS user-story E2E
status expectation from 202 to 200.

## Testing

Use test-driven development:

1. Change the focused integration assertion to expect `200 OK`.
2. Run the focused test and confirm it fails because the implementation still
   returns `202 Accepted`.
3. Change the route implementation to return `200 OK`.
4. Update the E2E status expectation.
5. Re-run the focused test and the relevant BCS HTTP test suite.
