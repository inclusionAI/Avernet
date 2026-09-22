# DingTalk approval identity contract

Approval actions use a server-verified identity. Both external `card-web`
pages and the ClawWeb workbench use the authenticated ClawWeb session. A user
identifier supplied by the browser is never an authorization credential.

## Browser flow

The external approval page and the run/workspace approval panels send actions
to `POST /api/approval/:id/resolve/session`. The host resolves the current user
from the ClawWeb login request and checks that server-verified user against the
approval record. The browser does not send an `empId`, and this path does not
require the DingTalk JSAPI or an H5 application-domain allowlist.

The resolve request body is:

```json
{
  "action": "approve",
  "comment": "optional"
}
```

The public runner does not treat a decoded `IAM_TOKEN` payload as verified
identity. In production, a host must inject an identity resolver that validates
the login session with its identity provider before this endpoint can approve
or reject. The public runner only retains its loopback `dev` identity for local
testing; deployment hosts supply their own verified resolver.

## Retained DingTalk flow

The DingTalk authorization-code adapter remains available for future H5
passwordless login support:

1. `GET /api/approval/auth/dingtalk/config` returns the public `clientId` and
   `corpId`.
2. A client obtains a short-lived authorization code with
   `dd.requestAuthCode({ clientId, corpId })`.
3. The client sends that code to `POST /api/approval/:id/resolve`.
4. The server exchanges it for a DingTalk `userId` and checks the verified
   identity against the approval record.

The current external approval page does not call this flow.

## Compatibility

- Existing workflow YAML and pending approval records require no migration.
- Historical links containing `empId` or `corpId` still open, display, and can
  resolve an existing approval through the authenticated ClawWeb session.
  Those query parameters never authorize the action.
- An old cached page that submits only `empId` receives HTTP 401. Refreshing
  the page loads the session-authenticated flow.
- The retained DingTalk flow still returns HTTP 503 when a deployment has no
  DingTalk identity adapter; it must not fall back to a URL identity.
