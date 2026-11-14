# DeviceToken Caller credential exchange

Add `token_type=iam|dingtalk` to the existing GET `/api/v1/token/iam`, defaulting to `iam`. Keep the existing Cookie transport and response shape. The `dingtalk` source is an opaque platform DeviceToken, not a native chat-platform access token.

The DeviceToken branch must authenticate the existing BaaS application principal before accepting its `user_id` and `owner_id` assertions. The service resolves the exact existing Caller instance for that Bot, owner and caller; it never updates the owner's draft or shared service. It invokes the optional neutral `DingTalkCallerTokenProvider.exchange_dingtalk` capability and checks the verified returned subject and expiration before installation. Resolve the binding again after exchange and reject replacement. Reuse existing session validation and runtime installation.

Keep default IAM and existing agentpass behavior. A BaaS `dingtalk-token` credential forwards the source marker, caller, owner and a freshly signed application principal. Credentials and signed headers must never appear in logs. Log request, success and stable failure metadata.

Tests cover compatibility, signed application enforcement, missing fields, unknown types, subject mismatch, expired results, unavailable/replaced targets, provider failure, exact target installation and log secrecy. Run affected tests and the Backend and BaaS CI suites without weakening gates. No schema, deployment, relay, cron, new SDK or credential persistence changes.
