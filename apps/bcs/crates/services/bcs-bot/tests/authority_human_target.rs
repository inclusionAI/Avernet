use std::sync::Arc;
use bcs_bot::{Bot, BotCore};
use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::{BotCapabilities, BotLeaveCommand, BotManagementService, BotRegistryCoreService};
use bcs_service_api::core::registry::TrustedBotRegistration;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::bot_authority::{AuthorityActor, AuthorityMutation, OwnershipInitialization};

#[tokio::test]
async fn cannot_delete_human_who_owns_live_bot() {
    let dir = tempfile::tempdir().unwrap();
    let repo = Arc::new(MemoryBotRepo::with_base_dir(dir.path().into()));
    repo.set_legacy_created_by_fallback(true);
    let core = Arc::new(BotCore::with_repo(repo.clone()));
    let bot = core.register_with_ownership(TrustedBotRegistration {
        capabilities: BotCapabilities { name: Some("owned".into()), ..Default::default() },
        initialization: OwnershipInitialization { owner_user_id: "alice".into(), mutation: AuthorityMutation {
            mutation_id: "review-create".into(), actor: AuthorityActor::Human { user_id: "alice".into() },
        } },
    }).await.unwrap();
    let hook = bcs_test_support::bot_authority::authority_hook_for(repo.clone());
    let app = Bot::new(core.clone()).with_bot_core(core.clone()).with_bot_authority(hook);
    let result = app.leave_bot(BotLeaveCommand { caller_actor_id: Some("human_alice".into()),
        human_actor_id: Some("human_alice".into()), bot_id: "human_alice".into() }).await;
    assert!(result.is_err(), "must reject removal of the owner Human while its owned Bot is live");
    use bcs_service_api::port::repo::BotRepoPort;
    use bcs_service_api::types::bot_authority::{BotRoleQuery, BotAccessRelation};
    assert_eq!(repo.role("human_alice", "human_alice").await.unwrap(), None);
    assert_eq!(repo.roles_for(&[BotRoleQuery { user_id:"human_alice".into(), bot_id:"human_alice".into() }]).await.unwrap(), vec![None]);
    assert!(repo.retire_with_authority("human_alice", AuthorityMutation {
        mutation_id:"retire-human-direct".into(), actor:AuthorityActor::Human { user_id:"alice".into() },
    }).await.is_err());
    assert!(core.get("human_alice").await.is_some());
    assert_eq!(repo.role("human_alice", &bot.bot_uuid).await.unwrap(), Some(BotAccessRelation::Owner));
}
