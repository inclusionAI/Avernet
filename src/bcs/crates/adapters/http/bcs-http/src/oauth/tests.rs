    use super::*;
    use async_trait::async_trait;
    use axum::body::to_bytes;
    use bcs_app_auth::{AuthApplicationService, AuthApplicationServiceConfig};
    use bcs_auth_api::{
        AuthError, AuthPlugin, AuthPluginChain, AuthPrincipal, AuthSource,
        UserIdentityInfo, UserIdentityPort,
    };
    use bcs_service_api::application::v1::{ApplicationError, AuthProviderUrl};
    use bcs_service_api::port::oauth::{
        BrowserSession, ExternalLoginIdentity, OAuthProviderPort, OAuthSessionPort,
        PendingLoginBatch, PendingOAuthLoginPort,
    };
    use bcs_test_support::{MockOAuthProvider, NoopAuthPlugin, NoopUserIdentityPort};
    use std::sync::Mutex;

    /// A chain plugin that always yields a principal with the given `user_id`
    /// (which may be `None`), so the empty/missing-user_id branch of
    /// `current_user_handler` is reachable without depending on any specific
    /// config-driven plugin's input validation.
    struct FixedUserPlugin {
        user_id: Option<String>,
    }

    #[async_trait]
    impl AuthPlugin for FixedUserPlugin {
        fn can_authenticate(&self, _headers: &HeaderMap) -> bool {
            true
        }
        async fn authenticate(
            &self,
            _headers: &HeaderMap,
        ) -> Result<Option<AuthPrincipal>, AuthError> {
            let mut p = AuthPrincipal::new(AuthSource::Local);
            p.user_id = self.user_id.clone();
            Ok(Some(p))
        }
        fn priority(&self) -> u8 {
            10
        }
        fn name(&self) -> &'static str {
            "fixed"
        }
    }

    fn chain_with(user_id: Option<String>) -> Arc<AuthPluginChain> {
        Arc::new(AuthPluginChain::new(vec![
            Box::new(FixedUserPlugin { user_id }),
            Box::new(NoopAuthPlugin),
        ]))
    }

    async fn run_current_user(chain: Arc<AuthPluginChain>) -> (StatusCode, serde_json::Value) {
        let state = Arc::new(OAuthRouteState::new_chain_only(
            Arc::new(NoopUserIdentityPort),
            chain,
        ));
        let resp = current_user_handler(State(state), HeaderMap::new())
            .await
            .into_response();
        let status = resp.status();
        let bytes = to_bytes(resp.into_body(), usize::MAX).await.expect("body");
        let body = serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null);
        (status, body)
    }

    /// A principal whose `user_id` is `None` is not a human login — `/auth/user`
    /// must NOT return 200 with an empty user_id.
    #[tokio::test]
    async fn current_user_rejects_principal_without_user_id() {
        let (status, body) = run_current_user(chain_with(None)).await;
        assert_eq!(status, StatusCode::UNAUTHORIZED, "no user_id => 401, got {body}");
    }

    /// A principal whose `user_id` is an empty string must likewise be treated
    /// as not authenticated, not as a logged-in user with id "".
    #[tokio::test]
    async fn current_user_rejects_principal_with_empty_user_id() {
        let (status, body) = run_current_user(chain_with(Some(String::new()))).await;
        assert_eq!(status, StatusCode::UNAUTHORIZED, "empty user_id => 401, got {body}");
    }

    /// Regression guard: a populated user_id still returns 200.
    #[tokio::test]
    async fn current_user_accepts_principal_with_user_id() {
        let (status, body) = run_current_user(chain_with(Some("u-123".to_string()))).await;
        assert_eq!(status, StatusCode::OK, "real user_id => 200, got {body}");
        assert_eq!(body["user_id"], "u-123");
    }

    // ------------------------------------------------------------------
    // Legacy OAuth flow through the SHARED secure service.
    //
    // Counting fakes for the three OAuth ports (the service itself is the
    // real `AuthApplicationService` — no stubbing of the application layer).
    // Coverage transfer from the pre-switchover trait impl tests: the
    // redirect + cookie sequencing assertions now run through the real
    // handlers, which additionally pin the browser-binding challenge
    // protocol that did not exist before Task 11.
    // ------------------------------------------------------------------

    struct FakeProvider {
        exchanges: Mutex<Vec<String>>,
    }

    #[async_trait]
    impl OAuthProviderPort for FakeProvider {
        async fn names(&self) -> Vec<String> {
            vec!["google".to_string()]
        }

        async fn auth_url(
            &self,
            provider: &str,
            state: &str,
            _redirect_uri: &str,
        ) -> Result<String, ApplicationError> {
            Ok(format!("https://accounts.example/{provider}?state={state}"))
        }

        async fn exchange_user(
            &self,
            provider: &str,
            code: &str,
            _redirect_uri: &str,
        ) -> Result<ExternalLoginIdentity, ApplicationError> {
            self.exchanges.lock().unwrap().push(code.to_string());
            Ok(ExternalLoginIdentity {
                provider: provider.to_string(),
                external_user_id: "external-42".to_string(),
                name: Some("Mock User".to_string()),
                avatar: None,
            })
        }
    }

    #[derive(Default)]
    struct FakeSession {
        installs: Mutex<Vec<String>>,
        refreshes: Mutex<Vec<String>>,
        revocations: Mutex<Vec<String>>,
        fail_revoke: std::sync::atomic::AtomicBool,
        next: std::sync::atomic::AtomicU64,
    }

    impl FakeSession {
        fn next(&self) -> u64 {
            self.next
                .fetch_add(1, std::sync::atomic::Ordering::SeqCst)
                + 1
        }

        fn installed(&self) -> usize {
            self.installs.lock().unwrap().len()
        }
    }

    #[async_trait]
    impl OAuthSessionPort for FakeSession {
        async fn install_identity(
            &self,
            _identity: ExternalLoginIdentity,
            _now: u64,
        ) -> Result<BrowserSession, ApplicationError> {
            let token = format!("session-token-{}", self.next());
            self.installs.lock().unwrap().push(token.clone());
            Ok(BrowserSession {
                token,
                expires_at: 1_900_000_000,
            })
        }

        async fn refresh(&self, token: &str, _now: u64) -> Result<BrowserSession, ApplicationError> {
            if !self.installs.lock().unwrap().contains(&token.to_string()) {
                return Err(ApplicationError::Unauthenticated);
            }
            self.refreshes.lock().unwrap().push(token.to_string());
            Ok(BrowserSession {
                token: format!("refreshed-{token}"),
                expires_at: 1_900_000_000,
            })
        }

        async fn revoke(&self, token: &str) -> Result<(), ApplicationError> {
            if self.fail_revoke.load(std::sync::atomic::Ordering::SeqCst) {
                return Err(ApplicationError::unavailable("revocation failed"));
            }
            self.revocations.lock().unwrap().push(token.to_string());
            Ok(())
        }
    }

    #[derive(Default)]
    struct FakePending {
        batches: Mutex<Vec<(String, String)>>,
        consumed: Mutex<Vec<String>>,
        fail_issue: bool,
    }

    impl FakePending {
        fn fail_issue() -> Self {
            Self {
                fail_issue: true,
                ..Default::default()
            }
        }
    }

    #[async_trait]
    impl PendingOAuthLoginPort for FakePending {
        async fn issue_batch(
            &self,
            providers: &[String],
            callback_base: &str,
            flow: &str,
            _now: u64,
        ) -> Result<PendingLoginBatch, ApplicationError> {
            if self.fail_issue {
                return Err(ApplicationError::unavailable("pending store down"));
            }
            let index = self.batches.lock().unwrap().len();
            assert_eq!(flow, "legacy", "the legacy entry uses the legacy flow namespace");
            let state = format!("pending-state-{index}");
            self.batches.lock().unwrap().push((
                state.clone(),
                format!("browser-nonce-{index}"),
            ));
            let batch = PendingLoginBatch {
                browser_nonce: format!("browser-nonce-{index}"),
                expires_at: 1_900_000_000,
                provider_states: providers
                    .iter()
                    .map(|p| (p.clone(), state.clone()))
                    .collect(),
            };
            assert!(
                callback_base.ends_with("/auth/callback"),
                "legacy callback base must be exact: {callback_base}"
            );
            Ok(batch)
        }

        async fn consume(
            &self,
            state: &str,
            browser_nonce: &str,
            provider: &str,
            exact_callback: &str,
            flow: &str,
            _now: u64,
        ) -> Result<(), ApplicationError> {
            if self.consumed.lock().unwrap().iter().any(|s| s == state) {
                return Err(ApplicationError::invalid("oauth_invalid_state", "burned"));
            }
            let batches = self.batches.lock().unwrap();
            for (pending_state, pending_nonce) in batches.iter() {
                if pending_state == state
                    && pending_nonce == browser_nonce
                    && provider == "google"
                    && flow == "legacy"
                    && exact_callback == "https://bcs.example.com/auth/callback/google"
                {
                    self.consumed.lock().unwrap().push(state.to_string());
                    return Ok(());
                }
            }
            Err(ApplicationError::invalid("oauth_invalid_state", "no match"))
        }
    }


    fn legacy_service(
        session: Arc<FakeSession>,
        pending: Arc<FakePending>,
        provider: Arc<FakeProvider>,
    ) -> Arc<dyn AuthService> {
        Arc::new(AuthApplicationService::new(
            provider,
            session,
            pending,
            AuthApplicationServiceConfig {
                enabled_providers: vec!["google".to_string()],
                flow: "legacy".to_string(),
                post_login_redirect: "/".to_string(),
            },
        ))
    }

    const JWT_SECRET: &str = "test-secret-key-at-least-32-bytes!!";

    fn legacy_state(
        session: Arc<FakeSession>,
        pending: Arc<FakePending>,
        provider: Arc<FakeProvider>,
    ) -> Arc<OAuthRouteState> {
        let service = legacy_service(session, pending, provider);
        // Base URL is https → the __Host- challenge name + Secure attributes.
        let config = OAuthConfig {
            jwt_secret: JWT_SECRET.to_string(),
            idle_timeout_minutes: 30,
            base_url: "https://bcs.example.com".to_string(),
            cookie_secure: true,
            env: "test".to_string(),
            success_redirect_path: "/".to_string(),
        };
        Arc::new(OAuthRouteState::new(
            service,
            JWT_SECRET,
            Arc::new(NoopUserIdentityPort),
            config,
            None,
            Some(Arc::new(vec!["https://workbench.example".to_string()])),
        ))
    }

    async fn get_auth_url(
        state: &Arc<OAuthRouteState>,
    ) -> axum::response::Response {
        auth_url_handler(State(state.clone()), HeaderMap::new())
            .await
            .into_response()
    }

    fn set_cookies(response: &axum::response::Response) -> Vec<String> {
        response
            .headers()
            .get_all(axum::http::header::SET_COOKIE)
            .iter()
            .map(|value| value.to_str().expect("ascii").to_string())
            .collect()
    }

    fn cookie_value<'a>(cookies: &'a [String], name: &str) -> Option<&'a str> {
        cookies.iter().find_map(|c| {
            c.strip_prefix(&format!("{name}="))
                .and_then(|rest| rest.split(';').next())
        })
    }

    fn request_with_cookie(_uri: &str, cookie: &str) -> HeaderMap {
        let mut headers = HeaderMap::new();
        headers.insert(
            axum::http::header::COOKIE,
            cookie.parse().expect("cookie header"),
        );
        headers
    }

    #[tokio::test]
    async fn legacy_auth_url_sets_challenge_cookie_and_preserves_body_shape() {
        let state = legacy_state(
            Arc::new(FakeSession::default()),
            Arc::new(FakePending::default()),
            Arc::new(FakeProvider {
                exchanges: Mutex::new(Vec::new()),
            }),
        );
        let response = get_auth_url(&state).await;
        assert_eq!(response.status(), StatusCode::OK);
        let cookies = set_cookies(&response);
        assert_eq!(cookies.len(), 1);
        assert!(cookies[0].starts_with("__Host-bcs_oauth_login="));
        for attr in ["HttpOnly", "Secure", "SameSite=Lax", "Path=/", "Max-Age=300"] {
            assert!(cookies[0].contains(attr), "{cookies:?} missing {attr}");
        }
        assert_eq!(
            response.headers().get(axum::http::header::CACHE_CONTROL),
            Some(&HeaderValue::from_static("no-store")),
            "login URL response must be no-store"
        );
        let bytes = to_bytes(response.into_body(), usize::MAX).await.expect("body");
        let body: serde_json::Value = serde_json::from_slice(&bytes).expect("json");
        let providers = body["providers"].as_array().expect("providers array");
        assert_eq!(providers.len(), 1);
        assert_eq!(providers[0]["name"], "google");
        let url = providers[0]["url"].as_str().expect("url");
        // Old body shape: redirect built from config.base_url + /auth/callback.
        assert!(url.contains("state="));
        // The nonce must never leak into the JSON body.
        let nonce = cookie_value(&cookies, "__Host-bcs_oauth_login").expect("nonce");
        assert!(!serde_json::to_string(&body).unwrap().contains(nonce));
    }

    #[tokio::test]
    async fn legacy_login_urls_pending_store_failure_maps_to_unavailable_without_cookies() {
        let state = legacy_state(
            Arc::new(FakeSession::default()),
            Arc::new(FakePending::fail_issue()),
            Arc::new(FakeProvider {
                exchanges: Mutex::new(Vec::new()),
            }),
        );
        let response = get_auth_url(&state).await;
        assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
        assert!(
            set_cookies(&response).is_empty(),
            "no challenge is sent when the pending store fails"
        );
    }

    async fn start_legacy_login(
        state: &Arc<OAuthRouteState>,
    ) -> (String, String) {
        let response = get_auth_url(state).await;
        let cookies = set_cookies(&response);
        let nonce = cookie_value(&cookies, CHALLENGE_COOKIE_SECURE)
            .expect("challenge cookie")
            .to_string();
        let bytes = to_bytes(response.into_body(), usize::MAX).await.unwrap();
        let body: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        let url = body["providers"][0]["url"].as_str().unwrap().to_string();
        let state_param = url
            .rsplit("state=")
            .next()
            .expect("state query param")
            .to_string();
        (nonce, state_param)
    }

    #[tokio::test]
    async fn legacy_callback_binds_the_browser_and_issues_both_cookies() {
        let session = Arc::new(FakeSession::default());
        let provider_exchanges = Mutex::new(Vec::new());
        let state = legacy_state(
            session.clone(),
            Arc::new(FakePending::default()),
            Arc::new(FakeProvider {
                exchanges: provider_exchanges,
            }),
        );
        let (nonce, url_state) = start_legacy_login(&state).await;

        // SAME, matching browser: challenge cookie + code + state.
        let response = callback_handler(
            State(state.clone()),
            Path("google".to_string()),
            axum::extract::Query(CallbackParams {
                code: Some("auth-code".to_string()),
                auth_code: None,
                state: url_state.clone(),
            }),
            request_with_cookie(
                "/auth/callback/google",
                &format!("{CHALLENGE_COOKIE_SECURE}={nonce}"),
            ),
        )
        .await
        .into_response();
        assert_eq!(response.status(), StatusCode::FOUND);
        assert_eq!(response.headers()["location"], "/");
        let cookies = set_cookies(&response);
        assert_eq!(
            cookies.len(),
            2,
            "session Set-Cookie AND challenge clear stay distinct: {cookies:?}"
        );
        assert!(cookies.iter().any(|c| c.starts_with("bcs_session=")));
        let clear = cookies
            .iter()
            .find(|c| c.starts_with("__Host-bcs_oauth_login="))
            .expect("challenge clear");
        assert!(clear.contains("Max-Age=0"));
        assert_eq!(session.installed(), 1);

        // Replay burns: the consumed batch cannot complete again.
        let replay = callback_handler(
            State(state.clone()),
            Path("google".to_string()),
            axum::extract::Query(CallbackParams {
                code: Some("auth-code".to_string()),
                auth_code: None,
                state: url_state,
            }),
            request_with_cookie(
                "/auth/callback/google",
                &format!("{CHALLENGE_COOKIE_SECURE}={nonce}"),
            ),
        )
        .await
        .into_response();
        assert_eq!(replay.status(), StatusCode::BAD_REQUEST);
        assert_eq!(session.installed(), 1);
    }


    #[tokio::test]
    async fn legacy_callback_without_binding_cookie_is_rejected_before_exchange() {
        let session = Arc::new(FakeSession::default());
        let exchanges = Arc::new(FakeProvider {
            exchanges: Mutex::new(Vec::new()),
        });
        let state = legacy_state(
            session.clone(),
            Arc::new(FakePending::default()),
            exchanges.clone(),
        );
        let (_nonce, url_state) = start_legacy_login(&state).await;

        let response = callback_handler(
            State(state.clone()),
            Path("google".to_string()),
            axum::extract::Query(CallbackParams {
                code: Some("auth-code".to_string()),
                auth_code: None,
                state: url_state,
            }),
            HeaderMap::new(),
        )
        .await
        .into_response();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "no cookie => invalid state");
        assert!(
            set_cookies(&response).is_empty(),
            "a cross-browser callback must not set or clear anything"
        );
        assert_eq!(
            to_bytes(response.into_body(), 1024)
                .await
                .unwrap()
                .to_vec(),
            b"invalid state",
            "old plain-text body preserved"
        );
        assert_eq!(session.installed(), 0);
        assert!(
            exchanges.exchanges.lock().unwrap().is_empty(),
            "the provider must NOT be exchanged for a cross-browser callback"
        );
    }

    #[tokio::test]
    async fn legacy_refresh_requires_origin_and_rotates_the_binding() {
        let session = Arc::new(FakeSession::default());
        let state = legacy_state(
            session.clone(),
            Arc::new(FakePending::default()),
            Arc::new(FakeProvider {
                exchanges: Mutex::new(Vec::new()),
            }),
        );
        let (nonce, url_state) = start_legacy_login(&state).await;
        let login = callback_handler(
            State(state.clone()),
            Path("google".to_string()),
            axum::extract::Query(CallbackParams {
                code: Some("auth-code".to_string()),
                auth_code: None,
                state: url_state,
            }),
            request_with_cookie(
                "/auth/callback/google",
                &format!("{CHALLENGE_COOKIE_SECURE}={nonce}"),
            ),
        )
        .await
        .into_response();
        let token = cookie_value(&set_cookies(&login), "bcs_session")
            .expect("session token")
            .to_string();

        // Missing Origin on a cookie-backed unsafe request → 403.
        let response = refresh_handler(
            State(state.clone()),
            request_with_cookie("/auth/refresh", &format!("bcs_session={token}")),
        )
        .await
        .into_response();
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
        assert_eq!(session.refreshes.lock().unwrap().len(), 0);

        // Untrusted Origin → 403.
        let mut origin_headers = request_with_cookie(
            "/auth/refresh",
            &format!("bcs_session={token}"),
        );
        origin_headers.insert(
            axum::http::header::ORIGIN,
            "https://attacker.example".parse().unwrap(),
        );
        let response = refresh_handler(State(state.clone()), origin_headers)
            .await
            .into_response();
        assert_eq!(response.status(), StatusCode::FORBIDDEN);

        // Trusted Origin → 200 + rotated cookie.
        let mut origin_headers = request_with_cookie(
            "/auth/refresh",
            &format!("bcs_session={token}"),
        );
        origin_headers.insert(
            axum::http::header::ORIGIN,
            "https://workbench.example".parse().unwrap(),
        );
        let response = refresh_handler(State(state.clone()), origin_headers)
            .await
            .into_response();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(session.refreshes.lock().unwrap().len(), 1);
        let rotated = format!("refreshed-{token}");
        assert!(
            set_cookies(&response)
                .iter()
                .any(|c| c.starts_with(&format!("bcs_session={rotated}"))),
            "the rotated token must be in the Set-Cookie"
        );

        // No cookie → 401 (old body).
        let response = refresh_handler(State(state.clone()), HeaderMap::new())
            .await
            .into_response();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn legacy_logout_without_cookie_is_idempotent_success() {
        let state = legacy_state(
            Arc::new(FakeSession::default()),
            Arc::new(FakePending::default()),
            Arc::new(FakeProvider {
                exchanges: Mutex::new(Vec::new()),
            }),
        );
        let response = logout_handler(State(state.clone()), HeaderMap::new())
            .await
            .into_response();
        assert_eq!(response.status(), StatusCode::OK);
        let cookies = set_cookies(&response);
        assert_eq!(cookies.len(), 1);
        assert!(cookies[0].starts_with("bcs_session=;"));
        assert!(cookies[0].contains("Max-Age=0"));
    }

    #[tokio::test]
    async fn legacy_logout_failure_is_an_error_response_not_success() {
        let session = Arc::new(FakeSession::default());
        session
            .fail_revoke
            .store(true, std::sync::atomic::Ordering::SeqCst);
        let state = legacy_state(
            session.clone(),
            Arc::new(FakePending::default()),
            Arc::new(FakeProvider {
                exchanges: Mutex::new(Vec::new()),
            }),
        );

        // Cookie-backed logout REQUIRES a trusted Origin first.
        let mut headers = request_with_cookie("/auth/logout", "bcs_session=tok");
        headers.insert(
            axum::http::header::ORIGIN,
            "https://attacker.example".parse().unwrap(),
        );
        let response = logout_handler(State(state.clone()), headers)
            .await
            .into_response();
        assert_eq!(response.status(), StatusCode::FORBIDDEN);

        // Trusted Origin but persistent store failure → error, no cookie.
        let mut headers = request_with_cookie("/auth/logout", "bcs_session=tok");
        headers.insert(
            axum::http::header::ORIGIN,
            "https://workbench.example".parse().unwrap(),
        );
        let response = logout_handler(State(state.clone()), headers)
            .await
            .into_response();
        assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
        assert!(
            set_cookies(&response).is_empty(),
            "a failed revocation must not promise cookie-clearing success"
        );
    }
