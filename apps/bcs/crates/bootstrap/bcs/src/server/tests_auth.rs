//! Tests mod split out from server.rs: gateway_principal_tests.

    use super::*;

use super::*;
    use bcs_secret_local::InMemorySecretAccess;

    fn trust_config() -> crate::config::GatewayPrincipalConfig {
        crate::config::GatewayPrincipalConfig::default()
    }

    #[test]
    fn configured_invite_token_secret_is_preserved() {
        let mut config = BcsConfig::default();
        config.invite.token_secret = Some("configured-invite-secret".to_string());

        assert_eq!(
            resolve_invite_token_secret(&config),
            b"configured-invite-secret"
        );
    }

    #[tokio::test]
    async fn all_config_secret_references_resolve_together() {
        use bcs_config_api::{OAuthSettings, ProviderSettings};
        use std::collections::BTreeMap;
        let mut config = BcsConfig::default();
        config.auth_sdk.secret_key_secret = Some("auth".into());
        config.llm.api_key_secret = Some("llm".into());
        config.bcsfuse.authorization_ref = Some("bcsfuse".into());
        config.invite.token_secret_secret = Some("invite".into());
        config.session_files.share.token_secret_secret = Some("share".into());
        let mut account = config.dingtalk_accounts.first().cloned().unwrap_or_default();
        account.client_secret_secret = Some("ding".into());
        config.dingtalk_accounts = vec![account];
        let logger = ding_logger::GroupLoggerConfig { enabled: true, client_id: "id".into(), client_secret: String::new(), client_secret_secret: Some("logger".into()), group_ids: vec!["g".into()] };
        config.group_logger = Some(logger.clone());
        config.human_notify.providers = vec![bcs_config_api::HumanNotifyProviderConfig {
            name: "dingtalk".to_string(),
            enabled: true,
            options: {
                let mut human_options = BTreeMap::new();
                human_options.insert(
                    "client_secret_secret".to_string(),
                    serde_json::Value::String("human".into()),
                );
                human_options
            },
        }];
        let mut providers = BTreeMap::new();
        providers.insert("google".into(), ProviderSettings { kind: None, client_id: "id".into(), client_secret: None, client_secret_secret: Some("oauth".into()), private_key: None, alipay_public_key: None });
        config.auth.oauth = Some(OAuthSettings { providers, ..OAuthSettings::default() });
        let access = InMemorySecretAccess::with_entries([
            ("auth", String::new(), "auth-value".into()), ("llm", String::new(), "llm-value".into()),
            ("bcsfuse", String::new(), "bcsfuse-value".into()),
            ("invite", String::new(), "invite-value".into()), ("share", String::new(), "share-value".into()),
            ("ding", String::new(), "ding-value".into()), ("logger", String::new(), "logger-value".into()),
            ("oauth", String::new(), "oauth-value".into()), ("human", String::new(), "human-value".into()),
        ]);
        resolve_config_secrets(&mut config, &access).await.unwrap();
        assert_eq!(config.auth_sdk.secret_key.as_deref(), Some("auth-value"));
        assert_eq!(config.llm.api_key.as_ref().map(|v| v.expose_secret().as_str()), Some("llm-value"));
        assert_eq!(config.bcsfuse.auth_token(), Some("bcsfuse-value"));
        assert_eq!(config.invite.token_secret.as_deref(), Some("invite-value"));
        assert_eq!(config.session_files.share.token_secret.as_deref(), Some("share-value"));
        assert_eq!(config.dingtalk_accounts[0].client_secret.as_ref().map(|v| v.expose_secret().as_str()), Some("ding-value"));
        assert_eq!(config.group_logger.as_ref().unwrap().client_secret, "logger-value");
        let dingtalk = &config.human_notify.providers[0];
        assert_eq!(
            dingtalk.options["client_secret"],
            serde_json::Value::String("human-value".into())
        );
        assert!(!dingtalk.options.contains_key("client_secret_secret"));
        assert_eq!(config.auth.oauth.as_ref().unwrap().providers["google"].client_secret.as_ref().map(|v| v.expose_secret().as_str()), Some("oauth-value"));
    }

    #[tokio::test]
    async fn token_secret_reference_resolves_and_rejects_missing_or_empty() {
        let access = InMemorySecretAccess::with_entries([("invite-key", String::new(), "resolved".to_string())]);
        let resolved = resolve_token_secret_secret(Some(" invite-key "), &access, "invite.token_secret_secret")
            .await
            .expect("secret resolves");
        assert_eq!(resolved.as_deref(), Some("resolved"));

        let missing = resolve_token_secret_secret(Some("missing"), &InMemorySecretAccess::new(), "invite.token_secret_secret")
            .await
            .expect_err("missing secret fails");
        assert!(missing.to_string().contains("invite.token_secret_secret"));

        let empty_access = InMemorySecretAccess::with_entries([("empty", String::new(), "  ".to_string())]);
        let empty = resolve_token_secret_secret(Some("empty"), &empty_access, "invite.token_secret_secret")
            .await
            .expect_err("empty secret fails");
        assert!(empty.to_string().contains("is empty"));

        assert_eq!(resolve_token_secret_secret(None, &InMemorySecretAccess::new(), "field").await.unwrap(), None);
        let access = InMemorySecretAccess::with_entries([("auth", String::new(), "auth-value".to_string())]);
        assert_eq!(resolve_secret_value(Some(" auth "), &access, "auth_sdk.secret_key_secret").await.unwrap().as_deref(), Some("auth-value"));
        assert!(resolve_secret_value(Some("missing"), &InMemorySecretAccess::new(), "llm.api_key_secret").await.is_err());
    }

    #[tokio::test]
    async fn config_secret_resolution_preserves_legacy_values_without_references() {
        use std::collections::BTreeMap;
        let mut config = BcsConfig::default();
        config.auth_sdk.secret_key = Some("legacy-auth".into());
        config.llm.api_key = Some(Secret::new("legacy-llm".into()));
        config.invite.token_secret = Some("legacy-invite".into());
        config.session_files.share.token_secret = Some("legacy-share".into());
        config.human_notify.providers = vec![bcs_config_api::HumanNotifyProviderConfig {
            name: "dingtalk".to_string(),
            enabled: true,
            options: {
                let mut options = BTreeMap::new();
                options.insert("client_secret".into(), serde_json::Value::String("legacy-human".into()));
                options
            },
        }];

        resolve_config_secrets(&mut config, &InMemorySecretAccess::new()).await.unwrap();

        assert_eq!(config.auth_sdk.secret_key.as_deref(), Some("legacy-auth"));
        assert_eq!(config.llm.api_key.as_ref().map(|v| v.expose_secret().as_str()), Some("legacy-llm"));
        assert_eq!(config.invite.token_secret.as_deref(), Some("legacy-invite"));
        assert_eq!(config.session_files.share.token_secret.as_deref(), Some("legacy-share"));
        assert_eq!(config.human_notify.providers[0].options["client_secret"], serde_json::Value::String("legacy-human".into()));
    }

    #[tokio::test]
    async fn generic_secret_options_resolve_and_literal_values_win() {
        use std::collections::BTreeMap;
        let mut config = BcsConfig::default();
        let mut signing_key_options = BTreeMap::new();
        signing_key_options.insert(
            "signing_key_secret".to_string(),
            serde_json::Value::String("principal-key".into()),
        );
        let mut literal_options = BTreeMap::new();
        literal_options.insert(
            "client_secret".to_string(),
            serde_json::Value::String("literal".into()),
        );
        literal_options.insert(
            "client_secret_secret".to_string(),
            serde_json::Value::String("ding".into()),
        );
        config.human_notify.providers = vec![
            bcs_config_api::HumanNotifyProviderConfig {
                name: "work_order".to_string(),
                enabled: true,
                options: signing_key_options,
            },
            bcs_config_api::HumanNotifyProviderConfig {
                name: "dingtalk".to_string(),
                enabled: true,
                options: literal_options,
            },
        ];
        let access = InMemorySecretAccess::with_entries([
            ("principal-key", String::new(), "principal-key-value".into()),
            ("ding", String::new(), "ding-value".into()),
        ]);

        resolve_config_secrets(&mut config, &access).await.unwrap();

        let work_order = &config.human_notify.providers[0];
        assert_eq!(
            work_order.options["signing_key"],
            serde_json::Value::String("principal-key-value".into())
        );
        assert!(!work_order.options.contains_key("signing_key_secret"));
        let dingtalk = &config.human_notify.providers[1];
        assert_eq!(
            dingtalk.options["client_secret"],
            serde_json::Value::String("literal".into()),
            "literal value wins over the reference"
        );
        assert!(
            dingtalk.options.contains_key("client_secret_secret"),
            "unresolved reference is kept untouched"
        );
    }

    #[test]
    fn legacy_token_secret_is_preserved_without_secret_reference() {
        let mut config = BcsConfig::default();
        config.invite.token_secret = Some("legacy-invite".to_string());
        assert_eq!(resolve_invite_token_secret(&config), b"legacy-invite");
    }

    #[test]
    fn gateway_principal_material_must_be_explicit_and_non_blank() {
        for material in [None, Some(""), Some("   ")] {
            assert!(matches!(
                gateway_principal_signing_key(material),
                Err(crate::BcsError::InvalidConfig(message))
                    if message.contains("Gateway Principal signing key")
            ));
        }
    }

    #[test]
    fn explicit_gateway_principal_material_is_accepted() {
        assert_eq!(
            gateway_principal_signing_key(Some("explicit-test-key")).expect("explicit material"),
            "explicit-test-key"
        );
    }

    #[tokio::test]
    async fn gateway_principal_signing_key_can_come_from_secret_access() {
        let mut config = trust_config();
        config.signing_key_secret =
            Some("other_manual_teamclawgw_principal_signing_key".to_string());
        let access: Arc<dyn bcs_service_api::port::SecretAccessPort> =
            Arc::new(InMemorySecretAccess::with_entries([(
                "other_manual_teamclawgw_principal_signing_key",
                "teamclawgw".to_string(),
                "mist-test-signing-key".to_string(),
            )]));

        let result = build_gateway_principal_verifier_from_secret_access(&config, access).await;

        if let Err(error) = result {
            let message = error.to_string();
            assert!(!message.contains("mist-test-signing-key"));
            panic!("Mist-backed Gateway Principal signing key must be accepted: {message}");
        }
    }

    #[test]
    fn blank_gateway_principal_trust_or_lookup_config_is_rejected() {
        for field in ["issuers", "audience", "key_id", "signing_key_env"] {
            let mut config = trust_config();
            match field {
                "issuers" => config.issuers = vec![" ".to_string()],
                "audience" => config.audience = " ".to_string(),
                "key_id" => config.key_id = " ".to_string(),
                "signing_key_env" => config.signing_key_env = " ".to_string(),
                _ => unreachable!("known trust config field"),
            }
            assert!(matches!(
                build_gateway_principal_verifier(&config, Some("explicit-test-key")),
                Err(crate::BcsError::InvalidConfig(_))
            ));
        }
        // Empty issuer list and duplicate issuers are also rejected.
        let mut empty = trust_config();
        empty.issuers = vec![];
        assert!(matches!(
            build_gateway_principal_verifier(&empty, Some("explicit-test-key")),
            Err(crate::BcsError::InvalidConfig(_))
        ));
        let mut duplicate = trust_config();
        duplicate.issuers = vec!["gateway".to_string(), "gateway".to_string()];
        assert!(matches!(
            build_gateway_principal_verifier(&duplicate, Some("explicit-test-key")),
            Err(crate::BcsError::InvalidConfig(_))
        ));
    }

    #[tokio::test]
    async fn group_session_websocket_signing_key_is_required_and_non_empty() {
        let missing: Arc<dyn bcs_service_api::port::SecretAccessPort> =
            Arc::new(InMemorySecretAccess::new());
        let config = GroupSessionWsConfig::default();
        let missing_error = match build_group_session_token_port(&config, missing).await {
            Ok(_) => panic!("missing group-session WebSocket key must fail"),
            Err(error) => error,
        };
        assert!(matches!(missing_error, crate::BcsError::InvalidConfig(_)));
        assert!(missing_error.to_string().contains(
            "group_session_ws.signing_key_secret 'bcn-group-session-ws-jwt' is required"
        ));

        let empty: Arc<dyn bcs_service_api::port::SecretAccessPort> =
            Arc::new(InMemorySecretAccess::with_entries([(
                config.signing_key_secret.clone(),
                String::new(),
                "   ".to_string(),
            )]));
        let empty_error = match build_group_session_token_port(&config, empty).await {
            Ok(_) => panic!("empty group-session WebSocket key must fail"),
            Err(error) => error,
        };
        assert!(matches!(empty_error, crate::BcsError::InvalidConfig(_)));
        assert!(!empty_error.to_string().contains("   "));
    }

    #[tokio::test]
    async fn explicit_group_session_websocket_signing_key_is_accepted() {
        let secret_material = "test-only-group-session-key-at-least-32-bytes";
        let config = GroupSessionWsConfig {
            signing_key_secret: "other_manual_teamclawgw_principal_signing_key".to_string(),
        };
        let access: Arc<dyn bcs_service_api::port::SecretAccessPort> =
            Arc::new(InMemorySecretAccess::with_entries([(
                config.signing_key_secret.clone(),
                String::new(),
                secret_material.to_string(),
            )]));

        let result = build_group_session_token_port(&config, access).await;

        if let Err(error) = result {
            let message = error.to_string();
            assert!(!message.contains(secret_material));
            panic!("explicit group-session WebSocket key must be accepted: {message}");
        }
    }
