use bcs_bridge_core::config::ProviderConfig;

#[test]
fn siblings_have_independent_execution_environments() {
    let mut cfg = ProviderConfig::from_toml(r#"
provider_id = "p"
provider_auth = "agentpass-subbots"
mode = "plugin"
state_path = "/tmp/state"
[plugin]
url = "wss://bcn.example/ws/bot"
[[bot]]
provider_bot_ref = "parent:a"
agent_code = "parent"
bot_slug = "a"
bot_id = "bot-a"
engine = "codex"
cwd = "/tmp"
[[bot]]
provider_bot_ref = "parent:b"
agent_code = "parent"
bot_slug = "b"
bot_id = "bot-b"
engine = "claude-code"
cwd = "/tmp"
"#, std::path::Path::new("/tmp/bridge.toml")).unwrap();
    cfg.cli_api_base_url = Some("https://bcn.example".into());
    let a = cfg.execution_env(&cfg.bots[0]);
    let b = cfg.execution_env(&cfg.bots[1]);
    assert!(a.contains(&("BCN_ACTOR_BOT_ID".into(), "bot-a".into())));
    assert!(b.contains(&("BCN_ACTOR_BOT_ID".into(), "bot-b".into())));
    assert!(a.contains(&("BCN_AUTH_MODE".into(), "agentpass-subbots".into())));
    assert!(a.contains(&("BCS_API_BASE_URL".into(), "https://bcn.example".into())));
    assert!(!a.iter().any(|(key, _)| key == "BCN_BOT_TOKEN"));
}


#[test]
fn ordinary_bots_receive_only_their_own_nonblank_identity_and_token() {
    let cfg = ProviderConfig::from_toml(r#"
provider_id = "p"
mode = "plugin"
state_path = "/tmp/state"
[plugin]
url = "wss://bcn.example/ws/bot"
[[bot]]
provider_bot_ref = "worker"
bot_id = "bot-ordinary"
token = "  ordinary-token  "
engine = "codex"
cwd = "/tmp"
"#, std::path::Path::new("/tmp/bridge.toml")).unwrap();
    let env = cfg.execution_env(&cfg.bots[0]);
    assert!(env.contains(&("BCN_ACTOR_BOT_ID".into(), "bot-ordinary".into())));
    assert!(env.contains(&("BCN_BOT_TOKEN".into(), "  ordinary-token  ".into())));
    assert!(!env.iter().any(|(key, _)| key == "BCN_AUTH_MODE"));
    assert!(!env.iter().any(|(key, _)| key == "BCS_API_BASE_URL"));
    assert!(!env.iter().any(|(key, _)| key == "MOLTIS_BCS_URL"));
}

#[test]
fn blank_identity_and_token_do_not_override_the_parent_environment() {
    let cfg = ProviderConfig::from_toml(r#"
provider_id = "p"
listen = "127.0.0.1:21000"
bcs_to_provider_token = "provider-token"
state_path = "/tmp/state"
[[bot]]
provider_bot_ref = "worker"
token = "\t"
engine = "codex"
cwd = "/tmp"
"#, std::path::Path::new("/tmp/bridge.toml")).unwrap();
    let env = cfg.execution_env(&cfg.bots[0]);
    assert!(!env.iter().any(|(key, _)| key == "BCN_ACTOR_BOT_ID"));
    assert!(!env.iter().any(|(key, _)| key == "BCN_BOT_TOKEN"));
}
