use bcs_domain::ProviderAuthMode;

#[test]
fn agentpass_subbots_has_an_explicit_wire_name() {
    let mode: ProviderAuthMode = serde_json::from_str("\"agentpass-subbots\"").unwrap();
    assert_eq!(serde_json::to_string(&mode).unwrap(), "\"agentpass-subbots\"");
    assert!(serde_json::from_str::<ProviderAuthMode>("\"agent_pass_subbots\"").is_err());
}

#[test]
fn existing_agentpass_wire_name_is_preserved() {
    assert_eq!(serde_json::to_string(&ProviderAuthMode::AgentPass).unwrap(), "\"agentpass\"");
}

#[test]
fn siblings_parse_the_parent_and_preserve_complete_refs() {
    let mode = ProviderAuthMode::AgentPassSubbots;
    let a = bcs_domain::provider_identity::normalize(mode, "poolab_P:reviewer").unwrap();
    let b = bcs_domain::provider_identity::normalize(mode, "poolab_P:coder").unwrap();
    assert_eq!(a.agent_code.as_deref(), Some("poolab_P"));
    assert_eq!(b.agent_code, a.agent_code);
    assert_eq!(a.provider_bot_ref, "poolab_P:reviewer");
    assert_eq!(b.provider_bot_ref, "poolab_P:coder");
    assert_eq!(a.bot_slug.as_deref(), Some("reviewer"));
}

#[test]
fn parent_may_contain_colons_and_last_colon_delimits_slug() {
    let identity = bcs_domain::provider_identity::normalize(
        ProviderAuthMode::AgentPassSubbots, "poolab:region:P:reviewer",
    ).unwrap();
    assert_eq!(identity.agent_code.as_deref(), Some("poolab:region:P"));
    assert_eq!(identity.provider_bot_ref, "poolab:region:P:reviewer");
}

#[test]
fn new_mode_rejects_malformed_complete_refs() {
    for reference in ["parent", ":a", "parent:", "parent:Bad", "parent: a", "parent:-a",
        "bad parent:a", "bad/parent:a", "父身份:a", " parent:a", "parent:a "] {
        assert!(bcs_domain::provider_identity::normalize(
            ProviderAuthMode::AgentPassSubbots, reference,
        ).is_err(), "accepted {reference:?}");
    }
    for reference in [format!("parent:{}", "a".repeat(33)), format!("{}:{}", "p".repeat(96), "a".repeat(32))] {
        assert!(bcs_domain::provider_identity::normalize(
            ProviderAuthMode::AgentPassSubbots, &reference,
        ).is_err());
    }
}

#[test]
fn new_mode_accepts_exact_length_boundaries() {
    let reference = format!("{}:{}", "p".repeat(95), "a".repeat(32));
    let identity = bcs_domain::provider_identity::normalize(
        ProviderAuthMode::AgentPassSubbots, &reference,
    ).unwrap();
    assert_eq!(identity.provider_bot_ref, reference);
    assert_eq!(identity.bot_slug.as_ref().unwrap().len(), 32);
}

#[test]
fn ordinary_modes_preserve_the_entire_ref_without_parsing() {
    let a = bcs_domain::provider_identity::normalize(ProviderAuthMode::AgentPass, "parent:Bad").unwrap();
    assert_eq!(a.provider_bot_ref, "parent:Bad");
    assert_eq!(a.agent_code.as_deref(), Some("parent:Bad"));
    assert_eq!(a.bot_slug, None);
    let s = bcs_domain::provider_identity::normalize(ProviderAuthMode::StaticBearer, "ref:a").unwrap();
    assert_eq!(s.provider_bot_ref, "ref:a");
    assert_eq!(s.agent_code, None);
    assert_eq!(s.bot_slug, None);
}
