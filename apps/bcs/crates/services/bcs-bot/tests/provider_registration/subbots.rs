use super::*;

#[tokio::test]
async fn two_subbots_register_with_one_parent_and_distinct_refs() {
    let f = fixture_with_auth(None, true, None, ProviderAuthMode::AgentPassSubbots).await;
    let mut a = request(&f, ProviderRegistrationMode::Plugin);
    a.bot_slug = Some("reviewer".into());
    let a = f.core.register(a).await.unwrap();
    let mut b = request(&f, ProviderRegistrationMode::Plugin);
    b.bot_slug = Some("coder".into());
    let b = f.core.register(b).await.unwrap();
    assert_ne!(a.record.bot_uuid, b.record.bot_uuid);
    assert_eq!(a.record.provider_bot_ref, "stable-ref:reviewer");
    assert_eq!(b.record.provider_bot_ref, "stable-ref:coder");
    for id in [&a.record.bot_uuid, &b.record.bot_uuid] {
        assert_eq!(f.registry.try_get(id).await.unwrap().unwrap().capabilities.agent_code.as_deref(), Some("stable-ref"));
    }
    let mut duplicate = request(&f, ProviderRegistrationMode::Plugin);
    duplicate.bot_slug = Some("reviewer".into());
    assert!(matches!(f.core.register(duplicate).await, Err(ServiceError::Conflict(_))));
}

#[tokio::test]
async fn subbot_registration_requires_slug_before_writes() {
    let f = fixture_with_auth(None, true, None, ProviderAuthMode::AgentPassSubbots).await;
    assert!(f.core.register(request(&f, ProviderRegistrationMode::Plugin)).await.is_err());
    assert!(f.membership.get_provider_bot_by_ref(&f.provider, "stable-ref").await.unwrap().is_none());
}

#[tokio::test]
async fn subbot_authentication_requires_actor_for_parent_but_not_static_token() {
    let f = fixture_with_auth(None, true, None, ProviderAuthMode::AgentPassSubbots).await;
    let mut a = request(&f, ProviderRegistrationMode::Plugin);
    a.bot_slug = Some("reviewer".into());
    let a = f.core.register(a).await.unwrap();
    let core = f.registry.as_ref().clone().with_bot_provider_repo(f.membership.clone());
    assert!(core.resolve_agent_bot("stable-ref", None).await.is_err());
    assert_eq!(core.resolve_agent_bot("stable-ref", Some(&a.record.bot_uuid)).await.unwrap().unwrap().bot.bot_id, a.record.bot_uuid);
    assert!(core.resolve_agent_bot("other-parent", Some(&a.record.bot_uuid)).await.is_err());
    assert_eq!(core.resolve_bot_token(&a.record.bot_token, None).await.unwrap().unwrap().bot.bot_id, a.record.bot_uuid);
    assert!(core.resolve_bot_token(&a.record.bot_token, Some("another-bot")).await.is_err());
}

#[tokio::test]
async fn precheck_reports_slug_occupancy_without_registering_a_bot() {
    let f = fixture_with_auth(None, true, None, ProviderAuthMode::AgentPassSubbots).await;
    let mut command = request(&f, ProviderRegistrationMode::Plugin);
    command.bot_slug = Some("reviewer".into());
    let first = f.core.check(command.clone()).await.unwrap();
    assert!(first.available);
    assert_eq!(first.identity.provider_bot_ref, "stable-ref:reviewer");
    f.core.register(command.clone()).await.unwrap();
    assert!(!f.core.check(command).await.unwrap().available);
}

