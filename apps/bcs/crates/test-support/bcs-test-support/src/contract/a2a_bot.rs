//! Shared consumer contracts for A2A configuration boundaries.
use bcs_service_api::application::a2a_bot::{A2aBotManagementService, ConfigureA2aBotCommand};
use bcs_service_api::core::a2a_bot::A2aBotCoreService;
use bcs_service_api::port::{a2a_card::A2aCardPort, repo::a2a_bot::{A2aBotRecord, A2aBotRepoPort}};

/// Caller supplies an existing active Gateway Bot without an A2A Card.
pub async fn a2a_bot_repo_port_contract_tests(repo: &dyn A2aBotRepoPort, record: A2aBotRecord) {
    assert!(repo.get(&record.bot_uuid).await.unwrap().is_none());
    assert!(repo.compare_and_set(&record, 0).await.unwrap());
    assert_eq!(repo.get(&record.bot_uuid).await.unwrap(), Some(record.clone()));
    let cards = repo.cards_for_bots(&[record.bot_uuid.clone(), "missing-contract-bot".into()]).await.unwrap();
    assert_eq!(cards.len(), 1);
    assert_eq!(cards[&record.bot_uuid], record.agent_card);
    let filtered = repo.discovery_cards(&[record.bot_uuid.clone()], Some("impossible-contract-search-39f0"), &[]).await.unwrap();
    assert_eq!(filtered.get(&record.bot_uuid), Some(&None));
    assert_eq!(repo.discovery_cards(&[record.bot_uuid.clone()], None, &[]).await.unwrap()[&record.bot_uuid],Some(record.agent_card.clone()));
    assert!(!repo.compare_and_set(&record, 0).await.unwrap());
    assert!(repo.refreshable(None,16).await.unwrap().iter().any(|card|card.bot_uuid==record.bot_uuid));
    let mut invalid = record.clone(); invalid.revision += 2;
    assert!(repo.compare_and_set(&invalid, record.revision).await.is_err());
    assert_eq!(repo.get(&record.bot_uuid).await.unwrap(), Some(record));
}

/// Caller owns the listening HTTP fixture and verifies that it was hit.
pub async fn a2a_card_port_contract_tests(port: &dyn A2aCardPort, url: &str) {
    let card = port.fetch(url, true).await.unwrap();
    assert_eq!(card.interface.protocol_version, "1.0");
    assert!(card.raw.get("supportedInterfaces").is_some());
    assert!(port.fetch("file:///invalid-card", true).await.is_err());
}

pub async fn a2a_bot_core_service_contract_tests(core: &dyn A2aBotCoreService, record: A2aBotRecord) {
    let binding = core.provider_binding(&record.provider_id, &record.bot_uuid).await.unwrap();
    assert_eq!(binding.provider_bot_ref, record.provider_bot_ref);
    assert!(core.provider_binding("foreign-provider", &record.bot_uuid).await.is_err());
    assert_eq!(core.commit_card(record.clone(), 0).await.unwrap(), record);
    assert_eq!(core.cards_for_bots(&[record.bot_uuid.clone()]).await.unwrap()[&record.bot_uuid], record.agent_card);
    assert!(core.commit_card(record.clone(), 0).await.is_err());
    assert_eq!(core.get_card(&record.bot_uuid).await.unwrap(), Some(record));
}

pub async fn a2a_bot_management_service_contract_tests(service: &dyn A2aBotManagementService, command: ConfigureA2aBotCommand) {
    let mut unauthorized = command.clone(); unauthorized.provider_admin_token = "bad".into();
    assert!(service.configure(unauthorized).await.is_err());
    let record = service.configure(command.clone()).await.unwrap();
    assert_eq!(record.bot_uuid, command.bot_uuid);
    assert!(record.agent_card_url.is_some());
    let refreshed = service.refresh(&command.provider_id, &command.provider_admin_token, &command.bot_uuid, command.now_ms + 1000).await.unwrap();
    assert_eq!(refreshed.agent_card, record.agent_card);
    assert_eq!(refreshed, record, "identical refresh must preserve Card and revision");
    assert!(service.configure(command).await.is_err());
}

pub async fn a2a_run_repo_port_contract_tests(
    repo: &dyn bcs_service_api::port::repo::a2a_run::A2aRunRepoPort,
    record: bcs_service_api::port::repo::a2a_run::A2aRunRecord,
) {
    use bcs_service_api::port::repo::a2a_run::A2aRunState;
    assert!(repo.get(&record.run_id).await.unwrap().is_none());
    assert!(repo.create(&record).await.unwrap());
    assert!(!repo.create(&record).await.unwrap());
    assert_eq!(repo.get(&record.run_id).await.unwrap(), Some(record.clone()));
    assert!(repo.context(&record.scope).await.unwrap().is_none());
    let mut accepted = record;
    accepted.version = 2;
    accepted.remote_task_id = Some("remote-task".into());
    accepted.context_id = Some("remote-context".into());
    accepted.state = A2aRunState::Running;
    accepted = repo.compare_and_set(&accepted, 1).await.unwrap().unwrap();
    assert!(repo.compare_and_set(&accepted, 1).await.unwrap().is_none());
    assert_eq!(repo.context(&accepted.scope).await.unwrap().as_deref(), Some("remote-context"));
    assert_eq!(repo.active(&accepted.scope).await.unwrap(), vec![accepted.clone()]);
    accepted.version = 3;
    accepted.state = A2aRunState::Completed;
    accepted = repo.compare_and_set(&accepted, 2).await.unwrap().unwrap();
    assert!(repo.active(&accepted.scope).await.unwrap().is_empty());
    assert_eq!(repo.context(&accepted.scope).await.unwrap().as_deref(), Some("remote-context"));
    accepted.version = 4;
    accepted.state = A2aRunState::Running;
    assert!(repo.compare_and_set(&accepted, 3).await.unwrap().is_none());
}


