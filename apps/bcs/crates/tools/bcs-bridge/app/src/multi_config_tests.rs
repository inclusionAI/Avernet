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
    let edit=Edit {source:Document {groups:vec![legacy],v2:false,cli:CliSettings::default()},target:None,bot:None};
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
    let doc=Document {groups:vec![provider],v2:true,cli:CliSettings::default()};
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
    let edit=Edit {source:Document {groups:vec![first.clone(),unrelated.clone()],v2:true,cli:CliSettings::default()},target:Some(0),bot:Some(1)};
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
    let edit=Edit {source:Document {groups:vec![first.clone()],v2:false,cli:CliSettings::default()},target:Some(0),bot:Some(1)};
    let mut replacement=first.clone();replacement.insert("registration".into(),selected["registration"].clone());
    replacement.insert("bot".into(),toml::Value::Array(vec![selected.clone()]));
    let merged=edit.finish(replacement).unwrap();
    assert_eq!(merged["registration"],first["registration"]);
    assert_eq!(merged["bot"][0],first["bot"][0]);
    assert_eq!(merged["bot"][1],selected);
}

#[test]
fn replacement_api_resolution_matches_runtime_loading() {
    for v2 in [false, true] {
        for (provider_api, bot_api, expected) in [
            (None, None, "http://localhost/provider"),
            (Some("http://localhost/default"), None, "http://localhost/default"),
            (Some("http://localhost/default"), Some("http://localhost/bot"), "http://localhost/bot"),
        ] {
            let mut provider = group("a");
            provider.insert("api_url".into(), "http://localhost/provider".into());
            if let Some(api) = provider_api {
                provider.insert("registration".into(), toml::Value::Table(toml::Table::from_iter([("api_url".into(), api.into())])));
            }
            let mut metadata = toml::Table::from_iter([("provider_auth".into(), "static-bearer".into())]);
            if let Some(api) = bot_api { metadata.insert("api_url".into(), api.into()); }
            provider.get_mut("bot").unwrap().as_array_mut().unwrap()[0].as_table_mut().unwrap()
                .insert("registration".into(), metadata.into());
            let root = if v2 {
                toml::Table::from_iter([("schema_version".into(), 2.into()),
                    ("provider".into(), toml::Value::Array(vec![provider.into()]))])
            } else { provider };
            let dir = tempfile::tempdir().unwrap(); let path = dir.path().join("bridge.toml");
            let text = toml::to_string(&root).unwrap(); std::fs::write(&path, &text).unwrap();
            let loaded = load(&path, &crate::credentials::EnvCredentials).unwrap();
            let selected = read_for_registration(&path, "static-bearer", "a", "plugin", false, true, Some("bot-a")).unwrap();
            assert_eq!(loaded[0].registrations[0].api_url.as_deref(), Some(expected));
            assert_eq!(selected.settings.api_url, loaded[0].registrations[0].api_url);
            assert_eq!(selected.settings.provider_auth.as_deref(), Some("static-bearer"));
            assert_eq!(std::fs::read_to_string(&path).unwrap(), text);
        }
    }
}


#[test]
fn global_cli_endpoint_is_loaded_for_every_provider_but_legacy_api_url_is_not_runtime_endpoint() {
    let mut first = group("first");
    first.insert("api_url".into(), "http://registration.example".into());
    let second = group("second");
    let root = toml::Table::from_iter([
        ("schema_version".into(), 2.into()),
        ("cli".into(), toml::Value::Table(toml::Table::from_iter([
            ("api_base_url".into(), "http://runtime.example".into()),
        ]))),
        ("provider".into(), toml::Value::Array(vec![first.into(), second.into()])),
    ]);
    let document = Document::parse(&toml::to_string(&root).unwrap()).unwrap();
    assert_eq!(document.cli.api_base_url.as_deref(), Some("http://runtime.example"));
    let loaded = load_document(&document, Path::new("/tmp/config.toml"), &crate::credentials::EnvCredentials).unwrap();
    assert_eq!(loaded.len(), 2);
    for provider in loaded {
        assert_eq!(provider.config.cli_api_base_url.as_deref(), Some("http://runtime.example"));
        assert!(provider.config.execution_env(&provider.config.bots[0]).contains(
            &("BCS_API_BASE_URL".into(), "http://runtime.example".into())
        ));
        assert!(!provider.config.execution_env(&provider.config.bots[0]).iter()
            .any(|(key, _)| key == "MOLTIS_BCS_URL"));
    }
}

