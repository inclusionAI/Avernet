//! Ordinary Group creation uses live Human friend edges, not legacy mirrors.
mod helpers;

use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::edge_permission::{EdgeGrant, EdgeStatus, GrantKind};
use bcs_edge_permission_store::{DbEdgeGrantStore, DbPermissionProfileStore};
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity, BotFinalDelivery,
    ChatConfiguration, CollaborationConfiguration, CreateCollaborationGroup, CreateGroup,
    CreateGroupSpec, CreateParticipant, GroupDeliveryPolicy, GroupDetail, GroupVisibility,
};
use bcs_service_api::port::repo::{EdgeGrantRepoPort, PermissionProfileRepoPort};
use bcs_service_api::{
    ActorStatus, BotCapabilities, GroupCreateCommand, GroupCreateParticipantCommand, GroupStrategy,
    GroupUseCaseError, ParticipantRole, ProviderAuthMode, ProviderBotConnectionMode,
    ProviderCollaborationScope, RegisterProviderBotParams, ServiceError,
};

const SIGNING_ENTRY: &str = "human-friend-group-test";
const HUMAN: &str = "human_alice";
const LEAD: &str = "private-lead";
const MEMBER: &str = "protected-member";

fn openapi_request(visibility: GroupVisibility) -> CreateGroup {
    CreateGroup {
        caller: AuthenticatedCaller {
            tenant: None,
            user: Some(AuthenticatedUserIdentity {
                id: "alice".into(),
                username: "alice".into(),
                display_name: None,
                full_name: None,
            }),
            bot: None,
            app: None,
            access_key: None,
        },
        group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
            human_mention_notify_mode: Default::default(),
            name: Some("Friend group".into()),
            context: None,
            opening_message: None,
            visibility,
            driver_bot_uuid: LEAD.into(),
            participants: vec![CreateParticipant {
                actor_id: MEMBER.into(),
                role: ParticipantRole::Consultant,
                tags: Vec::new(),
                message_view_scope: None,
            }],
            collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                delivery_policy: GroupDeliveryPolicy {
                    bot_final_delivery: BotFinalDelivery::SendToDriver,
                },
            }),
            originator: None,
        }),
    }
}

fn core_request(strategy: GroupStrategy) -> GroupCreateCommand {
    let (lead, member) = match strategy {
        GroupStrategy::ManagerWorker => ("manager", "worker"),
        _ => ("driver", "consultant"),
    };
    GroupCreateCommand {
        group_id: None,
        caller_actor_id: Some(HUMAN.into()),
        driver_bot_id: LEAD.into(),
        label: None,
        topic: None,
        context: None,
        opening_message: None,
        create_initial_session: false,
        human_mention_notify_mode: Default::default(),
        routing_policy: None,
        participants: [(LEAD, lead), (MEMBER, member)]
            .into_iter()
            .map(|(id, role)| GroupCreateParticipantCommand {
                bot_id: id.into(),
                role: Some(role.into()),
                tags: Vec::new(),
                message_view_scope: None,
            })
            .collect(),
        member_bot_ids: Vec::new(),
        group_kind: None,
        group_strategy: Some(strategy),
        originator: Some(HUMAN.into()),
        visibility: None,
        provisioning: false,
    }
}

async fn grant_friend(
    profiles: &DbPermissionProfileStore,
    grants: &DbEdgeGrantStore,
    env: &str,
    human: &str,
    bot: &str,
) -> u64 {
    let profile = profiles.ensure_default_profile(bot, env).await.unwrap();
    grants
        .insert_grant(EdgeGrant {
            edge_id: 0,
            env: env.into(),
            from_id: human.into(),
            to_id: bot.into(),
            grant_kind: GrantKind::PermissionProfile,
            grant_ref_id: profile,
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: Default::default(),
            originator_policy_data: None,
        })
        .await
        .unwrap()
}