/// Explicit Card-aware registration contract: one successful creation,
/// non-replayable runtime credential and strict duplicate rejection. The driver
/// must also inspect the same persistent Bot/Card/authority state.
pub async fn a2a_registration_service_contract_tests(
    service: &dyn bcs_service_api::application::v1::RegisterService,
    command: bcs_service_api::application::v1::RegisterBot, card_url: &str,
) -> bcs_service_api::application::v1::BotRegistration {
    let result=service.register_a2a_bot(command.clone(),card_url.to_string()).await.unwrap();
    assert!(!result.bot_uuid.is_empty());assert!(!result.bot_token.is_empty());
    let scope=result.registration.as_ref().unwrap();
    assert_eq!(scope.mode,bcs_service_api::application::v1::ProviderRegistrationMode::Gateway);
    assert!(scope.webhook_url.is_none());assert!(scope.effective_webhook_url.is_none());
    assert!(service.register_a2a_bot(command,card_url.to_string()).await.is_err());
    result
}

pub async fn a2a_provider_registration_core_service_contract_tests(
    core: &dyn bcs_service_api::core::a2a_bot::A2aProviderRegistrationCoreService,
    provider: &str, token: &str, params: bcs_service_api::RegisterProviderBotParams,
    card: bcs_service_api::types::a2a_bot::A2aRegistrationCard,
) -> bcs_service_api::core::a2a_bot::A2aProviderRegistrationResult {
    assert!(core.register(provider, "wrong-contract-admin", params.clone(), card.clone()).await.is_err());
    let created = core.register(provider, token, params.clone(), card.clone()).await.unwrap();
    assert_eq!(created.binding.provider_id, provider);
    assert!(created.binding.webhook_url.is_none());
    assert!(!created.bot_runtime_token.is_empty());
    assert!(matches!(core.register(provider, token, params, card).await,
        Err(bcs_service_api::ServiceError::Conflict(_))));
    created
}

/// A remote Task is unique, but its local run bindings are many-to-one.
/// Task input revisions fence stale writers across different run versions.
pub async fn a2a_many_run_task_contract_tests(
    repo: &dyn bcs_service_api::port::repo::a2a_run::A2aRunRepoPort,
    mut initial: bcs_service_api::port::repo::a2a_run::A2aRunRecord,
) {
    use bcs_service_api::port::repo::a2a_run::A2aRunState;
    initial.run_id="shared-run-1".into();initial.scope.session_id="shared-session".into();
    assert!(repo.create(&initial).await.unwrap());
    let mut first=initial.clone();first.remote_task_id=Some("shared-task".into());first.context_id=Some("shared-context".into());
    first.state=A2aRunState::InputRequired;first.version=2;
    first.pending_input=Some(serde_json::json!({"input":"shared","accepted":[]}));
    first=repo.compare_and_set(&first,1).await.unwrap().unwrap();
    let mut second=initial;second.run_id="shared-run-2".into();second.message_id="second-message".into();
    assert!(repo.create(&second).await.unwrap());
    second.remote_task_id=first.remote_task_id.clone();second.context_id=first.context_id.clone();
    second.state=A2aRunState::Running;second.version=2;
    second=repo.compare_and_set(&second,1).await.unwrap().unwrap();
    assert_eq!(second.pending_input,first.pending_input);
    assert_eq!(second.remote_task_id,first.remote_task_id);
    assert_ne!(second.task_version,first.task_version);
    let mut unbound_cleanup=second.clone();unbound_cleanup.run_id="failed-first-binding".into();unbound_cleanup.task_version=None;
    assert!(repo.has_other_active_task_run(&unbound_cleanup).await.unwrap());
    assert!(repo.has_other_active_task_run(&first).await.unwrap());
    assert!(repo.has_other_active_task_run(&second).await.unwrap());
    // Run 1's own version is still valid, but its shared Task snapshot is stale.
    first.version+=1;
    assert!(repo.compare_and_set(&first,2).await.unwrap().is_none());
    first=repo.get(&first.run_id).await.unwrap().unwrap();first.version+=1;
    first.pending_input=Some(serde_json::json!({"input":"shared","accepted":["approve"]}));
    first=repo.compare_and_set(&first,2).await.unwrap().unwrap();
    let fresh=repo.get(&second.run_id).await.unwrap().unwrap();
    assert_eq!(fresh.pending_input,first.pending_input);
    assert_eq!(fresh.state,A2aRunState::Running); // Run state is independent.
    second.version+=1;
    assert!(repo.compare_and_set(&second,2).await.unwrap().is_none());
    let mut unversioned=fresh.clone();unversioned.version+=1;unversioned.task_version=None;
    assert!(repo.compare_and_set(&unversioned,2).await.unwrap().is_none());
    assert_eq!(repo.get(&fresh.run_id).await.unwrap().unwrap(),fresh);
    let mut retarget=fresh.clone();retarget.version+=1;retarget.remote_task_id=Some("different-task".into());
    assert!(repo.compare_and_set(&retarget,2).await.unwrap().is_none());
    assert_eq!(repo.get(&fresh.run_id).await.unwrap().unwrap(),fresh);
    // Completing one binding does not complete other runs sharing this Task.
    first.version+=1;first.state=A2aRunState::Completed;
    repo.compare_and_set(&first,3).await.unwrap().unwrap();
    assert_eq!(repo.get(&second.run_id).await.unwrap().unwrap().state,A2aRunState::Running);
    assert!(!repo.has_other_active_task_run(&second).await.unwrap());
}
