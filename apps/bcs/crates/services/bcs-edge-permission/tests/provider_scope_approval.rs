//! Pending approvals re-evaluate Provider policy before any grant or decision.
use bcs_bot::BotCore;
use bcs_bot_store::provider::{MemoryBotProviderStore, ProviderBindingProjection};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_edge_permission::DbConnectService;
use bcs_service_api::application::connect::{ConnectService, ConnectStatus};
use bcs_service_api::bot_provider::{
    BotConnectionMode, BotProviderRecord, DownlinkDetectionSource,
};
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use bcs_service_api::{
    BotCapabilities, BotRepoPort, ProviderRecord, ProviderRepoPort, ServiceError,
};
use std::sync::Arc;
#[path = "support/friend_auth.rs"]
mod support;

#[tokio::test]
async fn tightening_rejects_old_foreign_request_without_edges_or_decision() {
    let (edges, profiles, requests, config, db) = support::assemble().await;
    let id = "pending-local";
    support::seed_bot(&db, id, "APPROVAL").await;
    let dir = tempfile::tempdir().unwrap();
    let bots = Arc::new(MemoryBotRepo::with_base_dir(dir.path().into()));
    bots.register_with_owner_and_token(
        id.into(),
        BotCapabilities::default(),
        "85020",
        "fixture-token",
    )
    .await
    .unwrap();
    let providers = Arc::new(MemoryProviderStore::new());
    providers
        .insert_provider(ProviderRecord {
            provider_id: "local".into(),
            slug: None,
            name: "Local".into(),
            config: "{}".into(),
            created_by: "85020".into(),
            owners: "[]".into(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .unwrap();
    let metadata: Arc<dyn BotProviderRepoPort> =
        Arc::new(MemoryBotProviderStore::new(bots.clone(), providers.clone()));
    metadata
        .attach_provider_bot(BotProviderRecord {
            bot_uuid: id.into(),
            provider_id: "local".into(),
            provider_bot_ref: "local-ref".into(),
            connection_mode: BotConnectionMode::Plugin,
            webhook_url: None,
            is_deleted: false,
        })
        .await
        .unwrap();
    providers.bind_group_scope_source(
        Arc::new(bcs_group_store::MemoryGroupRepo::new()),
        Arc::downgrade(&metadata),
    );
    let projection = Arc::new(ProviderBindingProjection::new(
        providers.clone(),
        metadata.clone(),
        DownlinkDetectionSource::BotConnectionMode,
    ));
    let registry = Arc::new(
        BotCore::with_provider_repos(bots, providers.clone(), providers.clone(), projection)
            .with_bot_provider_repo(metadata),
    );
    let service = DbConnectService::new(
        edges.clone(),
        profiles,
        requests.clone(),
        config,
        None,
        Arc::new(bcs_service_api::NoopFriendConnectNotificationPort),
        Arc::new(bcs_service_api::NoopFriendAuthSyncPort),
        "dev".into(),
    )
    .with_collaboration_registry(registry);
    let pending = service
        .create_connect("human_guest", id, None, None)
        .await
        .unwrap();
    assert_eq!(pending.status, ConnectStatus::Pending);
    providers
        .update_provider_metadata(
            "local",
            None,
            Some(r#"{"collaboration_scope":"same_owner"}"#),
            None,
            2,
        )
        .await
        .unwrap();
    assert!(matches!(
        service
            .approve(&pending.request_ids[0], "85020", None)
            .await,
        Err(ServiceError::Forbidden(_))
    ));
    let request = requests.get(&pending.request_ids[0], "dev").await.unwrap();
    assert_eq!(
        request.status,
        bcs_service_api::types::edge_permission::RequestStatus::Pending
    );
    assert!(request.edge_id.is_none());
    assert!(
        edges
            .list_active_grants("human_guest", id, "dev")
            .await
            .is_empty()
    );
    assert!(
        edges
            .list_active_grants(id, "human_guest", "dev")
            .await
            .is_empty()
    );
    let own = service
        .create_connect("human_85020", id, None, None)
        .await
        .unwrap();
    assert_eq!(own.status, ConnectStatus::Approved);
    assert!(
        service
            .approve(&own.request_ids[0], "85020", None)
            .await
            .is_ok()
    );
    // Idempotent approval must also recheck the live policy.
    providers
        .update_provider_disabled("local", true, 3)
        .await
        .unwrap();
    assert!(matches!(
        service.approve(&own.request_ids[0], "85020", None).await,
        Err(ServiceError::Forbidden(_))
    ));
}
