# Current-user connector state for knowledge content

## Contract
The existing knowledge-content Service API gains `yuque_connection(kb_id, access)`. The registered GET `/api/knowledge/spaces/{space_id}/knowledge-bases/{kb_id}/yuque/connection` uses the existing authenticated context, feature gate and knowledge-base access policy. It does not introduce a public OpenAPI or bypass resource authorization.

The remote adapter issues one current-actor GET `/api/v1/knowledge/data-source/connectors/yuque/connection` through the existing bounded transport. Response is `{connected: boolean, authorization_url: string}`; credentials and identity never appear in the result. `connected` means a saved active connection, not remote validity or consent to a separate MCP provider.

A completion-window notification is not a grant. Clients must re-read server state and retain a manual refresh fallback. No new persistence, environment settings, dependencies or auth implementation is introduced. Existing content reads and writes are unchanged.

## Verification
Exercise remote request/identity forwarding and registered routes with real normal DI, authenticated/denied access, feature disabled and missing credentials. Receiver and UI delivery are separate; local tests do not prove a live external OAuth consent flow.
