# DeviceToken Caller credential exchange

Add only `token_type=iam|dingtalk` to the existing GET `/api/v1/token/iam`, defaulting to `iam`. Preserve the existing locator arguments, Cookie transport and response shape. Do not add `user_id` or `owner_id` HTTP parameters or equivalent expected identity service arguments. Extra query IDs cannot establish identity or select a target.

The DeviceToken branch authenticates the existing signed application principal, resolves the Bot with its existing locator, and obtains owner from that context. The optional neutral `DingTalkCallerTokenProvider.exchange_dingtalk(device_token, bot_id, owner_user_id, task_metadata)` verifies the source and returns an opaque CallerToken. It takes no caller assertion or auth_context. The public service neither decodes nor normalizes the source credential.

Only the returned upstream verified subject selects the exact existing Caller instance. Reject missing, blank or padded subjects/tokens, invalid or expired results, unavailable targets, ownerless or non-Caller Bots and replaced bindings. Preserve existing session-key validation and exact Caller binding scope checks, and reuse the existing runtime installation. Never update owner/shared targets or fall back to browser SSO. Default IAM behavior remains unchanged.

Tests cover API schema, ignored malicious query IDs, trusted upstream subject and derived owner, signature enforcement, invalid credentials, binding and session failures, IAM compatibility, and secret-free request/success/failure diagnostics. Run affected tests and architecture checks with coverage. No BaaS/BCN changes are part of this revision.
