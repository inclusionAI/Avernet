//! Shared concrete SQLite/native MySQL registry -> invocation regression.
#![allow(clippy::unwrap_used, reason = "shared integration assertions")]
use super::lifecycle::Driver;
use bcs_bot_store::{DbProviderStore, PersistentBotRepo};
use bcs_db_api::{DbPlugin, DbSqlFlavor, DbStatement, db_get_column};
use bcs_service_api::application::agent_invoke::{
    AgentInvocationService, AgentInvokeGrantManagementService,
};
use bcs_service_api::bot_provider::{BotConnectionMode, BotProviderRecord};
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use bcs_service_api::types::agent_invoke::*;
use bcs_service_api::{ActorStatus, BotCapabilities, BotRepoPort, ProviderBotBindingRepoPort};
use bcs_test_support::contract::agent_invoke::AgentInvokeContractDriver;
use std::sync::Arc;

async fn revision(d: &Driver, bot: &str) -> u64 {
    let rows =
        d.db.query(DbStatement::with_params(
            "SELECT agent_invoke_identity_revision AS n FROM bcs_bots WHERE bot_uuid=? AND env=?",
            vec![bot.into(), d.scope.env.as_str().into()],
        ))
        .await
        .unwrap();
    db_get_column(&rows[0], "n").unwrap()
}
fn caps(code: Option<&str>) -> BotCapabilities {
    BotCapabilities {
        name: Some("display only".into()),
        visibility: "protected".into(),
        agent_code: code.map(str::to_owned),
        ..Default::default()
    }
}
fn input(d: &Driver) -> AgentInvocationInput {
    AgentInvocationInput {
        principal: AgentInvokePrincipal::Agent("registry-a".into()),
        authenticated_scope: d.scope.clone(),
        pair: pair(),
        originator: "registry-a".into(),
        operation: AgentInvokeOperation::DirectInvocation,
    }
}
fn pair() -> AgentInvokePair {
    AgentInvokePair {
        from_bot_id: "registry-a".into(),
        to_bot_id: "registry-b".into(),
    }
}
async fn grant(d: &Driver, version: u64, key: &str) -> AgentInvokeGrantView {
    d.service
        .change(AgentInvokeCommand {
            principal: AgentInvokePrincipal::Human("human_registry_target".into()),
            authenticated_scope: d.scope.clone(),
            pair: pair(),
            change: AgentInvokeChange::Grant {
                expected_version: version,
            },
            idempotency_key: key.into(),
            reason: "registry integration".into(),
        })
        .await
        .unwrap()
        .current_grant
        .unwrap()
}
pub async fn run(db: Arc<dyn DbPlugin>, flavor: DbSqlFlavor) {
    let d = Driver::new_on(db.clone(), flavor, &bcs_config::resolve_env_str()).await;
    for bot in ["registry-a", "registry-b"] {
        d.register(bot).await;
    }
    d.role(
        "human_registry_source",
        "registry-a",
        Some(AgentInvokeRole::Owner),
    )
    .await;
    d.role(
        "human_registry_target",
        "registry-b",
        Some(AgentInvokeRole::Owner),
    )
    .await;
    let repo = PersistentBotRepo::with_sql_flavor(db.clone(), flavor);
    let first = grant(&d, 0, "registry-first").await;
    assert!(d.service.authorize_invocation(input(&d)).await.is_ok());
    repo.register("registry-a".into(), caps(None))
        .await
        .unwrap();
    repo.update_actor_status("registry-a", ActorStatus::Online)
        .await
        .unwrap();
    assert_eq!(
        revision(&d, "registry-a").await,
        1,
        "display/re-onboard is not rebinding"
    );
    assert!(d.service.authorize_invocation(input(&d)).await.is_ok());
    for status in [
        ActorStatus::Hidden,
        ActorStatus::Hidden,
        ActorStatus::Online,
    ] {
        repo.update_actor_status("registry-a", status)
            .await
            .unwrap();
        assert_eq!(
            d.service.authorize_invocation(input(&d)).await,
            Err(AgentInvokeError::Unavailable)
        );
    }
    assert_eq!(
        revision(&d, "registry-a").await,
        3,
        "hidden ABA survives restoration"
    );
    d.publish("registry-a", true, true).await;
    assert_eq!(
        d.service.authorize_invocation(input(&d)).await,
        Err(AgentInvokeError::Forbidden)
    );
    let second = grant(&d, first.version.get() + 1, "registry-second").await;
    assert_eq!(second.edge_id, first.edge_id);
    assert!(d.service.authorize_invocation(input(&d)).await.is_ok());
    for code in ["Agent-X", "Agent-X", "agent-x", "Agent-X"] {
        repo.register("registry-a".into(), caps(Some(code)))
            .await
            .unwrap();
        assert_eq!(
            d.service.authorize_invocation(input(&d)).await,
            Err(AgentInvokeError::Unavailable)
        );
    }
    assert_eq!(
        revision(&d, "registry-a").await,
        6,
        "byte-exact code ABA is monotonic"
    );
    d.publish("registry-a", true, true).await;
    let mut third = grant(&d, second.version.get() + 1, "registry-third").await;
    repo.update_actor_status("registry-b", ActorStatus::Hidden)
        .await
        .unwrap();
    // Even a trusted feed publishing hidden=false cannot override the physical
    // registry status once its generation is fresh enough to pass the binding.
    d.publish("registry-b", true, true).await;
    assert_eq!(
        d.service
            .change(AgentInvokeCommand {
                principal: AgentInvokePrincipal::Human("human_registry_target".into()),
                authenticated_scope: d.scope.clone(),
                pair: pair(),
                change: AgentInvokeChange::Grant {
                    expected_version: third.version.get() + 1
                },
                idempotency_key: "must-not-grant-hidden".into(),
                reason: "hidden registry proof".into(),
            })
            .await,
        Err(AgentInvokeError::Forbidden)
    );
    repo.update_actor_status("registry-b", ActorStatus::Online)
        .await
        .unwrap();
    d.publish("registry-b", true, true).await;
    third = grant(&d, third.version.get() + 1, "target-restored-grant").await;
    d.sql("INSERT INTO bcs_providers (provider_id,env,name,config,owners,created_by) VALUES ('registry-provider',?,'registry','{}','[]','fixture')",vec![d.scope.env.as_str().into()]).await;
    let providers = match flavor {
        DbSqlFlavor::Sqlite => DbProviderStore::sqlite(db.clone()),
        DbSqlFlavor::Mysql => DbProviderStore::mysql(db.clone()),
    };
    let mut affiliation = BotProviderRecord {
        bot_uuid: "registry-a".into(),
        provider_id: "registry-provider".into(),
        provider_bot_ref: "registry-a".into(),
        connection_mode: BotConnectionMode::Plugin,
        webhook_url: None,
        is_deleted: false,
    };
    providers
        .attach_provider_bot(affiliation.clone())
        .await
        .unwrap();
    assert_eq!(revision(&d, "registry-a").await, 7);
    providers
        .attach_provider_bot(affiliation.clone())
        .await
        .unwrap();
    assert_eq!(
        revision(&d, "registry-a").await,
        7,
        "idempotent attach preserves identity"
    );
    affiliation.connection_mode = BotConnectionMode::Gateway;
    providers.attach_provider_bot(affiliation).await.unwrap();
    assert_eq!(revision(&d, "registry-a").await, 8);
    assert_eq!(
        d.service.authorize_invocation(input(&d)).await,
        Err(AgentInvokeError::Unavailable)
    );
    for disabled in [true, true, false] {
        providers
            .update_binding_disabled("registry-a", disabled, 0)
            .await
            .unwrap();
    }
    assert_eq!(
        revision(&d, "registry-a").await,
        10,
        "binding ABA cannot restore a physical fence"
    );
    assert!(
        providers
            .delete_provider_bot("registry-provider", "registry-a", 0)
            .await
            .unwrap()
    );
    assert_eq!(revision(&d, "registry-a").await, 11);
    assert!(
        !providers
            .delete_provider_bot("registry-provider", "registry-a", 0)
            .await
            .unwrap()
    );
    assert_eq!(revision(&d, "registry-a").await, 11);
    // The unchanged approved row is not a runtime permit, and physical writers
    // must not silently rewrite its historical approval generations.
    let rows = db
        .query(DbStatement::with_params(
            "SELECT version FROM agent_invoke_grants WHERE edge_id=?",
            vec![third.edge_id.get().into()],
        ))
        .await
        .unwrap();
    assert_eq!(
        db_get_column::<u64>(&rows[0], "version").unwrap(),
        third.version.get()
    );
    d.register("registry-binding").await;
    let binding = bcs_service_api::ProviderBotBinding {
        bot_uuid: "registry-binding".into(),
        provider_id: "registry-provider".into(),
        provider_bot_ref: "binding-ref".into(),
        webhook_url: None,
        disabled: false,
        created_at: 0,
        updated_at: 0,
    };
    providers.insert_binding(binding.clone()).await.unwrap();
    assert_eq!(revision(&d, "registry-binding").await, 2);
    assert!(providers.insert_binding(binding).await.is_err());
    assert_eq!(
        revision(&d, "registry-binding").await,
        2,
        "duplicate insert rolls back the revision"
    );
    use bcs_service_api::types::bot_authority::{
        AuthorityActor, AuthorityMutation, OwnershipInitialization,
    };
    providers
        .restore_provider_bot_with_ownership(
            BotProviderRecord {
                bot_uuid: "registry-a".into(),
                provider_id: "registry-provider".into(),
                provider_bot_ref: "registry-a".into(),
                connection_mode: BotConnectionMode::Gateway,
                webhook_url: None,
                is_deleted: false,
            },
            caps(Some("Agent-X")),
            "registry_restored",
            "fixture-restored-runtime-token",
            OwnershipInitialization {
                owner_user_id: "registry_restored".into(),
                initial_manager_user_ids: vec![],
                mutation: AuthorityMutation {
                    mutation_id: "registry-restored".into(),
                    actor: AuthorityActor::Provider {
                        provider_id: "registry-provider".into(),
                    },
                },
            },
        )
        .await
        .unwrap();
    assert_eq!(
        revision(&d, "registry-a").await,
        12,
        "same UUID restore never resets revision"
    );
    assert_eq!(
        d.service.authorize_invocation(input(&d)).await,
        Err(AgentInvokeError::Unavailable)
    );
    d.publish("registry-a", true, true).await;
    assert_eq!(
        d.service.authorize_invocation(input(&d)).await,
        Err(AgentInvokeError::Forbidden)
    );
    for bot in ["registry-ordinary", "registry-soft"] {
        d.register(bot).await;
    }
    d.role(
        "human_registry_source",
        "registry-ordinary",
        Some(AgentInvokeRole::Owner),
    )
    .await;
    let mutation = AuthorityMutation {
        mutation_id: "registry-ordinary-retire".into(),
        actor: AuthorityActor::Human {
            user_id: "registry_source".into(),
        },
    };
    assert!(
        repo.retire_with_authority("registry-ordinary", mutation.clone())
            .await
            .unwrap()
    );
    assert_eq!(revision(&d, "registry-ordinary").await, 2);
    assert!(
        !repo
            .retire_with_authority("registry-ordinary", mutation.clone())
            .await
            .unwrap()
    );
    assert_eq!(revision(&d, "registry-ordinary").await, 2);
    assert!(repo.soft_delete("registry-soft").await);
    assert!(!repo.soft_delete("registry-soft").await);
    assert_eq!(revision(&d, "registry-soft").await, 2);
    println!("agent-invoke-registry-producers=actual-writes physical-fences=verified");
}
