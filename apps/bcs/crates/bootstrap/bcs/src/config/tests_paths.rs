    use super::*;
    use super::tests_core::{safe_remove_var, safe_set_var};
    use std::path::PathBuf;

    #[test]
    fn from_file_expands_env_references_in_string_fields() {
        safe_set_var("BCS_TEST_FROM_FILE_TOKEN", "expanded-via-from-file");
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("bcs-config.toml");
        std::fs::write(
            &path,
            r#"
bots_base_dir = "/bots"
botchat_url = "${BCS_TEST_FROM_FILE_TOKEN}"
"#,
        )
        .unwrap();

        let config = BcsConfig::from_file(&path).unwrap();
        assert_eq!(config.botchat_url.as_deref(), Some("expanded-via-from-file"));
        safe_remove_var("BCS_TEST_FROM_FILE_TOKEN");
    }

    #[test]
    fn from_file_errors_when_a_required_env_reference_is_unset() {
        safe_remove_var("BCS_TEST_FROM_FILE_MISSING");
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("bcs-config.toml");
        std::fs::write(
            &path,
            r#"
bots_base_dir = "/bots"
botchat_url = "${BCS_TEST_FROM_FILE_MISSING}"
"#,
        )
        .unwrap();

        let result = BcsConfig::from_file(&path);
        let err = result.unwrap_err().to_string();
        assert!(
            err.contains("BCS_TEST_FROM_FILE_MISSING"),
            "error should name the missing var: {err}"
        );
    }

    #[test]
    fn from_file_unknown_ext_json_with_unset_env_reports_env_error_not_toml() {
        // A valid JSON document with an unset `${VAR}` must surface the env
        // error directly, not be retried as TOML and mis-reported. Guards the
        // `_` (unknown extension) branch swallowing expansion errors.
        safe_remove_var("BCS_TEST_UNKNOWN_EXT_VAR");
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("bcs-config"); // no extension -> unknown-ext branch
        std::fs::write(
            &path,
            r#"{"bots_base_dir":"/bots","botchat_url":"${BCS_TEST_UNKNOWN_EXT_VAR}"}"#,
        )
        .unwrap();

        let err = BcsConfig::from_file(&path).unwrap_err().to_string();
        assert!(
            err.contains("BCS_TEST_UNKNOWN_EXT_VAR"),
            "should surface the unset env-var error, got: {err}"
        );
        assert!(
            !err.to_lowercase().contains("toml"),
            "should not misreport as a TOML parse error, got: {err}"
        );
    }

    #[test]
    fn test_config_loader_resolves_local_paths_relative_to_config_root() {
        let dir = tempfile::tempdir().unwrap();
        let config_dir = dir.path().join("configs");
        std::fs::create_dir_all(&config_dir).unwrap();
        std::fs::write(
            config_dir.join("bcs-config.toml"),
            r#"
bots_base_dir = "/bots"

[collaboration.templates]
base_dir = "seeds/collaboration-templates"

[[manifest.bundles]]
name = "bcsPanel"
type = "file"
file = "assets/panel/dist/index.umd.js"
"#,
        )
        .unwrap();

        let config = BcsConfig::try_load_with_env(Some(&config_dir)).unwrap();
        let expected_root = std::fs::canonicalize(dir.path()).unwrap();
        let expected_manifest_file = expected_root
            .join("assets/panel/dist/index.umd.js")
            .display()
            .to_string();
        let expected_template_dir = expected_root.join("seeds/collaboration-templates");

        assert_eq!(
            config.manifest.bundles[0].file.as_deref(),
            Some(expected_manifest_file.as_str())
        );
        assert_eq!(
            config.collaboration.templates.base_dir,
            expected_template_dir
        );
    }

    #[test]
    fn test_config_loader_resolves_local_paths_relative_to_custom_config_dir() {
        let dir = tempfile::tempdir().unwrap();
        let config_dir = dir.path().join("bcs-config");
        std::fs::create_dir_all(&config_dir).unwrap();
        std::fs::write(
            config_dir.join("bcs-config.toml"),
            r#"
bots_base_dir = "/bots"

[collaboration.templates]
base_dir = "seeds/collaboration-templates"

[[manifest.bundles]]
name = "bcsPanel"
type = "file"
file = "assets/panel/dist/index.umd.js"
"#,
        )
        .unwrap();

        let config = BcsConfig::try_load_with_env(Some(&config_dir)).unwrap();
        let expected_root = std::fs::canonicalize(&config_dir).unwrap();
        let expected_manifest_file = expected_root
            .join("assets/panel/dist/index.umd.js")
            .display()
            .to_string();
        let expected_template_dir = expected_root.join("seeds/collaboration-templates");

        assert_eq!(
            config.manifest.bundles[0].file.as_deref(),
            Some(expected_manifest_file.as_str())
        );
        assert_eq!(
            config.collaboration.templates.base_dir,
            expected_template_dir
        );
    }
