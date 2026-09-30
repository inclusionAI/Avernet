use bcs_config_api::{LlmConfig, LlmProviderType, StructuredOutputMode};
use secrecy::ExposeSecret;

#[test]
fn llm_config_deserializes_from_toml() {
    let config: LlmConfig = toml::from_str(
        r#"
type = "openai_compatible"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
api_key = "test-openai-key"
model = "gpt-4.1-mini"
timeout_ms = 120000
temperature = 0
max_tokens = 4096
structured_output = "json_schema"
"#,
    )
    .expect("llm config should parse");

    assert!(config.is_enabled());
    assert_eq!(config.provider_type, LlmProviderType::OpenAiCompatible);
    assert_eq!(config.base_url, "https://api.openai.com/v1");
    assert_eq!(config.api_key_env.as_deref(), Some("OPENAI_API_KEY"));
    assert_eq!(config.model, "gpt-4.1-mini");
    assert_eq!(config.timeout_ms, 120_000);
    assert_eq!(config.temperature, 0.0);
    assert_eq!(config.max_tokens, 4_096);
    assert_eq!(config.structured_output, StructuredOutputMode::JsonSchema);
    assert_eq!(
        config.api_key.as_ref().map(|secret| secret.expose_secret().as_str()),
        Some("test-openai-key")
    );
}

#[test]
fn llm_config_has_safe_defaults() {
    let config = LlmConfig::default();

    assert!(!config.is_enabled());
    assert_eq!(config.provider_type, LlmProviderType::None);
    assert_eq!(config.base_url, "https://api.openai.com/v1");
    assert_eq!(config.api_key_env.as_deref(), Some("OPENAI_API_KEY"));
    assert_eq!(config.model, "gpt-4.1-mini");
    assert_eq!(config.timeout_ms, 120_000);
    assert_eq!(config.temperature, 0.0);
    assert_eq!(config.max_tokens, 4_096);
    assert_eq!(config.structured_output, StructuredOutputMode::JsonSchema);
    assert!(config.api_key.is_none());
}

#[test]
fn llm_config_accepts_linked_provider_type() {
    let config: LlmConfig = toml::from_str(
        r#"
type = "internal"
model = "internal-model"
"#,
    )
    .expect("linked provider config should parse");

    assert_eq!(
        config.provider_type,
        LlmProviderType::Other("internal".to_string())
    );
    assert!(config.is_enabled());
    assert_eq!(config.model, "internal-model");
}

#[test]
fn llm_config_accepts_anthropic_provider_type() {
    let config: LlmConfig = toml::from_str(
        r#"
type = "anthropic"
base_url = "https://api.anthropic.com/v1"
api_key_env = "ANTHROPIC_API_KEY"
model = "claude-sonnet-4-6"
structured_output = "json_schema"
"#,
    )
    .expect("anthropic config should parse");

    assert_eq!(config.provider_type, LlmProviderType::Anthropic);
    assert_eq!(config.base_url, "https://api.anthropic.com/v1");
    assert_eq!(config.api_key_env.as_deref(), Some("ANTHROPIC_API_KEY"));
    assert_eq!(config.model, "claude-sonnet-4-6");
    assert_eq!(config.structured_output, StructuredOutputMode::JsonSchema);
}
