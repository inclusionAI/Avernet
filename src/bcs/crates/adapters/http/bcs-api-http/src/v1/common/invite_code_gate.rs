use std::sync::Arc;

use axum::extract::Request;
use axum::extract::State;
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use bcs_service_api::application::v1::{ApplicationError, AuthenticatedCaller, InviteCodeService};

use super::{ErrorResponse, PrincipalVerificationState, RequestId, application_error_response};

pub trait InviteCodeGateState: PrincipalVerificationState + Clone + Send + Sync + 'static {
    fn invite_code_service(&self) -> Option<&Arc<dyn InviteCodeService>>;
    fn invite_code_gate_enabled(&self) -> bool {
        true
    }
}

pub async fn enforce_invite_code_gate<S>(
    State(state): State<S>,
    request: Request,
    next: Next,
) -> Response
where
    S: InviteCodeGateState,
{
    let path = request.uri().path().to_string();
    if path == "/openapi/v1/collaboration/invite-codes/bind"
        || path == "/openapi/v1/collaboration/invite-codes/me"
        || path == "/api/v1/collaboration/invite-codes/init"
    {
        return next.run(request).await;
    }

    if !state.invite_code_gate_enabled() {
        return next.run(request).await;
    }

    let request_id = RequestId::from_headers(request.headers());
    let Some(caller) = request.extensions().get::<AuthenticatedCaller>().cloned() else {
        return ErrorResponse::unauthenticated(request_id.0).into_response();
    };
    let Some(service) = state.invite_code_service() else {
        return application_error_response(
            &request_id,
            ApplicationError::internal("V1 InviteCode service is not configured"),
        )
        .into_response();
    };
    match service.ensure_invite_code_access(&caller).await {
        Ok(()) => next.run(request).await,
        Err(error) => application_error_response(&request_id, error).into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::{Body, to_bytes};
    use axum::http::HeaderMap;
    use axum::http::Request;
    use axum::middleware::from_fn_with_state;
    use axum::routing::get;
    use axum::Router;
    use serde_json::Value;
    use tower::ServiceExt;
    use crate::{PrincipalVerificationError, PrincipalVerifier};

    #[derive(Default)]
    struct CountingInviteCodeService {
        allow_access: bool,
        ensure_access_calls: std::sync::Mutex<Vec<AuthenticatedCaller>>,
    }

    #[async_trait::async_trait]
    impl InviteCodeService for CountingInviteCodeService {
        async fn init_invite_codes(
            &self,
            _command: bcs_service_api::application::v1::InitInviteCodes,
        ) -> Result<bcs_service_api::application::v1::InitInviteCodesResult, ApplicationError> {
            Ok(bcs_service_api::application::v1::InitInviteCodesResult { codes: vec![] })
        }

        async fn bind_invite_code(
            &self,
            _command: bcs_service_api::application::v1::BindInviteCode,
        ) -> Result<bcs_service_api::application::v1::BindInviteCodeResult, ApplicationError> {
            Ok(bcs_service_api::application::v1::BindInviteCodeResult { bound: true, bound_at: 1 })
        }

        async fn get_my_invite_code_binding(
            &self,
            _command: bcs_service_api::application::v1::GetMyInviteCodeBinding,
        ) -> Result<bcs_service_api::application::v1::InviteCodeBindingView, ApplicationError> {
            Ok(bcs_service_api::application::v1::InviteCodeBindingView { bound: true, bound_at: Some(1) })
        }

        async fn ensure_invite_code_access(
            &self,
            caller: &AuthenticatedCaller,
        ) -> Result<(), ApplicationError> {
            self.ensure_access_calls
                .lock()
                .expect("ensure access lock")
                .push(caller.clone());
            if self.allow_access {
                Ok(())
            } else {
                Err(ApplicationError::invite_code_required("invite code required"))
            }
        }
    }

    #[derive(Clone)]
    struct DummyVerifier;

    #[async_trait::async_trait]
    impl PrincipalVerifier for DummyVerifier {
        async fn verify(
            &self,
            _headers: &HeaderMap,
        ) -> Result<AuthenticatedCaller, PrincipalVerificationError> {
            Ok(AuthenticatedCaller { tenant: None, user: None, bot: None, app: None, access_key: None })
        }
    }

    #[derive(Clone)]
    struct DummyState {
        verifier: Arc<dyn PrincipalVerifier>,
    }

    impl PrincipalVerificationState for DummyState {
        fn principal_verifier(&self) -> &Arc<dyn PrincipalVerifier> {
            &self.verifier
        }
    }

    impl InviteCodeGateState for DummyState {
        fn invite_code_service(&self) -> Option<&Arc<dyn InviteCodeService>> {
            None
        }

        fn invite_code_gate_enabled(&self) -> bool {
            true
        }
    }

    #[test]
    fn middleware_compiles_for_router_state() {
        let state = DummyState { verifier: Arc::new(DummyVerifier) };
        let _: Router = Router::new().layer(from_fn_with_state(state, enforce_invite_code_gate::<DummyState>));
    }

    #[test]
    fn request_id_can_be_constructed_from_headers() {
        let headers = HeaderMap::new();
        let _ = RequestId::from_headers(&headers);
        let _ = AuthenticatedCaller { tenant: None, user: None, bot: None, app: None, access_key: None };
    }

    #[derive(Clone)]
    struct DisabledGateState {
        verifier: Arc<dyn PrincipalVerifier>,
    }

    impl PrincipalVerificationState for DisabledGateState {
        fn principal_verifier(&self) -> &Arc<dyn PrincipalVerifier> {
            &self.verifier
        }
    }

    impl InviteCodeGateState for DisabledGateState {
        fn invite_code_service(&self) -> Option<&Arc<dyn InviteCodeService>> {
            None
        }

        fn invite_code_gate_enabled(&self) -> bool {
            false
        }
    }

    #[test]
    fn middleware_compiles_when_gate_is_disabled() {
        let state = DisabledGateState { verifier: Arc::new(DummyVerifier) };
        let _: Router = Router::new().layer(from_fn_with_state(state, enforce_invite_code_gate::<DisabledGateState>));
    }

    #[derive(Clone)]
    struct RuntimeState {
        verifier: Arc<dyn PrincipalVerifier>,
        service: Option<Arc<dyn InviteCodeService>>,
        enabled: bool,
    }

    impl PrincipalVerificationState for RuntimeState {
        fn principal_verifier(&self) -> &Arc<dyn PrincipalVerifier> {
            &self.verifier
        }
    }

    impl InviteCodeGateState for RuntimeState {
        fn invite_code_service(&self) -> Option<&Arc<dyn InviteCodeService>> {
            self.service.as_ref()
        }

        fn invite_code_gate_enabled(&self) -> bool {
            self.enabled
        }
    }

    async fn response_json(response: Response) -> Value {
        let bytes = to_bytes(response.into_body(), usize::MAX)
            .await
            .expect("response body bytes");
        serde_json::from_slice(&bytes).expect("response json")
    }

    fn gated_router(state: RuntimeState) -> Router {
        Router::new()
            .route("/protected", get(|| async { "ok" }))
            .layer(from_fn_with_state(state, enforce_invite_code_gate::<RuntimeState>))
    }

    fn caller() -> AuthenticatedCaller {
        AuthenticatedCaller {
            tenant: Some("tenant-1".to_string()),
            user: Some(bcs_service_api::application::v1::AuthenticatedUserIdentity {
                id: "staff-1".to_string(),
                username: "staff-1".to_string(),
                display_name: None,
                full_name: None,
            }),
            bot: None,
            app: None,
            access_key: None,
        }
    }

    #[tokio::test]
    async fn middleware_returns_unauthenticated_when_caller_is_missing() {
        let state = RuntimeState {
            verifier: Arc::new(DummyVerifier),
            service: Some(Arc::new(CountingInviteCodeService { allow_access: true, ..Default::default() })),
            enabled: true,
        };

        let response = gated_router(state)
            .oneshot(Request::builder().uri("/protected").body(Body::empty()).expect("request"))
            .await
            .expect("response");

        assert_eq!(response.status(), axum::http::StatusCode::UNAUTHORIZED);
        assert_eq!(response_json(response).await["data"]["error_code"], "unauthenticated");
    }

    #[tokio::test]
    async fn middleware_returns_internal_error_when_service_is_missing() {
        let state = RuntimeState {
            verifier: Arc::new(DummyVerifier),
            service: None,
            enabled: true,
        };

        let mut request = Request::builder().uri("/protected").body(Body::empty()).expect("request");
        request.extensions_mut().insert(caller());

        let response = gated_router(state)
            .oneshot(request)
            .await
            .expect("response");

        assert_eq!(response.status(), axum::http::StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(response_json(response).await["data"]["error_code"], "internal_error");
    }

    #[tokio::test]
    async fn middleware_forwards_when_access_is_allowed() {
        let service = Arc::new(CountingInviteCodeService { allow_access: true, ..Default::default() });
        let state = RuntimeState {
            verifier: Arc::new(DummyVerifier),
            service: Some(service.clone()),
            enabled: true,
        };

        let mut request = Request::builder().uri("/protected").body(Body::empty()).expect("request");
        request.extensions_mut().insert(caller());

        let response = gated_router(state)
            .oneshot(request)
            .await
            .expect("response");

        assert_eq!(response.status(), axum::http::StatusCode::OK);
        assert_eq!(service.ensure_access_calls.lock().expect("ensure access lock").len(), 1);
    }

    #[tokio::test]
    async fn middleware_skips_checks_when_gate_is_disabled() {
        let service = Arc::new(CountingInviteCodeService { allow_access: false, ..Default::default() });
        let state = RuntimeState {
            verifier: Arc::new(DummyVerifier),
            service: Some(service.clone()),
            enabled: false,
        };

        let response = gated_router(state)
            .oneshot(Request::builder().uri("/protected").body(Body::empty()).expect("request"))
            .await
            .expect("response");

        assert_eq!(response.status(), axum::http::StatusCode::OK);
        assert_eq!(service.ensure_access_calls.lock().expect("ensure access lock").len(), 0);
    }
}
