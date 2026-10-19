# IAM session query parameter compatibility

## Requirement and scope

The IAM HTTP endpoint currently accepts only `sessionKey`, while clients send
`session_key`. Accept `session_key` as the canonical query name and continue
accepting `sessionKey` for existing clients. If both are present, `session_key`
wins, including when its value is empty. Pass the selected value unchanged to
the existing application service and keep the existing session context logs.

Only the token exchange router and its HTTP parameter binding tests change.
Provider resolution, runtime authorization, BaaS, OCB, and token exchange policy
remain outside this fix.

## Implementation and validation

1. Declare both query parameters in the existing IAM endpoint; keep the direct
   Python call signature compatible and resolve precedence at the HTTP boundary.
2. Exercise the mounted FastAPI router over HTTP for both spellings, conflicting
   values, empty canonical values, missing values, and special characters.
3. Assert unchanged forwarding and visible session context in existing logs;
   preserve the existing error response for invalid empty session keys.
4. Run the relevant API/service tests and lint, rebase the fix onto remote `dev`,
   then submit a source PR and check its actual CI results.
