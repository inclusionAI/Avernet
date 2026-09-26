# Local Repair access to legacy Arca

The default `mist` connection mode is unchanged for deployed control planes.
For an explicitly local control plane (loopback `publicBaseUrl`), set
`REPAIR_ARCA_CONNECTION_MODE=ocb_owner` and supply
`REPAIR_ARCA_LOCAL_OWNER_ID` plus `REPAIR_ARCA_LOCAL_IAM_TOKEN` in its private process
environment. Inject the current legitimate login at process startup; do not put
credentials in tracked config, task JSON, bootstrap, model input, logs or receipts.
The server does not discover or read browser/ODC cookie files.

The Owner provider calls the existing OCB
`GET /api/v1/devices/{bindingId}/connection?port=20003&ttl=120` with Owner identity.
OCB validates binding access. Each command obtains a fresh short-lived token;
there is no disk token cache. Login redirects are not followed, and upstream
response bodies are not included in errors. After checking availability,
OpenClaw engine type, sandbox and exact frozen instance, the existing
`ArcaCommandTransport` calls AgentProxy terminal with the scoped token.
The explicit local provider also supplies the same Owner Cookie and x-user-id
for the AgentProxy request, matching the historical local transport. These
credentials stay in request memory and are never included in command bodies or
returned execution results. Local AgentProxy redirects are not followed.
MIST connections do not supply this local identity and retain their existing
scoped-token-only behavior.

The same selected transport is shared by full Repair and session recovery.
Invalid/expired identity, unavailable targets, changed instances and invalid
responses fail explicitly. There is no automatic fallback to signing secrets,
Arca CLI, another target or another identity. Refresh the login and explicitly
retry after checking the target. This adapter requires neither local MIST nor a
local copy of the AgentProxy signing secret.

This enables local container access; it does not establish AIS/NAS/model
end-to-end acceptance by itself. Engine `chat.send` identity requirements remain
a separate application-level contract.
