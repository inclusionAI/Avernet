use super::*;

#[tokio::test]
async fn id_validation_and_provider_isolation_reject_before_fetch() {
    let (chain, _, fetcher, _) = fixture().await;
    chain.db.execute(DbStatement::new("UPDATE bcs_providers SET slug='orders' WHERE provider_id='provider-a'")).await.unwrap();
    let cards = Arc::new(DbA2aBotStore::sqlite(chain.db.clone(), bcs_config::resolve_env_str()));
    let core = Arc::new(A2aBotCore::new(cards, chain.provider_repo.clone()));
    let service = A2aBotManagement::new(fetcher.providers.clone(), core, fetcher.clone());
    for id in ["", " id", "id ", "id\n"] {
        assert_eq!(code(service.get_agent_by_id("test-admin", id).await.unwrap_err()), Code::InvalidRequest);
    }
    for operation in ["get", "update", "refresh"] {
        let result = match operation {
            "get" => service.get_agent_by_id("test-admin", "missing").await,
            "update" => service.update_agent_by_id("test-admin", "missing", A2aCardSource::Url("https://example.org/card.json".into())).await,
            _ => service.refresh_agent_by_id("test-admin", "missing").await,
        };
        assert_eq!(code(result.unwrap_err()), Code::BotNotFound);
    }
    assert_eq!(fetcher.calls.load(Ordering::SeqCst), 0);

    let (chain, service) = public_fixture().await;
    let created = service.register_agent(request(card("隔离测试", None))).await.unwrap();
    for mutation in ["provider_id='another-provider'", "is_deleted=1", "connection_mode='plugin'", "agent_card=NULL"] {
        chain.db.execute(DbStatement::with_params(format!("UPDATE bcs_bots SET {mutation} WHERE bot_uuid=?"), vec![created.bot_id.clone().into()])).await.unwrap();
        for operation in ["get", "update", "refresh"] {
            let result = match operation {
                "get" => service.get_agent_by_id("test-admin", &created.bot_id).await,
                "update" => service.update_agent_by_id("test-admin", &created.bot_id, A2aCardSource::Url("https://example.org/card.json".into())).await,
                _ => service.refresh_agent_by_id("test-admin", &created.bot_id).await,
            };
            assert_eq!(code(result.unwrap_err()), Code::BotNotFound, "{mutation}: {operation}");
        }
        chain.db.execute(DbStatement::with_params(
            "UPDATE bcs_bots SET provider_id='provider-a',is_deleted=0,connection_mode='gateway',agent_card=? WHERE bot_uuid=?",
            vec![created.agent_card.to_string().into(), created.bot_id.clone().into()])).await.unwrap();
    }
}

#[tokio::test]
async fn source_key_recovery_is_exact_provider_scoped_and_rename_stable() {
    let (chain, service) = public_fixture().await;
    let mut command = request(card("来源查找", None));
    command.provider_bot_ref = "001Ab".into();
    let created = service.register_agent(command).await.unwrap();
    for key in ["001ab", "1Ab", "missing"] {
        assert!(service.find_agents_by_provider_ref("test-admin", key).await.unwrap().is_empty());
    }
    for key in ["", " 001Ab", "001Ab\n", &"x".repeat(257)] {
        assert_eq!(code(service.find_agents_by_provider_ref("test-admin", key).await.unwrap_err()), Code::InvalidRequest);
    }
    assert!(service.find_agents_by_provider_ref("wrong-admin", "001Ab").await.is_err());
    let found = service.find_agents_by_provider_ref("test-admin", "001Ab").await.unwrap();
    assert_eq!(found.len(), 1);
    assert_eq!(found[0].bot_id, created.bot_id);
    service.update_agent_by_id("test-admin", &created.bot_id, A2aCardSource::Inline(card("来源改名", Some("001Ab")))).await.unwrap();
    assert_eq!(service.find_agents_by_provider_ref("test-admin", "001Ab").await.unwrap()[0].agent_name, "来源改名");
    for mutation in ["provider_id='other'", "is_deleted=1", "connection_mode='plugin'"] {
        chain.db.execute(DbStatement::with_params(format!("UPDATE bcs_bots SET {mutation} WHERE bot_uuid=?"), vec![created.bot_id.clone().into()])).await.unwrap();
        assert!(service.find_agents_by_provider_ref("test-admin", "001Ab").await.unwrap().is_empty());
        chain.db.execute(DbStatement::with_params("UPDATE bcs_bots SET provider_id='provider-a',is_deleted=0,connection_mode='gateway' WHERE bot_uuid=?", vec![created.bot_id.clone().into()])).await.unwrap();
    }
}

