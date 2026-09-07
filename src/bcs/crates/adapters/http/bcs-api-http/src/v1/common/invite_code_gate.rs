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
    use axum::http::HeaderMap;
    use axum::middleware::from_fn_with_state;
    use axum::Router;
    use crate::{PrincipalVerificationError, PrincipalVerifier};

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
}
