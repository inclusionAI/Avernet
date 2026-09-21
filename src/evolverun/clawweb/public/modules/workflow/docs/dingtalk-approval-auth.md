# DingTalk approval identity contract

`card-web` approval pages authenticate an action with a short-lived DingTalk
authorization code. A user identifier supplied by the browser is never an
authorization credential.

## Browser flow

1. `GET /api/approval/auth/dingtalk/config` returns the public `clientId` and
   `corpId`. The application secret is not exposed.
2. The page calls `dd.requestAuthCode({ clientId, corpId })`. The legacy
   `dd.runtime.permission.requestAuthCode` call remains a client-side fallback
   for older DingTalk containers.
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

## Compatibility

- Existing workflow YAML and pending approval records require no migration.
- Historical links containing `empId` or `corpId` still open and display the
  approval, but those query parameters do not authorize an action.
- An old cached page that submits only `empId` receives HTTP 401. Refreshing
  the page loads the auth-code flow.
- Deployments without the DingTalk identity adapter return HTTP 503 for the
  public config and identity-exchange endpoints; they must not fall back to a
  URL identity.
