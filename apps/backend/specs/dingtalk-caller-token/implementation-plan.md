# DeviceToken exchange without new identity query parameters

The existing locator arguments (`bot_id`, optional `entity_id`, `stage`, `publish_id`, session keys) remain the HTTP contract. Only `token_type` is added. Application signature authentication stays in place. The owner comes from the existing Bot context; the optional provider returns the verified Caller subject, which selects the exact Caller instance.

- [x] Write failing schema and adapter tests proving no new caller/owner query parameters and extra query IDs cannot influence the service.
- [x] Write failing service tests requiring upstream exchange without caller assertion, using the verified subject verbatim, and rejecting invalid subjects, tokens, expiry and targets.
- [x] Remove identity assertion arguments; update optional provider capability and service orchestration while preserving IAM behavior.
- [x] Run focused security, compatibility and architecture checks with coverage. Record new evidence without repeating the prior full suite.
