//! V1 application-layer `AuthService` facade over the legacy shared
//! `OAuthRouteState`, extracted verbatim from `mod.rs`. Pure move: the
//! provider iteration order, session-claim construction, and every error
//! path are unchanged.

use super::*;

use axum::http::{HeaderMap, HeaderName, HeaderValue};
use tracing::{info, warn};

use bcs_auth_api::extract_session_cookie;
use bcs_jwt::Claims;
use bcs_service_api::application::v1::{
    ApplicationError, AuthProviderUrl as V1AuthProviderUrl, AuthProviderUrlList, AuthRedirect,
    AuthService, AuthUserInfo, BuildLoginUrls, CompleteOAuthLogin, LogoutResult, LogoutSession,
    ReadCurrentUser, RefreshSession, SessionRenewal,
};
use bcs_service_api::application::RequestAuthHeaders;

fn headers_from_request_auth(input: &RequestAuthHeaders) -> Result<HeaderMap, ApplicationError> {
    let mut headers = HeaderMap::new();
    if let Some(value) = input.authorization.as_ref() {
        let header_value = HeaderValue::from_str(value).map_err(|_| {
            ApplicationError::invalid("invalid_authorization_header", "authorization header is invalid")
        })?;
        headers.insert(axum::http::header::AUTHORIZATION, header_value);
    }
    if let Some(value) = input.cookie.as_ref() {
        let header_value = HeaderValue::from_str(value).map_err(|_| {
            ApplicationError::invalid("invalid_cookie_header", "cookie header is invalid")
        })?;
        headers.insert(axum::http::header::COOKIE, header_value);
    }
    for (name, value) in &input.forwarded_headers {
        let header_name = HeaderName::from_bytes(name.as_bytes()).map_err(|_| {
            ApplicationError::invalid("invalid_forwarded_header", "forwarded header name is invalid")
        })?;
        let header_value = HeaderValue::from_str(value).map_err(|_| {
            ApplicationError::invalid("invalid_forwarded_header", "forwarded header value is invalid")
        })?;
        headers.insert(header_name, header_value);
    }
    Ok(headers)
}

#[async_trait::async_trait]
impl AuthService for OAuthRouteState {
    async fn login_urls(
        &self,
        request: BuildLoginUrls,
    ) -> Result<AuthProviderUrlList, ApplicationError> {
        let callback_base_url = request.callback_base_url.trim().trim_end_matches('/');
        if callback_base_url.is_empty() {
            return Err(ApplicationError::invalid(
                "invalid_callback_base_url",
                "callback_base_url must not be empty",
            ));
        }
        let mut providers = Vec::new();
        let mut names: Vec<&str> = self.providers.keys().map(|s| s.as_str()).collect();
        names.sort();
        for name in names {
            if let Some(provider) = self.providers.get(name) {
                let csrf_state = self.state_store.generate(name).await;
                let redirect_uri = format!("{callback_base_url}/{name}");
                let url = provider.auth_url(&csrf_state, &redirect_uri);
                providers.push(V1AuthProviderUrl {
                    name: name.to_string(),
                    url,
                });
            }
        }
        Ok(AuthProviderUrlList { providers })
    }

    async fn complete_login(
        &self,
        request: CompleteOAuthLogin,
    ) -> Result<AuthRedirect, ApplicationError> {
        let callback_base_url = request.callback_base_url.trim().trim_end_matches('/');
        if callback_base_url.is_empty() {
            return Err(ApplicationError::invalid(
                "invalid_callback_base_url",
                "callback_base_url must not be empty",
            ));
        }

        let provider_key = self
            .state_store
            .consume(&request.state)
            .await
            .map_err(|_| ApplicationError::invalid("invalid_state", "invalid state"))?;

        if provider_key != request.provider {
            return Err(ApplicationError::invalid("provider_mismatch", "provider mismatch"));
        }

        let provider = self.providers.get(&request.provider).cloned().ok_or_else(|| {
            ApplicationError::not_found("provider_not_found", "provider not found")
        })?;

        let code = request.code.or(request.auth_code).ok_or_else(|| {
            ApplicationError::invalid("missing_code", "missing code or auth_code")
        })?;
        let redirect_uri = format!("{callback_base_url}/{}", request.provider);
        let token = provider.exchange_code(&code, &redirect_uri).await.map_err(|e| {
            warn!(request_id = %bcs_observability::CurrentRequestId, error = %e, provider = %request.provider, "OAuth token exchange failed");
            ApplicationError::bad_gateway("token_exchange_failed", "token exchange failed")
        })?;

        let user_info = provider.get_user_info(&token).await.map_err(|e| {
            warn!(request_id = %bcs_observability::CurrentRequestId, error = %e, provider = %request.provider, "OAuth userinfo failed");
            ApplicationError::bad_gateway("userinfo_request_failed", "userinfo request failed")
        })?;

        let user_id = self
            .user_port
            .ensure_identity(
                &request.provider,
                &user_info.id,
                user_info.name.as_deref(),
                user_info.avatar.as_deref(),
                &self.config.env,
            )
            .await
            .map_err(|e| {
                warn!(request_id = %bcs_observability::CurrentRequestId, error = %e, "ensure_identity failed");
                ApplicationError::internal("identity creation failed")
            })?;

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let claims = Claims {
            sub: user_id,
            src: request.provider.clone(),
            iat: now,
            exp: now + self.config.idle_timeout_secs(),
            name: user_info.name.clone(),
        };
        let jwt = self.jwt_service.sign(&claims).map_err(|e| {
            warn!(request_id = %bcs_observability::CurrentRequestId, error = %e, "JWT signing failed");
            ApplicationError::internal("session creation failed")
        })?;

        self.user_port
            .update_token(&claims.sub, &bcs_jwt::token_hash(&jwt), claims.exp)
            .await
            .map_err(|e| {
                warn!(request_id = %bcs_observability::CurrentRequestId, error = %e, "update_token failed; aborting login");
                ApplicationError::internal("session creation failed")
            })?;

        info!(provider = %request.provider, user_id = %claims.sub, "OAuth login successful");
        Ok(AuthRedirect {
            location: self.config.success_redirect_location().to_string(),
            set_cookie: session_cookie(&jwt, self.config.cookie_secure),
        })
    }

