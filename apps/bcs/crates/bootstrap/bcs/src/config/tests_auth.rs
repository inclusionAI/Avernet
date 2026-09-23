    use super::*;
    use super::tests_core::{safe_remove_var, safe_set_var};
    use secrecy::ExposeSecret;

    #[test]
    fn group_session_ws_signing_key_secret_can_be_configured() {
        let toml = r#"
            bots_base_dir = "/bots"

            [group_session_ws]
            signing_key_secret = "other_manual_teamclawgw_principal_signing_key"
        "#;

        let config: BcsConfig =
            toml::from_str(toml).expect("parse configurable group-session WebSocket secret name");

        assert_eq!(
            config.group_session_ws.signing_key_secret,
            "other_manual_teamclawgw_principal_signing_key"
        );
    }

    #[test]
    fn blank_group_session_ws_signing_key_secret_is_rejected() {
        let tmp = tempfile::TempDir::new().expect("temp config dir");
        std::fs::write(
            tmp.path().join("bcs-config.toml"),
            r#"
            bots_base_dir = "/bots"

            [group_session_ws]
            signing_key_secret = " "
            "#,
        )
        .expect("write config");

        let err = BcsConfig::try_load_with_env(Some(&tmp.path().to_path_buf()))
            .expect_err("blank group-session WebSocket secret name rejected");

        assert!(err.contains("group_session_ws.signing_key_secret must not be blank"));
    }

    #[test]
    fn test_config_auth_chain_section_parses() {
        let toml = r#"
            bots_base_dir = "/bots"
            [auth]
            chain = ["agentpass", "session"]
            require_authentication = true
            mock_user_id = "12345"
            allow_mock_headers = true
        "#;
        let config: BcsConfig = toml::from_str(toml).expect("parse [auth]");
        assert_eq!(config.auth.chain, vec!["agentpass", "session"]);
        assert!(config.auth.require_authentication);
        assert!(config.auth.allow_mock_headers);

        // resolve honors a non-empty chain verbatim.
        let resolved = crate::auth_wiring::resolve_auth_config(&config.auth, "test");
        assert_eq!(resolved.chain, vec!["agentpass", "session"]);
        assert!(resolved.require_authentication);
        assert_eq!(resolved.local.mock_user_id.as_deref(), Some("12345"));
        assert!(resolved.local.allow_mock_headers);
    }

    #[test]
    #[serial_test::serial]
    fn test_config_auth_mock_user_id_env_override() {
        // Save and clear env vars so the test is hermetic.
        let saved_user_id = std::env::var("BCS_MOCK_USER_ID").ok();
        let saved_nick_name = std::env::var("BCS_MOCK_USER_NICK_NAME").ok();
        let saved_auth_mock = std::env::var("BCS_AUTH_MOCK").ok();
        safe_remove_var("BCS_MOCK_USER_ID");
        safe_remove_var("BCS_MOCK_USER_NICK_NAME");
        safe_remove_var("BCS_AUTH_MOCK");

        let config = BcsConfig::default();
        // Without env var, mock_user_id is None.
        let resolved = crate::auth_wiring::resolve_auth_config(&config.auth, "test");
        assert!(resolved.local.mock_user_id.is_none());
        assert!(!resolved.local.allow_mock_headers);

        // With env var, it fills in when config is absent.
        safe_set_var("BCS_MOCK_USER_ID", "99999");
        safe_set_var("BCS_MOCK_USER_NICK_NAME", "EnvUser");
        let resolved = crate::auth_wiring::resolve_auth_config(&config.auth, "test");
        assert_eq!(resolved.local.mock_user_id.as_deref(), Some("99999"));
        assert_eq!(resolved.local.mock_user_name.as_deref(), Some("EnvUser"));
        safe_remove_var("BCS_MOCK_USER_ID");
        safe_remove_var("BCS_MOCK_USER_NICK_NAME");

        safe_set_var("BCS_AUTH_MOCK", "1");
        let resolved = crate::auth_wiring::resolve_auth_config(&config.auth, "test");
        assert!(resolved.local.allow_mock_headers);
        safe_remove_var("BCS_AUTH_MOCK");

        // Config value takes priority over env var.
        let toml = r#"
            bots_base_dir = "/bots"
            [auth]
            mock_user_id = "from_config"
        "#;
        let config: BcsConfig = toml::from_str(toml).unwrap();
        safe_set_var("BCS_MOCK_USER_ID", "from_env");
        let resolved = crate::auth_wiring::resolve_auth_config(&config.auth, "test");
        assert_eq!(resolved.local.mock_user_id.as_deref(), Some("from_config"));
        safe_remove_var("BCS_MOCK_USER_ID");

        // Restore original env vars.
        if let Some(v) = saved_user_id {
            safe_set_var("BCS_MOCK_USER_ID", v);
        }
        if let Some(v) = saved_nick_name {
            safe_set_var("BCS_MOCK_USER_NICK_NAME", v);
        }
        if let Some(v) = saved_auth_mock {
            safe_set_var("BCS_AUTH_MOCK", v);
        }
    }

    #[test]
    fn test_config_auth_chain_absent_falls_back_to_default() {
        let toml = r#"bots_base_dir = "/bots""#;
        let config: BcsConfig = toml::from_str(toml).expect("parse without [auth]");
        assert!(config.auth.chain.is_empty());

        // Empty chain → build-profile default applied in the composition root.
        let resolved = crate::auth_wiring::resolve_auth_config(&config.auth, "test");
        let expected = if cfg!(debug_assertions) {
            vec!["local".to_string()]
        } else {
            vec![
                "agentpass".to_string(),
                "cookie".to_string(),
                "session".to_string(),
            ]
        };
        assert_eq!(resolved.chain, expected);
        // The contract crate's default is now neutral (empty), not build-profile.
        assert!(bcs_auth_api::AuthConfig::default().chain.is_empty());
    }

    #[test]
    fn test_build_oauth_provider_known_kinds() {
        use bcs_config_api::ProviderSettings;

        let google = ProviderSettings {
            kind: None, // defaults to the instance name "google"
            client_id: "gid".to_string(),
            client_secret: None,
            client_secret_secret: None,
            private_key: None,
            alipay_public_key: None,
        };
        // Arc<dyn OAuthProvider> is not Debug, so match instead of .expect().
        match crate::auth_wiring::build_oauth_provider("google", &google) {
            Ok(p) => assert_eq!(p.name(), "google"),
            Err(e) => panic!("google provider should build: {e}"),
        }

        // Explicit kind decoupled from the instance name (multi-instance case).
        let gh = ProviderSettings {
            kind: Some("github".to_string()),
            client_id: "ghid".to_string(),
            client_secret: None,
            client_secret_secret: None,
            private_key: None,
            alipay_public_key: None,
        };
        match crate::auth_wiring::build_oauth_provider("github-partner", &gh) {
            Ok(p) => assert_eq!(p.name(), "github"),
            Err(e) => panic!("github provider should build: {e}"),
        }
    }

    #[test]
    fn test_build_oauth_provider_unknown_kind_errors() {
        use bcs_config_api::ProviderSettings;

        let cfg = ProviderSettings {
            kind: Some("facebook".to_string()),
            client_id: "id".to_string(),
            client_secret: None,
            client_secret_secret: None,
            private_key: None,
            alipay_public_key: None,
        };
        let err = match crate::auth_wiring::build_oauth_provider("fb", &cfg) {
            Ok(_) => panic!("unknown kind must error, not silently drop"),
            Err(e) => e,
        };
        assert!(err.contains("unknown provider kind"), "got: {err}");
        assert!(err.contains("facebook"), "got: {err}");
    }

    #[test]
    fn test_build_oauth_provider_empty_client_id_errors() {
        use bcs_config_api::ProviderSettings;

        let cfg = ProviderSettings {
            kind: None,
            client_id: "  ".to_string(),
            client_secret: None,
            client_secret_secret: None,
            private_key: None,
            alipay_public_key: None,
        };
        let err = match crate::auth_wiring::build_oauth_provider("google", &cfg) {
            Ok(_) => panic!("empty client_id must error"),
            Err(e) => e,
        };
        assert!(err.contains("client_id"), "got: {err}");
    }
