    //! Dispatch semantics over the REAL shared primitives: the real strict
    //! engine over the real memory identity store, the real gateway source,
    //! and a counting identity port where call counts matter.

    use std::sync::atomic::{AtomicUsize, Ordering};

    use super::*;
    use bcs_auth_api::session::{SessionScope, SessionStoreError};

    const SECRET_SESSION_KEY: &str = "test-session-signing-key";
    const SECRET_GATEWAY_KEY: &str = "test-gateway-signing-key-material";
    const TEST_ENV: &str = "test-env";

    struct CountingIdentityPort {
        inner: Arc<dyn AuthSessionIdentityPort>,
        by_hash_calls: Arc<AtomicUsize>,
    }

    #[async_trait]
    impl AuthSessionIdentityPort for CountingIdentityPort {
        async fn ensure_identity(
            &self,
            provider: &str,
            external_user_id: &str,
            name: Option<&str>,
            avatar: Option<&str>,
            env: &str,
        ) -> Result<String, SessionStoreError> {
            self.inner
                .ensure_identity(provider, external_user_id, name, avatar, env)
                .await
        }
        async fn read_session_revision(
            &self,
            scope: &SessionScope,
        ) -> Result<u64, SessionStoreError> {
            self.inner.read_session_revision(scope).await
        }
        async fn get_session_by_hash(
            &self,
            scope: &SessionScope,
            hash: &str,
            now: u64,
        ) -> Result<Option<SessionSnapshot>, SessionStoreError> {
            self.by_hash_calls.fetch_add(1, Ordering::SeqCst);
            self.inner.get_session_by_hash(scope, hash, now).await
        }
        async fn install_login_session(
            &self,
            command: bcs_auth_api::session::InstallSession,
        ) -> Result<bcs_auth_api::session::SessionWrite, SessionStoreError> {
            self.inner.install_login_session(command).await
        }
        async fn rotate_session(
            &self,
            command: bcs_auth_api::session::RotateSession,
        ) -> Result<bcs_auth_api::session::SessionWrite, SessionStoreError> {
            self.inner.rotate_session(command).await
        }
        async fn revoke_session(
            &self,
            scope: &SessionScope,
            session_id: &str,
        ) -> Result<bcs_auth_api::session::SessionRevoke, SessionStoreError> {
            self.inner.revoke_session(scope, session_id).await
        }
    }

    fn section(body: &str) -> ApiAuthConfig {
        toml::from_str(body).expect("test [api.auth] body parses")
    }

    fn fixtures() -> (
        Arc<CountingIdentityPort>,
        Arc<bcs_secret_local::InMemorySecretAccess>,
        Arc<AtomicUsize>,
    ) {
        let calls = Arc::new(AtomicUsize::new(0));
        let inner = crate::identity_wiring::memory_identity_ports().auth_session_identity;
        let port = Arc::new(CountingIdentityPort {
            inner,
            by_hash_calls: calls.clone(),
        });
        let access = Arc::new(bcs_secret_local::InMemorySecretAccess::with_entries([
            ("session-key".to_string(), String::new(), SECRET_SESSION_KEY.to_string()),
            ("gateway-key".to_string(), String::new(), SECRET_GATEWAY_KEY.to_string()),
            ("github-secret".to_string(), String::new(), "resolved".to_string()),
            ("google-secret".to_string(), String::new(), "resolved".to_string()),
            ("wechat-secret".to_string(), String::new(), "resolved".to_string()),
        ]));
        (port, access, calls)
    }

    /// One body fixture enabling a `chain` subset of the registered OAuth
    /// instances. The gateway table is present-but-NOT-enabled in the
    /// OAuth-only chains (registered tables may stay behind; their secrets
    /// are never read, and the other proofs hold by build-call counts).
    fn oauth_body(chain: &str) -> String {
        format!(
            r#"
chain = [{chain}]
public_base_url = "https://bcs.example.com/openapi/v1/auth"
session_signing_key_secret = "session-key"
success_redirect_path = "/"
trusted_browser_origins = ["https://workbench.example.com"]

[gateway]
signing_key_secret = "gateway-key"

[github]
client_id = "gh-cid"
client_secret_secret = "github-secret"

[google]
client_id = "g-cid"
client_secret_secret = "google-secret"

[wechat]
client_id = "wx-cid"
client_secret_secret = "wechat-secret"
"#
        )
    }

    /// Install a valid strict session for `provider` the same way the login
    /// entry does (`ensure_identity` on the same shared store, then the
    /// engine's install lifecycle), and return the `bcs_session` cookie
    /// header map carrying it plus the internal `user_id`.
    async fn session_cookie_headers(
        identities: &Arc<CountingIdentityPort>,
        provider: &str,
        env: &str,
        display_name: Option<&str>,
    ) -> (HeaderMap, String) {
        let engine = OAuthSessionEngine::new(
            bcs_jwt::OAuthSessionJwt::new(SECRET_SESSION_KEY),
            Arc::clone(identities) as Arc<dyn AuthSessionIdentityPort>,
            env.to_string(),
            3600,
        );
        let user_id = identities
            .ensure_identity(provider, "ext-login-1", display_name, None, env)
            .await
            .expect("test identity seeds");
        let scope = SessionScope {
            user_id: user_id.clone(),
            provider: provider.to_string(),
            env: env.to_string(),
        };
        let issued = engine
            .install(scope, display_name.map(str::to_string), unix_now())
            .await
            .expect("test session installs");
        let mut headers = HeaderMap::new();
        headers.insert(
            axum::http::header::COOKIE,
            axum::http::HeaderValue::from_str(&format!("{}={}", BCS_SESSION_COOKIE, issued.token))
                .expect("token is a header value"),
        );
        (headers, user_id)
    }

    #[tokio::test]
    async fn gateway_success_never_consults_the_oauth_cookie() {
        let (identities, access, calls) = fixtures();
        let built = assemble_v1_api_auth(
            &section(&oauth_body("\"gateway\", \"github\"")),
            &production_registrations(),
            Arc::clone(&identities) as Arc<dyn AuthSessionIdentityPort>,
            TEST_ENV.to_string(),
            access,
        )
        .await
        .expect("gateway-first chain assembles");
        let (mut headers, _user_id) =
            session_cookie_headers(&identities, "github", TEST_ENV, None).await;
        headers.insert(
            "x-avernet-principal",
            axum::http::HeaderValue::from_str(&craft_gateway_token()).unwrap(),
        );
        let verified = built.verifier.verify(&headers).await.expect("gateway wins");
        assert_eq!(
            verified.authentication_context.credential_kind,
            CredentialKind::GatewayPrincipalHeader
        );
        assert_eq!(
            verified.authentication_context.source, "gateway",
            "provenance is pinned by the successful source"
        );
        // Spec §7.1: a successful high-priority Gateway request never
        // inspects the cookie — zero strict session reads happened.
        assert_eq!(calls.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn shared_cookie_verifies_once_and_matches_its_own_source() {
        let (identities, access, calls) = fixtures();
        // Google is FIRST: the github session must skip it as Missing and
        // succeed at the github position with ONE strict verification.
        let built = assemble_v1_api_auth(
            &section(&oauth_body("\"google\", \"github\"")),
            &production_registrations(),
            Arc::clone(&identities) as Arc<dyn AuthSessionIdentityPort>,
            TEST_ENV.to_string(),
            access,
        )
        .await
        .expect("oauth chain assembles");
        let (headers, _user_id) =
            session_cookie_headers(&identities, "github", TEST_ENV, None).await;
        identities.ensure_identity(
            "github", "ext-login-1", None, Some("/avatars/cached.png"), TEST_ENV,
        ).await.expect("update display avatar");
        let verified = built.verifier.verify(&headers).await.expect("github matches");
        assert_eq!(verified.avatar.as_deref(), Some("/avatars/cached.png"),
            "the later matching source retains display data from the cached snapshot");
        assert_eq!(verified.authentication_context.source, "github");
        assert_eq!(
            verified.authentication_context.credential_kind,
            CredentialKind::OAuthSessionCookie
        );
        assert_eq!(
            calls.load(Ordering::SeqCst),
            1,
            "one request, one strict session read (the attempt cache)"
        );
    }

    #[tokio::test]
    async fn disabled_provider_session_is_missing_and_does_not_block_gateway() {
        for chain in ["\"google\", \"github\"", "\"google\", \"github\", \"gateway\""] {
            let (identities, access, calls) = fixtures();
            let built = assemble_v1_api_auth(
                &section(&oauth_body(chain)),
                &production_registrations(),
                identities.clone(),
                TEST_ENV.to_string(),
                access,
            ).await.expect("chain assembles");
            let (mut headers, _) = session_cookie_headers(&identities, "wechat", TEST_ENV, None).await;
            assert!(matches!(built.verifier.verify(&headers).await,
                Err(PrincipalVerificationError::Missing)));
            assert_eq!(calls.load(Ordering::SeqCst), 1, "cache Missing across OAuth positions");
            if chain.contains("gateway") {
                calls.store(0, Ordering::SeqCst);
                headers.insert("x-avernet-principal", craft_gateway_token().parse().unwrap());
                let identity = built.verifier.verify(&headers).await.expect("gateway remains usable");
                assert_eq!(identity.authentication_context.source, "gateway");
                assert_eq!(calls.load(Ordering::SeqCst), 1);
                headers.insert("cookie", "bcs_session=invalid".parse().unwrap());
                assert!(matches!(built.verifier.verify(&headers).await,
                    Err(PrincipalVerificationError::Invalid(_))), "invalid is still final");
            }
        }
    }

    #[tokio::test]
    async fn invalid_session_is_rejected_not_missing() {
        let (identities, access, _calls) = fixtures();
        let built = assemble_v1_api_auth(
            &section(&oauth_body("\"github\"")),
            &production_registrations(),
            Arc::clone(&identities) as Arc<dyn AuthSessionIdentityPort>,
            TEST_ENV.to_string(),
            access,
        )
        .await
        .expect("oauth chain assembles");
        let mut headers = HeaderMap::new();
        headers.insert(
            axum::http::header::COOKIE,
            axum::http::HeaderValue::from_static("bcs_session=not-a-real-jwt"),
        );
        assert!(matches!(
            built.verifier.verify(&headers).await,
            Err(PrincipalVerificationError::Invalid(_))
        ));
        // A duplicated cookie carrier is malformed, never "absent".
        let mut headers = HeaderMap::new();
        headers.insert(
            axum::http::header::COOKIE,
            axum::http::HeaderValue::from_static("bcs_session=a; bcs_session=b"),
        );
        assert!(matches!(
            built.verifier.verify(&headers).await,
            Err(PrincipalVerificationError::Invalid(_))
        ));
        // And no cookie at all keeps the position NotApplicable.
        assert!(matches!(
            built.verifier.verify(&HeaderMap::new()).await,
            Err(PrincipalVerificationError::Missing)
        ));
    }

    #[tokio::test]
    async fn store_outage_maps_to_dependency_unavailable() {
        struct UnavailablePort;
        #[async_trait]
        impl AuthSessionIdentityPort for UnavailablePort {
            async fn ensure_identity(
                &self,
                _provider: &str,
                _external_user_id: &str,
                _name: Option<&str>,
                _avatar: Option<&str>,
                _env: &str,
            ) -> Result<String, SessionStoreError> {
                Err(SessionStoreError::Unavailable)
            }
            async fn read_session_revision(
                &self,
                _scope: &SessionScope,
            ) -> Result<u64, SessionStoreError> {
                Err(SessionStoreError::Unavailable)
            }
            async fn get_session_by_hash(
                &self,
                _scope: &SessionScope,
                _hash: &str,
                _now: u64,
            ) -> Result<Option<SessionSnapshot>, SessionStoreError> {
                Err(SessionStoreError::Unavailable)
            }
            async fn install_login_session(
                &self,
                _command: bcs_auth_api::session::InstallSession,
            ) -> Result<bcs_auth_api::session::SessionWrite, SessionStoreError> {
                Err(SessionStoreError::Unavailable)
            }
            async fn rotate_session(
                &self,
                _command: bcs_auth_api::session::RotateSession,
            ) -> Result<bcs_auth_api::session::SessionWrite, SessionStoreError> {
                Err(SessionStoreError::Unavailable)
            }
            async fn revoke_session(
                &self,
                _scope: &SessionScope,
                _session_id: &str,
            ) -> Result<bcs_auth_api::session::SessionRevoke, SessionStoreError> {
                Err(SessionStoreError::Unavailable)
            }
        }
        let (_, access, _calls) = fixtures();
        let built = assemble_v1_api_auth(
            &section(&oauth_body("\"github\"")),
            &production_registrations(),
            Arc::new(UnavailablePort),
            TEST_ENV.to_string(),
            access,
        )
        .await
        .expect("assembly does not probe the store");
        // A REAL signature is required to reach the store probe: a garbage
        // token is an Invalid denial, and the dependency outage only
        // surfaces after the strict claim/env checks pass.
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_secs())
            .unwrap_or_default();
        let token = bcs_jwt::OAuthSessionJwt::new(SECRET_SESSION_KEY)
            .sign(&bcs_jwt::OAuthSessionClaims {
                sub: "user-outage".to_string(),
                src: "github".to_string(),
                env: TEST_ENV.to_string(),
                session_id: "sid-outage".to_string(),
                revision: 1,
                iat: now,
                exp: now + 600,
                name: None,
            })
            .expect("test outage token signs");
        let mut headers = HeaderMap::new();
        headers.insert(
            axum::http::header::COOKIE,
            axum::http::HeaderValue::from_str(&format!("{}={token}", BCS_SESSION_COOKIE))
                .expect("token header value"),
        );
        assert!(matches!(
            built.verifier.verify(&headers).await,
            Err(PrincipalVerificationError::DependencyUnavailable)
        ));
    }

    #[tokio::test]
    async fn username_falls_back_to_the_internal_id_when_no_trusted_name() {
        let (identities, access, _calls) = fixtures();
        let built = assemble_v1_api_auth(
            &section(&oauth_body("\"github\"")),
            &production_registrations(),
            Arc::clone(&identities) as Arc<dyn AuthSessionIdentityPort>,
            TEST_ENV.to_string(),
            access,
        )
        .await
        .expect("oauth chain assembles");
        let (headers, user_id) = session_cookie_headers(&identities, "github", TEST_ENV, None).await;
        let verified = built
            .verifier
            .verify(&headers)
            .await
            .expect("session accepted");
        let user = verified.caller.user.expect("human caller");
        assert_eq!(user.id, user_id);
        assert_eq!(
            user.username, user_id,
            "§7.3: the internal ID is the fallback display name"
        );
        assert_eq!(user.display_name, None);
        assert!(verified.caller.tenant.is_none(), "§7.3: tenant stays default");
    }

    #[tokio::test]
    async fn invalid_origin_config_is_a_startup_failure() {
        let (identities, access, _calls) = fixtures();
        let body = r#"
chain = ["github"]
public_base_url = "https://bcs.example.com/openapi/v1/auth"
session_signing_key_secret = "session-key"
success_redirect_path = "/"
trusted_browser_origins = ["https://ok.example.com", "https://bad origin.example.com"]

[github]
client_id = "gh-cid"
client_secret_secret = "github-secret"
"#;
        let outcome = assemble_v1_api_auth(
            &section(body),
            &production_registrations(),
            Arc::clone(&identities) as Arc<dyn AuthSessionIdentityPort>,
            TEST_ENV.to_string(),
            access,
        )
        .await;
        let error = match outcome {
            Err(error) => error,
            Ok(_) => panic!("an invalid origin configuration must refuse startup"),
        };
        assert!(
            error.contains("trusted browser origin"),
            "origin failure surfaces before any publish, got: {error}"
        );
    }

    fn craft_gateway_token() -> String {
        use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
        let mut header = Header::new(Algorithm::HS256);
        header.kid = Some("bare".to_string());
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_secs())
            .unwrap_or_default();
        let claims = serde_json::json!({
            "iss": "gateway",
            "aud": "bcs",
            "iat": now,
            "exp": now + 600,
            "principals": [
                {
                    "type": "user",
                    "tenant": "tenant-a",
                    "subject": { "id": "staff-1", "username": "alice" },
                }
            ],
        });
        encode(&header, &claims, &EncodingKey::from_secret(SECRET_GATEWAY_KEY.as_bytes()))
            .expect("test gateway token signs")
    }
