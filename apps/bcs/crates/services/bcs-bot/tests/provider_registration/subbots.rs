use super::*;

#[tokio::test]
async fn two_subbots_register_with_one_parent_and_distinct_refs() {
    let f = fixture_with_auth(None, true, None, ProviderAuthMode::AgentPassSubbots).await;
    let mut a = request(&f, ProviderRegistrationMode::Plugin);
    a.provider_bot_ref = "stable-ref:reviewer".into();
    let a = f.core.register(a).await.unwrap();
    let mut b = request(&f, ProviderRegistrationMode::Plugin);
    b.provider_bot_ref = "stable-ref:coder".into();
    let b = f.core.register(b).await.unwrap();
    assert_ne!(a.record.bot_uuid, b.record.bot_uuid);
    assert_eq!(a.record.provider_bot_ref, "stable-ref:reviewer");
    assert_eq!(b.record.provider_bot_ref, "stable-ref:coder");
    for id in [&a.record.bot_uuid, &b.record.bot_uuid] {
        assert_eq!(f.registry.try_get(id).await.unwrap().unwrap().capabilities.agent_code.as_deref(), Some("stable-ref"));
    }
    let mut duplicate = request(&f, ProviderRegistrationMode::Plugin);
    duplicate.provider_bot_ref = "stable-ref:reviewer".into();
    assert!(matches!(f.core.register(duplicate).await, Err(ServiceError::Conflict(_))));
}

#[tokio::test]
async fn subbot_registration_requires_complete_ref_before_writes() {
    let f = fixture_with_auth(None, true, None, ProviderAuthMode::AgentPassSubbots).await;
    assert!(f.core.register(request(&f, ProviderRegistrationMode::Plugin)).await.is_err());
    assert!(f.membership.get_provider_bot_by_ref(&f.provider, "stable-ref").await.unwrap().is_none());
}

#[tokio::test]
async fn subbot_authentication_requires_actor_for_parent_but_not_static_token() {
    let f = fixture_with_auth(None, true, None, ProviderAuthMode::AgentPassSubbots).await;
    let mut a = request(&f, ProviderRegistrationMode::Plugin);
    a.provider_bot_ref = "stable-ref:reviewer".into();
    let a = f.core.register(a).await.unwrap();
    let core = f.registry.as_ref().clone().with_bot_provider_repo(f.membership.clone());
    assert!(core.resolve_agent_bot("stable-ref", None).await.is_err());
    assert_eq!(core.resolve_agent_bot("stable-ref", Some(&a.record.bot_uuid)).await.unwrap().unwrap().bot.bot_id, a.record.bot_uuid);
    assert!(core.resolve_agent_bot("other-parent", Some(&a.record.bot_uuid)).await.is_err());
    assert_eq!(core.resolve_bot_token(&a.record.bot_token, None).await.unwrap().unwrap().bot.bot_id, a.record.bot_uuid);
    assert!(core.resolve_bot_token(&a.record.bot_token, Some("another-bot")).await.is_err());
}

#[tokio::test]
async fn actual_registration_rejects_duplicate_and_deleted_slug_without_replacing_identity() {
    let f = fixture_with_auth(None, true, None, ProviderAuthMode::AgentPassSubbots).await;
    let mut command = request(&f, ProviderRegistrationMode::Plugin);
    command.provider_bot_ref = "stable-ref:reviewer".into();
    let first = f.core.register(command.clone()).await.unwrap();
    assert!(matches!(f.core.register(command.clone()).await, Err(ServiceError::Conflict(_))));
    assert_eq!(f.membership.list_provider_bot_metadata(Some(&f.provider)).await.unwrap().len(), 1);
    assert_eq!(f.registry.resolve_bot_token(&first.record.bot_token, None).await.unwrap().unwrap().bot.bot_id, first.record.bot_uuid);
    f.registry.soft_delete(&first.record.bot_uuid).await;
    assert!(matches!(f.core.register(command).await, Err(ServiceError::Conflict(_))));
}

#[tokio::test]
async fn registration_uses_provider_auth_mode() {
    for mode in [ProviderAuthMode::AgentPass, ProviderAuthMode::AgentPassSubbots] {
        let f = fixture_with_auth(None, true, None, mode).await;
        let mut command = request(&f, ProviderRegistrationMode::Plugin);
        command.provider_bot_ref = "stable-ref:reviewer".into();
        let result = f.core.register(command).await.unwrap();
        assert_eq!(result.auth_mode, mode);
        if mode == ProviderAuthMode::AgentPassSubbots {
            assert_eq!(result.identity.agent_code.as_deref(), Some("stable-ref"));
            assert_eq!(result.record.provider_bot_ref, "stable-ref:reviewer");
            assert_eq!(result.identity.bot_slug.as_deref(), Some("reviewer"));
        } else {
            assert_eq!(result.record.provider_bot_ref, "stable-ref:reviewer");
            assert_eq!(result.identity.agent_code.as_deref(), Some("stable-ref:reviewer"));
            assert_eq!(result.identity.bot_slug, None);
        }
    }
}

#[tokio::test]
async fn concurrent_subbot_registration_of_same_slug_has_one_winner() {
    let f = fixture_with_auth(None, true, None, ProviderAuthMode::AgentPassSubbots).await;
    let mut command = request(&f, ProviderRegistrationMode::Plugin);
    command.provider_bot_ref = "stable-ref:reviewer".into();
    let (a, b) = tokio::join!(f.core.register(command.clone()), f.core.register(command));
    assert_eq!(usize::from(a.is_ok()) + usize::from(b.is_ok()), 1);
    assert!(matches!(a.err().or_else(|| b.err()), Some(ServiceError::Conflict(_))));
    assert_eq!(f.membership.list_provider_bot_metadata(Some(&f.provider)).await.unwrap().len(), 1);
}

#[tokio::test]
async fn provider_admin_subbots_ignore_legacy_ref_as_uuid_and_preserve_parent_on_patch() {
    let f = fixture_with_auth(None, true, None, ProviderAuthMode::AgentPassSubbots).await;
    let admin = ProviderCore::new(f.providers.clone(), f.providers.clone(), f.providers.clone(), f.registry.clone()).with_bot_provider_repo(f.membership.clone());
    let token = f.providers.get_credential_by_kind(&f.provider, "provider_admin").await.unwrap().unwrap().secret_value;
    let mut ids = vec![];
    for slug in ["reviewer", "coder"] {
        let params = bcs_service_api::RegisterProviderBotParams {
            bot_name: slug.into(), owners: vec!["alice".into()], provider_bot_ref: format!("parent:{slug}"),
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
    command.provider_bot_ref = "stable-ref:reviewer".into();
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
    command.provider_bot_ref = "stable-ref:a".into();
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
