//! `POST /admin/bots/{bot_uuid}/ensure` — Phase 0 backfill (spec §4.2 Step 0b).
//!
//! Takes a **service credential** (`X-BCS-Service-Key`) plus `staff_no` in the
//! body, not a user JWT. Idempotently ensures the `bcs_bots` row, binds the
//! creator's human actor + owner edges, and seeds the default permission
//! profile in one call. Used to backfill bots that exist in the backend
//! `ac_bots` table but are missing from BCS `bcs_bots`.
use axum::{
    Json,
    extract::{Path, State},
    http::HeaderMap,
};
use bcs_protocol::{EnsureBotRequest, EnsureBotResponse};
use bcs_service_api::{EnsureBotCommand, OnboardActorIdentity, ServiceError};

use crate::error::HttpAdapterError;
use crate::state::HttpAppState;

/// Header carrying the service credential. Reuses the existing
/// `X-BCS-Service-Key` convention (sha256-registered admin key; empty
/// `bound_groups` = admin capable of any target). The spec names this
/// `X-Service-Credential`; we accept both for backfill-tool compatibility.
const SERVICE_KEY_HEADER: &str = "X-BCS-Service-Key";
const SERVICE_CREDENTIAL_HEADER: &str = "X-Service-Credential";

pub async fn ensure_bot(
    State(state): State<HttpAppState>,
    Path(bot_uuid): Path<String>,
    headers: HeaderMap,
    Json(body): Json<EnsureBotRequest>,
) -> Result<Json<EnsureBotResponse>, HttpAdapterError> {
    // 1. Verify service credential.
    verify_service_credential(&state, &headers)?;

    // 2. Parse the human-facing visibility BEFORE any side effect so an
    // invalid payload fails the request without having ensured the bot.
    let user_visibility = parse_user_visibility(body.user_visibility.as_deref())?;

    // 3. Map body → EnsureBotCommand.
    let name = body
        .name
        .filter(|name| !name.trim().is_empty())
        .unwrap_or_else(|| bot_uuid.clone());
    let actor_identity = if body.staff_no.trim().is_empty() {
        None
    } else {
        Some(OnboardActorIdentity {
            staff_no: body.staff_no,
            nick_name: body.nick_name,
        })
    };
    let command = EnsureBotCommand {
        bot_uuid: bot_uuid.clone(),
        name,
        summary: body.summary,
        visibility: body.visibility,
        actor_identity,
    };

    // 4. Call the onboarding service.
    let result = state.services.bot_onboarding.ensure_bot(command).await?;

    // 5. Optional human-facing visibility (`user_visibility`) — applied after
    // the ensure upsert through the internal bot-attributes patch so a
    // re-ensure that only binds owner edges stays a pure no-op when omitted.
    apply_user_visibility(&state, &result.bot_uuid, user_visibility).await?;

    Ok(Json(EnsureBotResponse {
        bot_uuid: result.bot_uuid,
        ensured: result.ensured,
        created: result.created,
    }))
}

/// Parse the request's human-facing visibility.
///
/// `None` (or an empty/whitespace value) means "preserve existing" and maps
/// to a no-op; any other value must name one of the three supported states.
/// Runs before the ensure upsert so an invalid payload never leaves a
/// half-applied bot behind while the request still reads as a client error.
fn parse_user_visibility(
    user_visibility: Option<&str>,
) -> Result<Option<bcs_service_api::UserVisibility>, HttpAdapterError> {
    let Some(value) = user_visibility.map(str::trim).filter(|value| !value.is_empty()) else {
        return Ok(None);
    };
    match value {
        "public" => Ok(Some(bcs_service_api::UserVisibility::Public)),
        "protected" => Ok(Some(bcs_service_api::UserVisibility::Protected)),
        "private" => Ok(Some(bcs_service_api::UserVisibility::Private)),
        _ => Err(HttpAdapterError::BadRequest(format!(
            "user_visibility must be public, protected, or private; got '{value}'"
        ))),
    }
}

