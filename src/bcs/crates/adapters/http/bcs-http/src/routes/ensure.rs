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
use bcs_service_api::{EnsureBotCommand, OnboardActorIdentity};

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

    // 2. Map body → EnsureBotCommand.
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

    // 3. Call the onboarding service.
    let result = state.services.bot_onboarding.ensure_bot(command).await?;

    Ok(Json(EnsureBotResponse {
        bot_uuid: result.bot_uuid,
        ensured: result.ensured,
        created: result.created,
    }))
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
}