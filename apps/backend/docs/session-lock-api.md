# Session lock error responses

The session lock acquire, release, steal, and info endpoints use the existing
`ApiResponse` envelope and HTTP 200 for business errors. If the Bot address
cannot be resolved, these endpoints return `success: false`, `error_code: 404`,
and `data: null`. Previously this condition returned business error code 500.
The response must not be interpreted as a successful lock operation.

Other exceptions that reach the HTTP handler continue to use business code 500;
release by a non-holder without force continues to use 403. This change does not
change membership checks, remove historical lock records, or stop clients from
sending requests for deleted Bots. Clients that branch on `error_code` should
handle the new not-found code. HTTP status and response shape are unchanged.
