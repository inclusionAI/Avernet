use bcs_domain::ProviderAuthMode;

#[test]
fn agentpass_subbots_has_an_explicit_wire_name() {
    let mode: ProviderAuthMode = serde_json::from_str("\"agentpass-subbots\"")
        .expect("the new Provider mode must deserialize");
    assert_eq!(serde_json::to_string(&mode).unwrap(), "\"agentpass-subbots\"");
    assert!(serde_json::from_str::<ProviderAuthMode>("\"agent_pass_subbots\"").is_err());
}

#[test]
fn existing_agentpass_wire_name_is_preserved() {
    assert_eq!(serde_json::to_string(&ProviderAuthMode::AgentPass).unwrap(), "\"agentpass\"");
}

#[test]
fn siblings_keep_the_parent_and_have_distinct_refs() {
    let mode = serde_json::from_str::<ProviderAuthMode>("\"agentpass-subbots\"").unwrap();
    let a = bcs_domain::provider_identity::normalize(mode, "poolab_P", Some("reviewer")).unwrap();
    let b = bcs_domain::provider_identity::normalize(mode, "poolab_P", Some("coder")).unwrap();
    assert_eq!(a.agent_code.as_deref(), Some("poolab_P"));
    assert_eq!(b.agent_code, a.agent_code);
    assert_eq!(a.provider_bot_ref, "poolab_P:reviewer");
    assert_eq!(b.provider_bot_ref, "poolab_P:coder");
}

#[test]
fn new_mode_requires_a_valid_slug_and_bounded_ref() {
    let mode = serde_json::from_str::<ProviderAuthMode>("\"agentpass-subbots\"").unwrap();
    for slug in [None, Some(""), Some("Bad"), Some("a:b"), Some(" a"), Some("-a")] {
        assert!(bcs_domain::provider_identity::normalize(mode, "parent", slug).is_err());
    }
    assert!(bcs_domain::provider_identity::normalize(mode, &"p".repeat(128), Some("a")).is_err());
}

#[test]
fn ordinary_modes_ignore_slug_without_changing_the_identity() {
    let a = bcs_domain::provider_identity::normalize(ProviderAuthMode::AgentPass, "parent", Some("bad:slug")).unwrap();
    assert_eq!(a.provider_bot_ref, "parent");
    assert_eq!(a.agent_code.as_deref(), Some("parent"));
    assert_eq!(a.bot_slug, None);
    let s = bcs_domain::provider_identity::normalize(ProviderAuthMode::StaticBearer, "ref", None).unwrap();
    assert_eq!(s.agent_code, None);
}