    async fn current_user(&self, request: ReadCurrentUser) -> Result<AuthUserInfo, ApplicationError> {
        let Some(chain) = self.auth_chain.as_ref() else {
            return Err(ApplicationError::Unauthenticated);
        };
        let headers = headers_from_request_auth(&request.headers)?;
        match chain.authenticate(&headers).await {
            Ok(result) => match result.principal {
                Some(principal)
                    if principal
                        .user_id
                        .as_deref()
                        .is_some_and(|id| !id.is_empty()) =>
                {
                    Ok(AuthUserInfo {
                        user_id: principal.user_id.unwrap(),
                        name: principal.user_name,
                        provider: principal.source_name.unwrap_or_else(|| "chain".to_string()),
                        avatar: principal.avatar,
                    })
                }
                _ => Err(ApplicationError::Unauthenticated),
            },
            Err(e) => {
                warn!(request_id = %bcs_observability::CurrentRequestId, error = %e, "auth chain failed in OpenAPI auth user");
                Err(ApplicationError::internal("auth chain failed"))
            }
        }
    }

    async fn refresh_session(&self, request: RefreshSession) -> Result<SessionRenewal, ApplicationError> {
        let headers = headers_from_request_auth(&request.headers)?;
        let jwt = extract_session_cookie(&headers).ok_or(ApplicationError::Unauthenticated)?;
        let claims = self
            .jwt_service
            .verify_no_exp(&jwt)
            .map_err(|_| ApplicationError::Unauthenticated)?;

        let info = match self.user_port.get_identity_by_token(&bcs_jwt::token_hash(&jwt)).await {
            Ok(Some(info)) if info.user_id == claims.sub => info,
            Ok(_) => return Err(ApplicationError::Unauthenticated),
            Err(e) => {
                warn!(request_id = %bcs_observability::CurrentRequestId, error = %e, "refresh: identity lookup failed");
                return Err(ApplicationError::internal("identity lookup failed"));
            }
        };

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let iat = now.max(claims.iat.saturating_add(1));
        let new_claims = Claims {
            sub: claims.sub.clone(),
            src: claims.src.clone(),
            iat,
            exp: iat + self.config.idle_timeout_secs(),
            name: info.user_name.or(info.external_user_name),
        };
        let new_jwt = self.jwt_service.sign(&new_claims).map_err(|e| {
            warn!(request_id = %bcs_observability::CurrentRequestId, error = %e, "refresh: JWT signing failed");
            ApplicationError::internal("session renewal failed")
        })?;
        self.user_port
            .update_token(&new_claims.sub, &bcs_jwt::token_hash(&new_jwt), new_claims.exp)
            .await
            .map_err(|e| {
                warn!(request_id = %bcs_observability::CurrentRequestId, error = %e, "refresh: update_token failed");
                ApplicationError::internal("session renewal failed")
            })?;

        Ok(SessionRenewal {
            set_cookie: session_cookie(&new_jwt, self.config.cookie_secure),
        })
    }

    async fn logout(&self, request: LogoutSession) -> Result<LogoutResult, ApplicationError> {
        let headers = headers_from_request_auth(&request.headers)?;
        if let Some(jwt) = extract_session_cookie(&headers) {
            if let Ok(claims) = self.jwt_service.verify(&jwt) {
                if let Err(e) = self.user_port.update_token(&claims.sub, "", 0).await {
                    warn!(request_id = %bcs_observability::CurrentRequestId, error = %e, user_id = %claims.sub, "logout: token revocation failed");
                }
            }
        }
        Ok(LogoutResult {
            set_cookie: clear_session_cookie(self.config.cookie_secure),
        })
    }
}
