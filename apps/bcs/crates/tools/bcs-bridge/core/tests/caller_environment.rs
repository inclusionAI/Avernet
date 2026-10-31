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

#[test]
fn ordinary_bots_receive_only_their_own_configured_identity_and_nonempty_token() {
    let cfg = ProviderConfig::from_toml(r#"
provider_id = "p"
api_url = "https://bcs.example/cli"
listen = "127.0.0.1:0"
bcs_to_provider_token = "webhook-not-a-bot-token"
bot_runtime_token = "provider-not-a-bot-token"
[[bot]]
provider_bot_ref = "a"
bot_id = "bot-a"
token = "token-a"
engine = "codex"
cwd = "/tmp"
[[bot]]
provider_bot_ref = "b"
bot_id = "bot-b"
engine = "claude-code"
cwd = "/tmp"
[[bot]]
provider_bot_ref = "c"
bot_id = "bot-c"
token = "   "
engine = "claude-code"
cwd = "/tmp"
"#, std::path::Path::new("/tmp/bridge.toml")).unwrap();
    for bot in &cfg.bots {
        let env = cfg.execution_env(bot);
        assert!(env.contains(&("BCN_ACTOR_BOT_ID".into(), bot.bot_id.clone().unwrap())));
        assert!(env.contains(&("MOLTIS_BCS_URL".into(), "https://bcs.example/cli".into())));
        assert!(!env.iter().any(|(key, _)| key == "BCN_AUTH_MODE"));
        let token = env.iter().find(|(key, _)| key == "BCN_BOT_TOKEN").map(|(_, value)| value.as_str());
        assert_eq!(token, if bot.provider_bot_ref == "a" { Some("token-a") } else { None });
    }
}

#[test]
fn subbots_forward_an_available_token_without_changing_the_auth_mode() {
    let cfg = ProviderConfig::from_toml(r#"
provider_id = "p"
provider_auth = "agentpass-subbots"
listen = "127.0.0.1:0"
bcs_to_provider_token = "webhook"
[[bot]]
provider_bot_ref = "parent:a"
agent_code = "parent"
bot_slug = "a"
bot_id = "bot-a"
token = "token-a"
engine = "codex"
cwd = "/tmp"
"#, std::path::Path::new("/tmp/bridge.toml")).unwrap();
    let env = cfg.execution_env(&cfg.bots[0]);
    assert!(env.contains(&("BCN_BOT_TOKEN".into(), "token-a".into())));
    assert!(env.contains(&("BCN_AUTH_MODE".into(), "agentpass-subbots".into())));
}