#[tokio::test]
async fn provider_admin_subbots_ignore_legacy_ref_as_uuid_and_preserve_parent_on_patch() {
    let f = fixture_with_auth(None, true, None, ProviderAuthMode::AgentPassSubbots).await;
    let admin = ProviderCore::new(f.providers.clone(), f.providers.clone(), f.providers.clone(), f.registry.clone()).with_bot_provider_repo(f.membership.clone());
    let token = f.providers.get_credential_by_kind(&f.provider, "provider_admin").await.unwrap().unwrap().secret_value;
    let mut ids = vec![];
    for slug in ["reviewer", "coder"] {
        let params = bcs_service_api::RegisterProviderBotParams {
            bot_name: slug.into(), owners: vec!["alice".into()], provider_bot_ref: "parent".into(), bot_slug: Some(slug.into()),
            bot_uuid: Some("parent".into()), connection_mode: bcs_service_api::ProviderBotConnectionMode::Plugin, ..Default::default()
        };
        let (binding, token) = admin.register_provider_bot_with_bot_uuid(&f.provider, &token, params).await.unwrap();
        assert!(token.is_some());
        assert_ne!(binding.bot_uuid, "parent");
        assert_eq!(binding.provider_bot_ref, format!("parent:{slug}"));
        assert_eq!(f.registry.try_get(&binding.bot_uuid).await.unwrap().unwrap().capabilities.agent_code.as_deref(), Some("parent"));
        ids.push(binding.bot_uuid);
    }
    assert_ne!(ids[0], ids[1]);
    admin.update_provider_bot(&f.provider,&token,"parent:reviewer",Some("Renamed".into()),None,None,None,None,None).await.unwrap();
    assert_eq!(f.registry.try_get(&ids[0]).await.unwrap().unwrap().capabilities.agent_code.as_deref(),Some("parent"));
}

#[tokio::test]
async fn provider_callback_uses_actor_and_allows_subbot_static_token() {
    let f = fixture_with_auth(Some("https://example.test/hook"), true, None, ProviderAuthMode::AgentPassSubbots).await;
    let mut command = request(&f, ProviderRegistrationMode::Gateway);
    command.bot_slug = Some("reviewer".into());
    let bot = f.core.register(command).await.unwrap();
    let registry = Arc::new(f.registry.as_ref().clone().with_bot_provider_repo(f.membership.clone()));
    let admin = ProviderCore::new(f.providers.clone(), f.providers.clone(), f.providers.clone(), registry).with_bot_provider_repo(f.membership.clone());
    assert!(admin.authenticate_agentpass_event(&f.provider, "stable-ref").await.is_err());
    assert_eq!(admin.authenticate_agentpass_actor_event(&f.provider, "stable-ref", &bot.record.bot_uuid).await.unwrap().bot_uuid, bot.record.bot_uuid);
    assert!(admin.authenticate_agentpass_actor_event(&f.provider, "other", &bot.record.bot_uuid).await.is_err());
    assert_eq!(admin.authenticate_static_bearer_event(&f.provider, &bot.record.bot_token).await.unwrap().bot_uuid, bot.record.bot_uuid);
}

#[tokio::test]
async fn generic_writes_cannot_reparent_a_provider_bot() {
    let f = fixture_with_auth(None, true, None, ProviderAuthMode::AgentPassSubbots).await;
    let mut command = request(&f, ProviderRegistrationMode::Plugin);
    command.bot_slug = Some("a".into());
    let bot = f.core.register(command).await.unwrap();
    let core = f.registry.as_ref().clone().with_bot_provider_repo(f.membership.clone());
    let mut caps = core.try_get(&bot.record.bot_uuid).await.unwrap().unwrap().capabilities;
    caps.agent_code = None;
    core.save_to_storage(&bot.record.bot_uuid, &caps).await.unwrap();
    assert_eq!(core.try_get(&bot.record.bot_uuid).await.unwrap().unwrap().capabilities.agent_code.as_deref(), Some("stable-ref"));
    caps.agent_code = Some("other".into());
    assert!(core.save_to_storage(&bot.record.bot_uuid, &caps).await.is_err());
    assert!(core.register(bot.record.bot_uuid.clone(), caps).await.is_err());
}