struct PausedFetcher {
    entered: tokio::sync::Notify,
    release: tokio::sync::Notify,
    calls: AtomicUsize,
}
#[async_trait]
impl A2aCardPort for PausedFetcher {
    async fn validate(&self, raw: serde_json::Value, _: bool) -> ServiceResult<ValidatedA2aCard> {
        Ok(ValidatedA2aCard { interface: select_a2a_interface(&raw, |_, _| true).unwrap(), raw })
    }
    async fn fetch(&self, _: &str, _: bool) -> ServiceResult<ValidatedA2aCard> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        self.entered.notify_one();
        self.release.notified().await;
        Ok(validated())
    }
}
async fn paused_fixture() -> (RegistrationChain, Arc<A2aBotManagement>, Arc<PausedFetcher>) {
    let (chain, _, fetcher, registration) = fixture().await;
    chain.db.execute(DbStatement::new("UPDATE bcs_providers SET slug='orders' WHERE provider_id='provider-a'")).await.unwrap();
    let cards = Arc::new(DbA2aBotStore::sqlite(chain.db.clone(), bcs_config::resolve_env_str()).with_registry(&chain.bot_repo));
    let core = Arc::new(A2aBotCore::new(cards, chain.provider_repo.clone()));
    let paused = Arc::new(PausedFetcher { entered: Default::default(), release: Default::default(), calls: AtomicUsize::new(0) });
    let service = Arc::new(A2aBotManagement::new(fetcher.providers.clone(), core, paused.clone())
        .with_registration(registration, chain.registry_core.clone()));
    (chain, service, paused)
}

#[tokio::test]
async fn rename_during_fetch_keeps_id_target_and_does_not_refetch() {
    let (_, service, fetcher) = paused_fixture().await;
    let first = service.register_agent(request(card("原始名称", None))).await.unwrap();
    let updating = {
        let service = service.clone();
        let id = first.bot_id.clone();
        tokio::spawn(async move { service.update_agent_by_id("test-admin", &id, A2aCardSource::Url("https://example.org/card.json".into())).await })
    };
    fetcher.entered.notified().await;
    service.update_agent_by_id("test-admin", &first.bot_id, A2aCardSource::Inline(card("并发改名", None))).await.unwrap();
    let other = service.register_agent(request(card("原始名称", None))).await.unwrap();
    fetcher.release.notify_one();
    let updated = updating.await.unwrap().unwrap();
    assert_eq!(updated.bot_id, first.bot_id);
    assert_eq!(updated.agent_name, "Remote");
    assert_eq!(service.get_agent_by_id("test-admin", &other.bot_id).await.unwrap().agent_name, "原始名称");
    assert_eq!(fetcher.calls.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn refresh_does_not_overwrite_explicit_update_during_fetch() {
    let (chain, service, fetcher) = paused_fixture().await;
    let first = service.register_agent(request(card("刷新测试", None))).await.unwrap();
    chain.db.execute(DbStatement::with_params("UPDATE bcs_bots SET agent_card_url='https://example.org/card.json' WHERE bot_uuid=?", vec![first.bot_id.clone().into()])).await.unwrap();
    let refreshing = {
        let service = service.clone();
        let id = first.bot_id.clone();
        tokio::spawn(async move { service.refresh_agent_by_id("test-admin", &id).await })
    };
    fetcher.entered.notified().await;
    let explicit = card("显式更新", None);
    service.update_agent_by_id("test-admin", &first.bot_id, A2aCardSource::Inline(explicit.clone())).await.unwrap();
    fetcher.release.notify_one();
    assert_eq!(code(refreshing.await.unwrap().unwrap_err()), Code::RefreshSuperseded);
    assert_eq!(service.get_agent_by_id("test-admin", &first.bot_id).await.unwrap().agent_card, explicit);
    assert_eq!(fetcher.calls.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn deletion_or_binding_change_during_fetch_never_writes() {
    for mutation in ["is_deleted=1", "provider_bot_ref='changed'"] {
        let (chain, service, fetcher) = paused_fixture().await;
        let first = service.register_agent(request(card("目标变化", None))).await.unwrap();
        let updating = {
            let service = service.clone();
            let id = first.bot_id.clone();
            tokio::spawn(async move { service.update_agent_by_id("test-admin", &id, A2aCardSource::Url("https://example.org/card.json".into())).await })
        };
        fetcher.entered.notified().await;
        chain.db.execute(DbStatement::with_params(format!("UPDATE bcs_bots SET {mutation} WHERE bot_uuid=?"), vec![first.bot_id.clone().into()])).await.unwrap();
        fetcher.release.notify_one();
        assert_eq!(code(updating.await.unwrap().unwrap_err()), Code::TargetChanged);
        let rows = chain.db.query(DbStatement::with_params("SELECT agent_card FROM bcs_bots WHERE bot_uuid=?", vec![first.bot_id.into()])).await.unwrap();
        let saved: serde_json::Value = serde_json::from_str(&rows[0].get_string("agent_card").unwrap().unwrap()).unwrap();
        assert_eq!(saved, first.agent_card);
    }
}