#[tokio::test]
async fn ordinary_group_admission_follows_live_human_friend_edges() {
    // Single-case binary: set the test resolver input before server startup.
    unsafe {
        std::env::set_var(
            "BCS_SECRET_HUMAN_FRIEND_GROUP_TEST",
            "test-only-group-signing-value",
        );
    }
    let dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&dir.path().into());
    let path = dir.path().join("human-friend-group.sqlite");
    config.database.sqlite.path = path.to_str().unwrap().into();
    config.max_groups_as_driver = 10;
    config.secret.provider = "env".into();
    config.gateway_principal.signing_key_secret = Some(SIGNING_ENTRY.into());
    config.group_session_ws.signing_key_secret = SIGNING_ENTRY.into();
    config.session_files.backend.insert(
        "data_dir".into(),
        toml::Value::String(dir.path().join("files").to_str().unwrap().into()),
    );
    let server = bcs::BcsServer::new_with_storage(config).await.unwrap();
    let (_, task, state) = server.run_on_random_port_with_state().await.unwrap();
    struct Stop(tokio::task::AbortHandle);
    impl Drop for Stop {
        fn drop(&mut self) {
            self.0.abort();
        }
    }
    let _stop = Stop(task.abort_handle());
    let db = Arc::new(LocalSqliteDbPlugin::new_file(path.to_str().unwrap()).unwrap());
    let profiles = DbPermissionProfileStore::sqlite(db.clone());
    let grants = DbEdgeGrantStore::sqlite(db.clone());
    let env = &bcs_config::resolve_env_str();
    for (id, visibility) in [(LEAD, "private"), (MEMBER, "protected")] {
        state
            .services
            .registry
            .register(
                id.into(),
                BotCapabilities {
                    visibility: visibility.into(),
                    ..Default::default()
                },
            )
            .await
            .unwrap();
    }
    let service = &state.openapi_v1.group_service;
    assert!(matches!(
        service
            .create(openapi_request(GroupVisibility::Private))
            .await,
        Err(ApplicationError::Forbidden(_)),
    ));

    let lead_edge = grant_friend(&profiles, &grants, env, HUMAN, LEAD).await;
    grant_friend(&profiles, &grants, env, HUMAN, MEMBER).await;
    let created = service
        .create(openapi_request(GroupVisibility::Private))
        .await
        .expect(
            "active friend edges admit a private lead and protected member without legacy mirrors",
        );
    let GroupDetail::Collaboration(group) = created else {
        panic!("expected normal Group");
    };
    assert_eq!(group.driver_bot_uuid, LEAD);
    assert!(
        group
            .participants
            .iter()
            .any(|entry| entry.actor_id == MEMBER)
    );
    assert!(matches!(
        service
            .create(openapi_request(GroupVisibility::Public))
            .await,
        Err(ApplicationError::Forbidden(_)),
    ));
    let management = &state.services.group_management;
    for strategy in [
        GroupStrategy::Chat,
        GroupStrategy::ManagerWorker,
        GroupStrategy::StateMachine,
    ] {
        let group = management
            .create_group(core_request(strategy))
            .await
            .unwrap();
        assert_eq!(group.group_strategy, strategy);
        assert_eq!(group.driver_bot_id, LEAD);
        assert!(
            group
                .participants
                .iter()
                .any(|entry| entry.bot_uuid == MEMBER)
        );
    }
    let mut other_caller = core_request(GroupStrategy::Chat);
    other_caller.caller_actor_id = Some("human_bob".into());
    assert!(matches!(
        management.create_group(other_caller).await,
        Err(GroupUseCaseError::Forbidden(_))
    ));

    // A Bot with a reserved-looking ID is not an authenticated Human.
    let lookalike = "human_lookalike";
    state
        .services
        .registry
        .register(lookalike.into(), BotCapabilities::default())
        .await
        .unwrap();
    for bot in [LEAD, MEMBER] {
        grant_friend(&profiles, &grants, env, lookalike, bot).await;
    }
    let mut bot_caller = core_request(GroupStrategy::Chat);
    bot_caller.caller_actor_id = Some(lookalike.into());
    bot_caller.originator = Some(lookalike.into());
    assert!(matches!(
        management.create_group(bot_caller).await,
        Err(GroupUseCaseError::Forbidden(_))
    ));

    state
        .services
        .registry
        .update_actor_status(MEMBER, ActorStatus::Hidden)
        .await
        .unwrap();
    assert!(matches!(
        service
            .create(openapi_request(GroupVisibility::Private))
            .await,
        Err(ApplicationError::Forbidden(_))
    ));
    state
        .services
        .registry
        .update_actor_status(MEMBER, ActorStatus::Online)
        .await
        .unwrap();

    // Even a live friend edge cannot cross a Provider's same-owner boundary.
    let providers = &state.services.provider_core;
    let provider = providers
        .register_provider_with_policy(
            "Restricted friend Provider".into(),
            None,
            ProviderAuthMode::StaticBearer,
            "bob".into(),
            None,
            None,
            None,
            None,
            Some(ProviderCollaborationScope::SameOwner),
            None,
        )
        .await
        .unwrap();
    let (restricted, _) = state
        .services
        .provider_bot_core
        .register_provider_bot_with_bot_uuid(
            &provider.provider.provider_id,
            &provider.provider_admin_token,
            RegisterProviderBotParams {
                bot_name: "Restricted friend".into(),
                provider_bot_ref: "restricted-friend".into(),
                owners: vec!["bob".into()],
                connection_mode: ProviderBotConnectionMode::Plugin,
                ..Default::default()
            },
        )
        .await
        .unwrap();
    grant_friend(&profiles, &grants, env, HUMAN, &restricted.bot_uuid).await;
    let mut restricted_request = core_request(GroupStrategy::Chat);
    restricted_request.participants[1].bot_id = restricted.bot_uuid;
    assert!(matches!(
        management.create_group(restricted_request).await,
        Err(GroupUseCaseError::Service(ServiceError::Forbidden(_)))
    ));

    grants.revoke_grant(lead_edge, env).await.unwrap();
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_friendships (left_bot, right_bot, env) VALUES (?, ?, ?)",
        vec![
            DbValue::from(HUMAN),
            DbValue::from(LEAD),
            DbValue::from(env.as_str()),
        ],
    ))
    .await
    .unwrap();
    assert!(matches!(
        service
            .create(openapi_request(GroupVisibility::Private))
            .await,
        Err(ApplicationError::Forbidden(_)),
    ));
}
