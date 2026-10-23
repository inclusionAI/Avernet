use super::*;

fn group(id: &str) -> toml::Table {
    toml::from_str(&format!("provider_id='{id}'\nmode='plugin'\nstate_path='{id}.db'\n[plugin]\nurl='ws://localhost/ws/bot'\n[[bot]]\nprovider_bot_ref='same'\nbot_id='bot-{id}'\nengine='codex'\ncwd='/tmp'\n")).unwrap()
}
#[test]
fn unknown_versions_mixed_envelopes_and_duplicate_provider_ids_fail_closed() {
    for text in ["schema_version=3", "provider=[]", "schema_version=2\nprovider=[]",
        "schema_version=2\nprovider_id='legacy'\n[[provider]]\nprovider_id='a'",
        "schema_version=2\n[[provider]]\nprovider_id='same'\n[[provider]]\nprovider_id='same'"] {
        assert!(Document::parse(text).is_err(), "{text}");
    }
}
#[test]
fn promotion_keeps_effective_legacy_database_and_does_not_invent_identity_policy() {
    let mut legacy=group("legacy"); legacy.remove("state_path");
    let edit=Edit {source:Document {groups:vec![legacy],v2:false},target:None,bot:None};
    let merged=edit.finish(group("new")).unwrap();
    let text=toml::to_string(&merged).unwrap(); let doc=Document::parse(&text).unwrap();
    assert_eq!(doc.groups[0]["state_path"].as_str(),Some("~/.bcn-bridge/bridge-state.sqlite3"));
    assert!(doc.groups[0]["bot"][0].get("registration").is_none());
    let loaded=load_document(&doc,Path::new("/tmp/custom/bridge.toml"),&crate::credentials::EnvCredentials).unwrap();
    assert_eq!(loaded[0].config.state_path,dirs::home_dir().unwrap().join(".bcn-bridge/bridge-state.sqlite3"));
    assert!(loaded[0].registrations[0].provider_auth.is_none());
}
#[test]
fn legacy_loading_never_rewrites_configuration() {
    let dir=tempfile::tempdir().unwrap(); let path=dir.path().join("bridge.toml");
    let text=format!("# preserve comment\n{}",toml::to_string(&group("legacy")).unwrap());
    std::fs::write(&path,&text).unwrap();
    let loaded=load(&path,&crate::credentials::EnvCredentials).unwrap();
    assert_eq!(loaded.len(),1); assert_eq!(std::fs::read_to_string(&path).unwrap(),text);
}
#[test]
fn per_bot_resolver_metadata_is_separate_from_provider_runtime_auth() {
    let mut provider=group("a"); provider.insert("provider_auth".into(),"agentpass".into());
    provider.insert("api_url".into(),"http://localhost".into());
    let bot=provider.get_mut("bot").unwrap().as_array_mut().unwrap()[0].as_table_mut().unwrap();
    bot.insert("registration".into(),toml::Value::Table(toml::from_str("provider_auth='static-bearer'").unwrap()));
    let doc=Document {groups:vec![provider],v2:true};
    let loaded=load_document(&doc,Path::new("/tmp/config.toml"),&crate::credentials::EnvCredentials).unwrap();
    assert_eq!(loaded[0].config.provider_auth.as_deref(),Some("agentpass"));
    assert_eq!(loaded[0].registrations[0].provider_auth.as_deref(),Some("static-bearer"));
    assert_eq!(loaded[0].registrations[0].api_url.as_deref(),Some("http://localhost"));
}
#[test]
fn replacement_splices_only_selected_bot_and_keeps_other_groups() {
    let mut first=group("a"); let mut second=first["bot"][0].clone();
    second.as_table_mut().unwrap().insert("bot_id".into(),"bot-second".into());
    first.get_mut("bot").unwrap().as_array_mut().unwrap().push(second.clone());
    let unrelated=group("b");
    let edit=Edit {source:Document {groups:vec![first.clone(),unrelated.clone()],v2:true},target:Some(0),bot:Some(1)};
    second.as_table_mut().unwrap().insert("model".into(),"replacement-model".into());
    let mut replacement=first.clone();replacement.insert("bot".into(),toml::Value::Array(vec![second.clone()]));
    let merged=edit.finish(replacement).unwrap();
    assert_eq!(merged["provider"][0]["bot"][0],first["bot"][0]);
    assert_eq!(merged["provider"][0]["bot"][1],second);
    assert_eq!(merged["provider"][1],toml::Value::Table(unrelated));
}

#[test]
fn replacing_an_overridden_resolver_never_changes_sibling_defaults() {
    let mut first=group("a");
    first.insert("registration".into(),toml::Value::Table(toml::from_str("provider_auth='static-bearer'").unwrap()));
    let mut selected=first["bot"][0].clone();
    selected.as_table_mut().unwrap().insert("registration".into(),toml::Value::Table(toml::from_str("provider_auth='agentpass-local'").unwrap()));
    first.get_mut("bot").unwrap().as_array_mut().unwrap().push(selected.clone());
    let edit=Edit {source:Document {groups:vec![first.clone()],v2:false},target:Some(0),bot:Some(1)};
    let mut replacement=first.clone();replacement.insert("registration".into(),selected["registration"].clone());
    replacement.insert("bot".into(),toml::Value::Array(vec![selected.clone()]));
    let merged=edit.finish(replacement).unwrap();
    assert_eq!(merged["registration"],first["registration"]);
    assert_eq!(merged["bot"][0],first["bot"][0]);
    assert_eq!(merged["bot"][1],selected);
}