/// Apply the parsed human-facing visibility after the ensure upsert, through
/// the internal bot-attributes patch. A missing internal-attributes service
/// fails the request; the ensure upsert itself remains idempotent and a
/// retry with a fixed deployment converges.
async fn apply_user_visibility(
    state: &HttpAppState,
    bot_uuid: &str,
    user_visibility: Option<bcs_service_api::UserVisibility>,
) -> Result<(), HttpAdapterError> {
    let Some(parsed) = user_visibility else {
        return Ok(());
    };
    let service = state
        .internal_bot_attributes_service
        .as_ref()
        .ok_or_else(|| {
            HttpAdapterError::Service(ServiceError::InternalError(
                "user_visibility requested but the internal bot-attributes service is not wired"
                    .to_string(),
            ))
        })?;
    service
        .patch(
            bcs_service_api::PatchBotInternalAttributes {
                bot_id: bot_uuid.to_string(),
                visibility: None,
                user_visibility: Some(parsed),
                friend_ext: None,
                friend_check_in_strategy: None,
            },
        )
        .await
        .map_err(|error| {
            HttpAdapterError::Service(ServiceError::InternalError(format!(
                "failed to apply user_visibility='{parsed:?}' to bot '{bot_uuid}': {error}",
            )))
        })?;
    Ok(())
}

