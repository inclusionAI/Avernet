# Public risk submission

The legacy and OpenAPI `POST .../public/risk-checks` persist the application,
then synchronously submit the security check before returning the saved view.
The first response retains the external check ID internally and exposes the
initial security status and detail URL when supplied. The response remains
Accepted: publication and any business approval are not complete.

The outbound `SecurityProvider.submit_agent_scan` contract returns a
`SecuritySubmission` containing `check_id` and its initial `SecurityReport`.
Both creation and query reports use the same status validation, polling windows,
and verified-PASS policy. Implementations must preserve the creation report,
not return just the external ID. Community fallback remains explicitly
unavailable; hosts must update their provider alongside this contract.

DETECTING schedules original-ID polling after 30 seconds (five-minute window).
REJECT schedules original-ID tracking after five minutes (one-hour window).
Verified PASS schedules the existing publication/approval advance task without
another report query. An unverified PASS requires attention. A later report
without a URL preserves the last nonempty detail URL.

A durable submission intent and conditional update precede the external call.
Recovery waits two minutes before examining an in-flight creation attempt;
this grace period is not a retry or a security outcome. HTTP creation has a
bounded provider timeout. Retries replay the same application, never blindly
repeat an unknown external POST. Old queued submit tasks are still handled
through the same one-attempt submission path.

Persistence failures propagate. A follow-up enqueue failure leaves the saved
check/report recoverable by the existing scanner. GET remains local-only;
explicit refresh queries the original check ID. No new table, column, HTTP
field, or stage parameter is required. Business approval remains asynchronous.
