# Bot Catalog Search：预留 BCS 元信息端口

## Global Constraints

- Public API remains `GET /openapi/v1/bots/catalog/search` with `search`, `page`, and `page_size`; do not add `user_id`.
- The verified principal is the only source of `tenant_id`, `user_id`, and `app_id`.
- The join key is tenant-scoped `(bot_id, entity_id)`; `bot_id` alone or a global composite worker ID is insufficient.
- Backend remains the only authority for public response fields. BCS metadata only decides membership.
- Current production/local/test bindings make Catalog Search fail closed with `502000`; no BCS URL, path, payload, credentials, or network call is guessed now.
- Legacy `/api/v1/bot-public/search` remains Backend-only. Discover keeps its existing BCSFuse behavior and `bot_discover_service.py` logging unchanged.
- Do not modify, stage, or remove `.superpowers/`.
- Logs contain only request ID, counts, and failure categories; never log bot addresses, raw upstream data, caller identifiers, credentials, tokens, or request keywords.

## Task 1: Implement the complete Backend seam and fail-closed Catalog Search

Follow strict TDD: add focused tests first and run them to observe the expected failure before changing production code.

1. Add frozen domain DTOs `BotCatalogAddress`, `BotCatalogCaller`, and `BotCatalogMetadata`, plus runtime-checkable `BotCatalogMetadataServiceProtocol.query_public_bot_metadata(...)` and `BotCatalogMetadataUnavailableError` in the Backend Core internal-port layer, preserving the enforced `api -> core` dependency direction.
2. Add `UnavailableBotCatalogMetadataService`. It must always log a low-sensitivity unconfigured category with request ID and candidate count, then raise `BotCatalogMetadataUnavailableError`, including for an empty address list.
3. Inject the protocol into `BotPublicService`. Its dedicated Catalog Search method must:
   - query all current-tenant public Backend candidates in stable order;
   - build ordered, de-duplicated `(bot_id, entity_id)` addresses from Backend data;
   - always invoke the metadata protocol with addresses, trusted caller context, and request ID;
   - reject the complete metadata result as unavailable when an item is not `kind == "bot"`, duplicates another item, was not requested, or has an invalid/blank address;
   - inner join by exact address, preserve Backend order, sanitize existing sensitive fields, and compute total/page after the join;
   - translate the metadata unavailable error into `BotCatalogSearchUnavailableError` without exposing its details.
4. Project the verified principal in the Catalog Search router into `BotCatalogCaller(tenant_id=principal.tenant, user_id=principal.user_id or None, app_id=principal.app_id)`, pass it and the trace ID to the service, and keep the fixed `502000 / Catalog service unavailable` response.
5. Bind the protocol to the unavailable implementation in production/local/test DI. Remove only the Catalog Search BCSFuse HTTP qualifier/client/constructor/query code and its dedicated tests. Keep `BcsFuseConfig` and Discover untouched.
6. Keep the legacy Search path Backend-only and retain join-after-filter pagination behavior for a future configured protocol implementation.
7. Update the Chinese frontend document, Backend OpenAPI README references, generated `bots.openapi.json`, and the pipeline spec/report to describe the BCS port and temporary fixed 502 behavior. Do not describe an unimplemented BCS HTTP route.
8. Add focused tests for protocol/DI resolution, unavailable behavior with empty and non-empty candidates, caller projection for user-only/app-only/user+app, exact address join, same-bot/different-entity isolation, stable ordering/de-duplication, invalid metadata fail-closed behavior, join-before-pagination, legacy Search compatibility, Discover compatibility, and response-field leakage.
9. Run targeted pytest, architecture and DI tests, Ruff on changed Python files, unused-import checks, OpenAPI consistency checks, and `git diff --check`. Do not commit `.superpowers/`.