/// Validate the service credential against the configured `ApiKeyRegistry`.
///
/// Accepts either `X-BCS-Service-Key` (the existing convention) or
/// `X-Service-Credential` (the spec name). An empty registry (dev/default)
/// allows any non-empty key — mirroring the existing
/// `resolve_service_caller` behavior in `routes::services`. A configured
/// registry requires the key to resolve to a known entry.
fn verify_service_credential(
    state: &HttpAppState,
    headers: &HeaderMap,
) -> Result<(), HttpAdapterError> {
    let raw_key = headers
        .get(SERVICE_KEY_HEADER)
        .or_else(|| headers.get(SERVICE_CREDENTIAL_HEADER))
        .and_then(|value| value.to_str().ok())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            HttpAdapterError::Unauthorized(
                "missing service credential (X-BCS-Service-Key or X-Service-Credential)".to_string(),
            )
        })?;

    let registry = &state.service_api_keys;
    if registry.is_empty() {
        // Dev/default: no keys configured, accept any non-empty value.
        return Ok(());
    }
    if registry.resolve(raw_key).is_some() {
        Ok(())
    } else {
        Err(HttpAdapterError::Unauthorized(
            "invalid service credential".to_string(),
        ))
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use async_trait::async_trait;
    use axum::{
        Json,
        body::{Body, to_bytes},
        http::Request,
    };
    use bcs_service_api::{
        AdminBotOnboardCommand, BotOnboardCommand, BotOnboardResult, BotOnboardingService,
        EnsureBotCommand, EnsureBotResult, ServiceError, ServiceResult,
    };
    use bcs_services_container::Services;
    use serde_json::{Value, json};
    use tower::util::ServiceExt;

    use crate::router::build_router;
    use crate::service_key::{ApiKeyEntry, ApiKeyRegistry, sha256_hex};
    use crate::state::HttpAppState;

    /// A controllable `BotOnboardingService` that records the last `ensure_bot`
    /// command and returns a canned `created` flag.
    struct RecordingOnboarding {
        last_ensure: tokio::sync::Mutex<Option<EnsureBotCommand>>,
        created: bool,
    }

    #[async_trait]
    impl BotOnboardingService for RecordingOnboarding {
        async fn onboard_bot(&self, _command: BotOnboardCommand) -> ServiceResult<BotOnboardResult> {
            Err(ServiceError::InternalError("not used".to_string()))
        }

        async fn admin_onboard_bot(
            &self,
            _command: AdminBotOnboardCommand,
        ) -> ServiceResult<BotOnboardResult> {
            Err(ServiceError::InternalError("not used".to_string()))
        }

        async fn ensure_bot(&self, command: EnsureBotCommand) -> ServiceResult<EnsureBotResult> {
            *self.last_ensure.lock().await = Some(command.clone());
            Ok(EnsureBotResult {
                bot_uuid: command.bot_uuid,
                ensured: true,
                created: self.created,
            })
        }
    }

    fn app(
        registry: ApiKeyRegistry,
        onboarding: Arc<dyn BotOnboardingService>,
    ) -> axum::Router {
        let state = HttpAppState::new(
            Services::builder()
                .bot_onboarding(onboarding)
                .build_for_test(),
        )
        .with_service_api_keys(Arc::new(registry));
        build_router(state)
    }

    fn ensure_request(body: &Value, header_name: &str, header_value: &str) -> Request<Body> {
        Request::builder()
            .method("POST")
            .uri("/admin/bots/bot-85020/ensure")
            .header(header_name, header_value)
            .header("content-type", "application/json")
            .body(Body::from(body.to_string()))
            .unwrap()
    }

    async fn body_json(response: axum::response::Response) -> Value {
        let bytes = to_bytes(response.into_body(), usize::MAX).await.unwrap();
        serde_json::from_slice(&bytes).unwrap()
    }

    #[tokio::test]
    async fn ensure_missing_credential_returns_401() {
        let onboarding = Arc::new(RecordingOnboarding {
            last_ensure: tokio::sync::Mutex::new(None),
            created: true,
        });
        let app = app(ApiKeyRegistry::new(Vec::new()), onboarding.clone());

        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/admin/bots/bot-85020/ensure")
                    .header("content-type", "application/json")
                    .body(Body::from(
                        json!({"name":"Bot","staff_no":"85020"}).to_string(),
                    ))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), axum::http::StatusCode::UNAUTHORIZED);
        assert!(onboarding.last_ensure.lock().await.is_none());
    }

    #[tokio::test]
    async fn ensure_invalid_credential_returns_401_when_registry_configured() {
        let known = "known-service-key";
        let registry = ApiKeyRegistry::new(vec![ApiKeyEntry {
            name: "admin".to_string(),
            sha256: sha256_hex(known),
            bound_groups: Vec::new(),
        }]);
        let onboarding = Arc::new(RecordingOnboarding {
            last_ensure: tokio::sync::Mutex::new(None),
            created: true,
        });
        let app = app(registry, onboarding.clone());

        let response = app
            .oneshot(ensure_request(
                &json!({"name":"Bot","staff_no":"85020"}),
                "X-BCS-Service-Key",
                "wrong-key",
            ))
            .await
            .unwrap();

        assert_eq!(response.status(), axum::http::StatusCode::UNAUTHORIZED);
        assert!(onboarding.last_ensure.lock().await.is_none());
    }

    #[tokio::test]
    async fn ensure_valid_credential_accepted_when_registry_configured() {
        let known = "known-service-key";
        let registry = ApiKeyRegistry::new(vec![ApiKeyEntry {
            name: "admin".to_string(),
            sha256: sha256_hex(known),
            bound_groups: Vec::new(),
        }]);
        let onboarding = Arc::new(RecordingOnboarding {
            last_ensure: tokio::sync::Mutex::new(None),
            created: true,
        });
        let app = app(registry, onboarding.clone());

        let response = app
            .oneshot(ensure_request(
                &json!({"name":"Bot","staff_no":"85020"}),
                "X-BCS-Service-Key",
                known,
            ))
            .await
            .unwrap();

        assert_eq!(response.status(), axum::http::StatusCode::OK);
        let body = body_json(response).await;
        assert_eq!(body["bot_uuid"], "bot-85020");
        assert_eq!(body["created"], true);
    }

    #[tokio::test]
    async fn ensure_first_call_creates_bot() {
        // Empty registry = dev mode, accept any non-empty key.
        let onboarding = Arc::new(RecordingOnboarding {
            last_ensure: tokio::sync::Mutex::new(None),
            created: true,
        });
        let app = app(ApiKeyRegistry::new(Vec::new()), onboarding.clone());

        let response = app
            .oneshot(ensure_request(
                &json!({
                    "name": "MyBot",
                    "summary": "dev bot",
                    "staff_no": "85020",
                    "nick_name": "Alice",
                    "visibility": "protected"
                }),
                "X-BCS-Service-Key",
                "any-dev-key",
            ))
            .await
            .unwrap();

        assert_eq!(response.status(), axum::http::StatusCode::OK);
        let body = body_json(response).await;
        assert_eq!(body["bot_uuid"], "bot-85020");
        assert_eq!(body["ensured"], true);
        assert_eq!(body["created"], true);

        let last = onboarding.last_ensure.lock().await.clone().unwrap();
        assert_eq!(last.bot_uuid, "bot-85020");
        assert_eq!(last.name, "MyBot");
        assert_eq!(last.summary.as_deref(), Some("dev bot"));
        assert_eq!(last.visibility, "protected");
        assert_eq!(last.actor_identity.as_ref().unwrap().staff_no, "85020");
        assert_eq!(
            last.actor_identity.as_ref().unwrap().nick_name.as_deref(),
            Some("Alice")
        );
    }

    #[tokio::test]
    async fn ensure_second_call_reports_not_created() {
        let onboarding = Arc::new(RecordingOnboarding {
            last_ensure: tokio::sync::Mutex::new(None),
            created: false,
        });
        let app = app(ApiKeyRegistry::new(Vec::new()), onboarding.clone());

        let response = app
            .oneshot(ensure_request(
                &json!({"name":"MyBot","staff_no":"85020"}),
                "X-Service-Credential",
                "any-dev-key",
            ))
            .await
            .unwrap();

        assert_eq!(response.status(), axum::http::StatusCode::OK);
        let body = body_json(response).await;
        assert_eq!(body["ensured"], true);
        assert_eq!(body["created"], false);
    }

    #[tokio::test]
    async fn ensure_empty_staff_no_skips_owner_binding() {
        let onboarding = Arc::new(RecordingOnboarding {
            last_ensure: tokio::sync::Mutex::new(None),
            created: true,
        });
        let app = app(ApiKeyRegistry::new(Vec::new()), onboarding.clone());

        let response = app
            .oneshot(ensure_request(
                &json!({"name":"MyBot","staff_no":""}),
                "X-BCS-Service-Key",
                "any-dev-key",
            ))
            .await
            .unwrap();

        assert_eq!(response.status(), axum::http::StatusCode::OK);
        let last = onboarding.last_ensure.lock().await.clone().unwrap();
        assert!(last.actor_identity.is_none());
    }

    #[tokio::test]
    async fn ensure_missing_name_falls_back_to_bot_uuid() {
        let onboarding = Arc::new(RecordingOnboarding {
            last_ensure: tokio::sync::Mutex::new(None),
            created: true,
        });
        let app = app(ApiKeyRegistry::new(Vec::new()), onboarding.clone());

        let response = app
            .oneshot(ensure_request(
                &json!({"staff_no":"85020"}),
                "X-BCS-Service-Key",
                "any-dev-key",
            ))
            .await
            .unwrap();

        assert_eq!(response.status(), axum::http::StatusCode::OK);
        let last = onboarding.last_ensure.lock().await.clone().unwrap();
        assert_eq!(last.name, "bot-85020");
    }

    // Silence the unused `Json` import warning when the test build keeps it
    // out of the handler path.
    #[test]
    fn json_import_compiles() {
        let _ = Json::<Value>::default();
    }

    // ── user_visibility (provisioning-time human-facing visibility) ─────────

    /// Records the last attributes patch.
    struct RecordingInternalAttributes {
        last_patch: tokio::sync::Mutex<Option<bcs_service_api::PatchBotInternalAttributes>>,
    }

    impl RecordingInternalAttributes {
        fn recording() -> Arc<Self> {
            Arc::new(Self {
                last_patch: tokio::sync::Mutex::new(None),
            })
        }
    }

    #[async_trait]
    impl bcs_service_api::InternalBotAttributesService for RecordingInternalAttributes {
        async fn get(
            &self,
            _bot_id: String,
        ) -> Result<
            bcs_service_api::BotInternalAttributes,
            bcs_service_api::application::v1::ApplicationError,
        > {
            Err(bcs_service_api::application::v1::ApplicationError::internal(
                "not used".to_string(),
            ))
        }

        async fn patch(
            &self,
            command: bcs_service_api::PatchBotInternalAttributes,
        ) -> Result<
            bcs_service_api::BotInternalAttributes,
            bcs_service_api::application::v1::ApplicationError,
        > {
            *self.last_patch.lock().await = Some(command);
            Ok(bcs_service_api::BotInternalAttributes {
                visibility: "protected".to_string(),
                user_visibility: bcs_service_api::UserVisibility::Protected,
                friend_ext: serde_json::Map::new(),
                friend_check_in_strategy: bcs_service_api::FriendCheckInStrategy::Approval,
            })
        }
    }

    fn app_with_attributes(
        registry: ApiKeyRegistry,
        onboarding: Arc<dyn BotOnboardingService>,
        attributes: Arc<dyn bcs_service_api::InternalBotAttributesService>,
    ) -> axum::Router {
        let state = HttpAppState::new(
            Services::builder()
                .bot_onboarding(onboarding)
                .build_for_test(),
        )
        .with_internal_bot_attributes_service(attributes)
        .with_service_api_keys(Arc::new(registry));
        build_router(state)
    }

    #[tokio::test]
    async fn ensure_user_visibility_applies_attributes_patch() {
        let onboarding = Arc::new(RecordingOnboarding {
            last_ensure: tokio::sync::Mutex::new(None),
            created: true,
        });
        let attributes = RecordingInternalAttributes::recording();
        let app = app_with_attributes(
            ApiKeyRegistry::new(Vec::new()),
            onboarding.clone(),
            attributes.clone(),
        );

        let response = app
            .oneshot(ensure_request(
                &json!({"name":"MyBot","staff_no":"85020","visibility":"protected","user_visibility":"public"}),
                "X-BCS-Service-Key",
                "any-dev-key",
            ))
            .await
            .unwrap();

        assert_eq!(response.status(), axum::http::StatusCode::OK);
        let patch = attributes.last_patch.lock().await.clone().unwrap();
        assert_eq!(patch.bot_id, "bot-85020");
        assert_eq!(
            patch.user_visibility,
            Some(bcs_service_api::UserVisibility::Public)
        );
        // The patch is user_visibility-only: no other attribute moves.
        assert_eq!(patch.visibility, None);
        assert_eq!(patch.friend_ext, None);
        assert_eq!(patch.friend_check_in_strategy, None);
        // The upsert ran first; the attributes patch applies on top of it.
        assert!(onboarding.last_ensure.lock().await.is_some());
    }

    #[tokio::test]
    async fn ensure_user_visibility_absent_or_blank_is_a_no_op() {
        let onboarding = Arc::new(RecordingOnboarding {
            last_ensure: tokio::sync::Mutex::new(None),
            created: true,
        });
        let attributes = RecordingInternalAttributes::recording();
        let app = app_with_attributes(
            ApiKeyRegistry::new(Vec::new()),
            onboarding.clone(),
            attributes.clone(),
        );

        for body in [
            json!({"name":"MyBot","staff_no":"85020","visibility":"protected"}),
            json!({"name":"MyBot","staff_no":"85020","user_visibility":""}),
            json!({"name":"MyBot","staff_no":"85020","user_visibility":"   "}),
        ] {
            let response = app
                .clone()
                .oneshot(ensure_request(&body, "X-BCS-Service-Key", "any-dev-key"))
                .await
                .unwrap();
            assert_eq!(response.status(), axum::http::StatusCode::OK);
            assert!(
                attributes.last_patch.lock().await.is_none(),
                "absent/blank user_visibility must not touch attributes (body {body})"
            );
        }
    }

    #[tokio::test]
    async fn ensure_user_visibility_invalid_value_returns_400_without_patching() {
        let onboarding = Arc::new(RecordingOnboarding {
            last_ensure: tokio::sync::Mutex::new(None),
            created: true,
        });
        let attributes = RecordingInternalAttributes::recording();
        let app = app_with_attributes(
            ApiKeyRegistry::new(Vec::new()),
            onboarding.clone(),
            attributes.clone(),
        );

        let response = app
            .oneshot(ensure_request(
                &json!({"name":"MyBot","staff_no":"85020","user_visibility":"friends"}),
                "X-BCS-Service-Key",
                "any-dev-key",
            ))
            .await
            .unwrap();

        assert_eq!(response.status(), axum::http::StatusCode::BAD_REQUEST);
        let body = body_json(response).await;
        assert!(
            body.to_string().contains("user_visibility"),
            "error must name the offending field: {body}"
        );
        assert!(attributes.last_patch.lock().await.is_none());
        assert!(
            onboarding.last_ensure.lock().await.is_none(),
            "invalid payloads must fail before the ensure upsert"
        );
    }

    #[tokio::test]
    async fn ensure_user_visibility_without_attributes_service_reports_missing_wiring() {
        // Same wiring as app(): no internal bot-attributes service mounted.
        let onboarding = Arc::new(RecordingOnboarding {
            last_ensure: tokio::sync::Mutex::new(None),
            created: true,
        });
        let app = app(ApiKeyRegistry::new(Vec::new()), onboarding.clone());

        let response = app
            .oneshot(ensure_request(
                &json!({"name":"MyBot","staff_no":"85020","user_visibility":"public"}),
                "X-BCS-Service-Key",
                "any-dev-key",
            ))
            .await
            .unwrap();

        // The upsert already succeeded, so a fixed deployment converges on
        // retry; missing wiring is a server-side condition, not a client error.
        assert_eq!(
            response.status(),
            axum::http::StatusCode::INTERNAL_SERVER_ERROR
        );
        assert!(onboarding.last_ensure.lock().await.is_some());
    }
}