#[test]
fn compiled_cli_endpoint_is_used_when_document_has_no_override() {
    let document = Document::parse(&toml::to_string(&toml::Table::from_iter([
        ("provider_id".into(), "compiled".into()),
        ("mode".into(), "plugin".into()),
        ("state_path".into(), "/tmp/compiled.db".into()),
        ("plugin".into(), toml::Value::Table(toml::Table::from_iter([
            ("url".into(), "ws://localhost/ws/bot".into()),
        ]))),
        ("bot".into(), toml::Value::Array(vec![toml::Value::Table(toml::Table::from_iter([
            ("provider_bot_ref".into(), "worker".into()),
            ("bot_id".into(), "compiled-bot".into()),
            ("engine".into(), "codex".into()),
            ("cwd".into(), "/tmp".into()),
        ]))])),
    ])).unwrap()).unwrap();
    let loaded = load_document(&document, Path::new("/tmp/config.toml"), &crate::credentials::EnvCredentials).unwrap();
    let expected = option_env!("BCS_CLI_API_URL").filter(|value| !value.trim().is_empty());
    assert_eq!(loaded[0].config.cli_api_base_url.as_deref(), expected);
}

#[test]
fn legacy_document_accepts_global_cli_endpoint_and_legacy_registration_api_url() {
    let mut provider = group("legacy");
    provider.insert("api_url".into(), "http://registration.example".into());
    provider.insert("cli".into(), toml::Value::Table(toml::Table::from_iter([
        ("api_base_url".into(), "http://runtime.example".into()),
    ])));
    let document = Document::parse(&toml::to_string(&provider).unwrap()).unwrap();
    let loaded = load_document(&document, Path::new("/tmp/config.toml"), &crate::credentials::EnvCredentials).unwrap();
    assert_eq!(loaded[0].config.cli_api_base_url.as_deref(), Some("http://runtime.example"));
    assert_eq!(loaded[0].registrations[0].api_url.as_deref(), Some("http://registration.example"));
    assert!(!loaded[0].config.execution_env(&loaded[0].config.bots[0]).iter()
        .any(|(key, value)| key == "BCS_API_BASE_URL" && value == "http://registration.example"));
}

#[test]
fn cli_endpoint_resolution_prefers_configuration_then_compiled_default() {
    let configured = CliSettings { api_base_url: Some("http://configured.example".into()) };
    assert_eq!(resolve_cli_api_base_url(&configured, Some("http://compiled.example")), Some("http://configured.example".into()));
    assert_eq!(resolve_cli_api_base_url(&CliSettings::default(), Some("http://compiled.example")), Some("http://compiled.example".into()));
    assert_eq!(resolve_cli_api_base_url(&CliSettings { api_base_url: Some("  ".into()) }, Some("http://compiled.example")), Some("http://compiled.example".into()));
    assert_eq!(resolve_cli_api_base_url(&CliSettings::default(), None), None);
}

#[test]
fn cli_endpoint_is_preserved_when_registration_edits_v1_or_v2_documents() {
    let mut source = group("first");
    let cli = CliSettings { api_base_url: Some("http://runtime.example".into()) };
    let edit = Edit { source: Document { groups: vec![], v2: false, cli: cli.clone() }, target: None, bot: None };
    let v1 = edit.finish(source.clone()).unwrap();
    assert_eq!(Document::parse(&toml::to_string(&v1).unwrap()).unwrap().cli, cli);

    source.insert("provider_id".into(), "second".into());
    let edit = Edit { source: Document { groups: vec![group("first")], v2: true, cli: cli.clone() }, target: Some(0), bot: None };
    let v2 = edit.finish(source).unwrap();
    assert_eq!(Document::parse(&toml::to_string(&v2).unwrap()).unwrap().cli, cli);
}

#[test]
fn provider_level_cli_endpoint_is_rejected() {
    let mut provider = group("first");
    provider.insert("cli".into(), toml::Value::Table(toml::Table::from_iter([
        ("api_base_url".into(), "http://runtime.example".into()),
    ])));
    let root = toml::Table::from_iter([
        ("schema_version".into(), 2.into()),
        ("provider".into(), toml::Value::Array(vec![provider.into()])),
    ]);
    assert!(Document::parse(&toml::to_string(&root).unwrap()).is_err());
}
