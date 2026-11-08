use bcs_bridge_core::config::ProviderConfig;

#[test]
fn siblings_have_independent_execution_environments() {
    let cfg = ProviderConfig::from_toml(r#"
provider_id = "p"
provider_auth = "agentpass-subbots"
api_url = "https://bcn.example"
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
    let a = cfg.execution_env(&cfg.bots[0]);
    let b = cfg.execution_env(&cfg.bots[1]);
    assert!(a.contains(&("BCN_ACTOR_BOT_ID".into(), "bot-a".into())));
    assert!(b.contains(&("BCN_ACTOR_BOT_ID".into(), "bot-b".into())));
    assert!(a.contains(&("BCN_AUTH_MODE".into(), "agentpass-subbots".into())));
    assert!(a.contains(&("BCS_API_BASE_URL".into(), "https://bcn.example".into())));
    assert!(!a.iter().any(|(key, _)| key == "BCN_BOT_TOKEN"));
}
