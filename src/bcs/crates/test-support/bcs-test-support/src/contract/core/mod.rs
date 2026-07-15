//! Core service contract harnesses.

use bcs_service_api::{
    BotRegistryCoreService, FriendCoreService, FriendRequestCoreService, FriendRequestDirection,
    FusionCoreService, GroupCoreService, OrganizationCoreService, ProposalCoreService,
    RelationCoreService, RoutingCoreService, ServiceError, SystemMessageDispatcherService,
    SystemMessageProducerService,
};

pub async fn bot_registry_core_service_contract_tests<T: BotRegistryCoreService + ?Sized>(
    _svc: &T,
) {
}

pub async fn friend_core_service_contract_tests<T: FriendCoreService + ?Sized>(svc: &T) {
    svc.add_friendship("core-alice", "core-bob")
        .await
        .expect("add friendship");
    svc.add_friendship("core-bob", "core-alice")
        .await
        .expect("add friendship idempotent reverse");

    assert!(svc.are_friends("core-alice", "core-bob").await);
    assert!(svc.are_friends("core-bob", "core-alice").await);
    assert_eq!(
        svc.list_friends("core-alice").await,
        vec!["core-bob".to_string()]
    );
    assert!(
        svc.are_all_friends("core-alice", &["core-bob".to_string()])
            .await
            .is_ok()
    );
    assert!(matches!(
        svc.are_all_friends("core-alice", &["core-missing".to_string()])
            .await,
        Err(ServiceError::NotFriends(_))
    ));
    assert_eq!(
        svc.remove_all_friendships("core-alice")
            .await
            .expect("remove friendships"),
        1
    );
    assert!(!svc.are_friends("core-alice", "core-bob").await);
}

pub async fn friend_request_core_service_contract_tests<T: FriendRequestCoreService + ?Sized>(
    svc: &T,
) {
    assert!(matches!(
        svc.get_request("core-missing-request").await,
        Err(ServiceError::FriendRequestNotFound(_))
    ));
    assert!(
        svc.list_requests("core-alice", FriendRequestDirection::All, None)
            .await
            .is_empty()
    );
}

pub async fn fusion_core_service_contract_tests<T: FusionCoreService + ?Sized>(_svc: &T) {}

pub async fn group_core_service_contract_tests<T: GroupCoreService + ?Sized>(_svc: &T) {}

pub async fn organization_core_service_contract_tests<T: OrganizationCoreService + ?Sized>(
    svc: &T,
    managing_provider_id: &str,
    organization_code: &str,
) {
    let created = svc
        .create(
            managing_provider_id,
            organization_code,
            "Organization Contract",
            Some("created by the core contract"),
        )
        .await
        .expect("create organization");
    assert_eq!(created.code, organization_code);
    assert_eq!(created.managing_provider_id, managing_provider_id);

    let fetched = svc
        .get_for_manager(managing_provider_id, organization_code)
        .await
        .expect("get organization");
    assert_eq!(fetched.name, "Organization Contract");
    assert!(svc
        .list_for_manager(managing_provider_id, false)
        .await
        .expect("list organizations")
        .iter()
        .any(|organization| organization.code == organization_code));
    assert!(matches!(
        svc.create(managing_provider_id, organization_code, "Duplicate", None)
            .await,
        Err(ServiceError::Conflict(_))
    ));
    assert!(matches!(
        svc.get_for_manager("contract-other-manager", organization_code)
            .await,
        Err(ServiceError::Forbidden(_))
    ));
}

pub async fn proposal_core_service_contract_tests<T: ProposalCoreService + ?Sized>(_svc: &T) {}

pub async fn relation_core_service_contract_tests<T: RelationCoreService + ?Sized>(_svc: &T) {}

pub async fn routing_core_service_contract_tests<T: RoutingCoreService + ?Sized>(_svc: &T) {}

pub async fn system_message_producer_service_contract_tests<
    T: SystemMessageProducerService + ?Sized,
>(
    svc: &T,
) {
    let _ = svc.kind();
}

pub async fn system_message_dispatcher_service_contract_tests<
    T: SystemMessageDispatcherService + ?Sized,
>(
    _svc: &T,
) {
}
