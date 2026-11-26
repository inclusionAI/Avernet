use super::*;
use bcs_test_support::contract::distributed::services::dist_session_directory_port_contract_tests;

#[tokio::test]
async fn conformance_direct_session_directory_authorizes_and_freezes_the_target() {
    use bcs_dist_model::*;
    use bcs_message_flow::a2a_chat::{DirectChatAccess, DirectSessionDirectory};
    use bcs_service_api::port::dist_agent::{
        DirectoryError, DistSessionDirectoryPort, SessionAccessContext,
    };

    let actors = Arc::new(MemoryRegistry::default());
    actors.insert("source", "public", Some("owner")).await;
    actors.insert("target", "protected", None).await;
    let access = DirectChatAccess::new(
        actors,
        Arc::new(StaticFriendCoreService::default()),
        Some(Arc::new(StaticOrganizationCoreService::new(vec![
            "source", "target",
        ]))),
        Some(staff_authority(&[("source", "owner")])),
    );
    let tenant = TenantId("first".into());
    let pool = PoolId("agents".into());
    let directory = DirectSessionDirectory::new(
        access,
        tenant.clone(),
        pool.clone(),
        SessionLimits {
            max_queued_tasks: 8,
            max_task_steps: 20,
            task_timeout_millis: 1_000,
            max_inline_bytes: 1024,
        },
    );
    let target = SessionTarget::DirectAgent {
        agent: AgentId("target".into()),
    };
    let scope = TaskScope {
        tenant: tenant.clone(),
        principal: PrincipalId("bot:source".into()),
    };
    assert_eq!(
        directory
            .authorize(&scope, &target, &SessionAccessContext::default())
            .await,
        Err(DirectoryError::AccessDenied)
    );
    let org = SessionAccessContext {
        organization: Some("promo-2026".into()),
        ..Default::default()
    };
    directory.authorize(&scope, &target, &org).await.unwrap();
    let missing_org = SessionAccessContext {
        organization: Some("absent".into()),
        ..Default::default()
    };
    assert_eq!(
        directory.authorize(&scope, &target, &missing_org).await,
        Err(DirectoryError::AccessDenied)
    );
    let delegated = SessionAccessContext {
        acting_agent: Some(AgentId("source".into())),
        ..org.clone()
    };
    let owner = TaskScope {
        tenant,
        principal: PrincipalId("human:owner".into()),
    };
    directory
        .authorize(&owner, &target, &delegated)
        .await
        .unwrap();
    let stranger = TaskScope {
        principal: PrincipalId("human:stranger".into()),
        ..owner.clone()
    };
    assert_eq!(
        directory.authorize(&stranger, &target, &delegated).await,
        Err(DirectoryError::AccessDenied)
    );
    let other = TaskScope {
        tenant: TenantId("other".into()),
        ..scope.clone()
    };
    assert_eq!(
        directory.authorize(&other, &target, &org).await,
        Err(DirectoryError::NotFound)
    );
    dist_session_directory_port_contract_tests(&directory, &scope, &target)
        .await
        .unwrap();
    let frozen = directory.resolve(&scope, &target).await.unwrap();
    assert_eq!(frozen.target, target);
    assert_eq!(
        frozen.mode_envelope.payload,
        Payload::Inline(serde_json::json!({"agent":"target"}))
    );
    assert_eq!(
        frozen.authorized_agents,
        vec![AuthorizedAgent {
            agent: AgentId("target".into()),
            pool
        }]
    );
}
