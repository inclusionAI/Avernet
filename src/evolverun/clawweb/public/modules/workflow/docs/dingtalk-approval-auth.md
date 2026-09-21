# DingTalk approval identity contract

Approval actions use a server-verified identity. External `card-web` pages use
a short-lived DingTalk authorization code, while the ClawWeb workbench uses
its authenticated server session. A user identifier supplied by the browser
is never an authorization credential.

## Browser flow

1. `GET /api/approval/auth/dingtalk/config` returns the public `clientId` and
   `corpId`. The application secret is not exposed.
2. The page calls `dd.requestAuthCode({ clientId, corpId })`. The legacy
   `dd.runtime.permission.requestAuthCode` call remains a client-side fallback
   for older DingTalk containers. The `dd` API is supplied by the DingTalk
   container; the workflow package does not bundle a private or internal SDK.
3. The page sends the returned `authCode` in
   `POST /api/approval/:id/resolve`.
4. The server exchanges that code for a DingTalk `userId`, then checks that
   verified identity against the approval record before recording the action.

The resolve request body is:

```json
{
  "authCode": "one-time-code",
  "action": "approve",
  "comment": "optional"
}
```

## ClawWeb workbench flow

The run detail and workflow workspace approval panels send the action to
`POST /api/approval/:id/resolve/session`. The host resolves the current user
from the ClawWeb login request and checks that server-verified user against the
approval record. The browser does not send an `empId`, and this path does not
require the DingTalk JSAPI.

The public runner does not treat a decoded `IAM_TOKEN` payload as verified
identity. In production, a host must inject an identity resolver that validates
the login session with its identity provider before this endpoint can approve
or reject. The public runner only retains its loopback `dev` identity for local
testing; deployment hosts supply their own verified resolver.

## Compatibility

- Existing workflow YAML and pending approval records require no migration.
- Historical links containing `empId` or `corpId` still open, display, and can
  resolve an existing approval after the page obtains a fresh DingTalk
  `authCode`. Those query parameters never authorize the action.
- An old cached page that submits only `empId` receives HTTP 401. Refreshing
  the page loads the auth-code flow.
- Deployments without the DingTalk identity adapter return HTTP 503 for the
  public config or resolve request; they must not fall back to a URL identity.
