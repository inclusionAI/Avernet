//! `ownerships_for` scenario of the Bot-authority contract suite.
//!
//! Split out of `bot_authority_contract.rs` at a mechanical
//! boundary (1,000-line budget): the batch owner projection pins
//! shared strict semantics for every store running the harness.

use bcs_service_api::types::bot_authority::{BotAccessRelation, human_actor_id};

use super::bot_authority_contract::BotAuthorityContractFixture;

/// Batch owner projection (`ownerships_for`): same length/order as
/// the input; per-item `None` for ids that cannot project an owner
/// under the bound env (missing, deleted, version-0 without the
/// legacy shim) while initialized bots project their ONE approved
/// owner edge; corrupt owner state errors the whole batch (strict
/// like `ownership`); another env's facts never leak.
pub(super) async fn ownerships_for_projects_per_item(
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
    other_env: &str,
) {
    let repo = fixture.authority_repo();

    // Empty input → empty aligned result, no error.
    let empty = repo.ownerships_for(&[]).await.expect("empty batch");
    assert!(empty.is_empty(), "empty input keeps empty output");

    // Initialized bot with one approved owner edge projects it.
    let owned_bot = "bot_own_batch_owned";
    fixture.seed_bot(env, owned_bot, 1).await;
    fixture
        .grant_edge(env, owned_bot, &human_actor_id("own_batch_owner"), BotAccessRelation::Owner)
        .await;
    // Uninitialized / deleted ids project None instead of erroring.
    fixture.seed_bot(env, "bot_own_batch_uninit", 0).await;
    fixture.seed_bot(env, "bot_own_batch_deleted", 1).await;
    fixture.delete_bot(env, "bot_own_batch_deleted").await;
    // Cross-env facts never initialize the bound env's projection.
    fixture.seed_bot(other_env, owned_bot, 1).await;
    fixture
        .grant_edge(other_env, owned_bot, &human_actor_id("other_env_owner"), BotAccessRelation::Owner)
        .await;

    let ids = vec![
        owned_bot.to_string(),
        "bot_own_batch_uninit".to_string(),
        "bot_own_batch_deleted".to_string(),
        "bot_own_batch_missing".to_string(),
    ];
    let views = repo.ownerships_for(&ids).await.expect("batch ownerships");
    assert_eq!(views.len(), ids.len(), "result length matches input");
    let owned = views[0].as_ref().expect("owned bot projects");
    assert_eq!(owned.bot_id, owned_bot);
    assert_eq!(owned.owner_user_id, "own_batch_owner");
    assert_eq!(owned.owner_actor_id, human_actor_id("own_batch_owner"));
    assert!(views[1].is_none(), "uninitialized bot projects None");
    assert!(views[2].is_none(), "deleted bot projects None");
    assert!(views[3].is_none(), "missing bot projects None");

    // Length + order preserved across chunk boundaries (129 > CHUNK).
    let mut many: Vec<String> = Vec::with_capacity(130);
    many.push(owned_bot.to_string());
    for index in 0..129 {
        let id = format!("bot_own_chunk_{index}");
        fixture.seed_bot(env, &id, 0).await;
        many.push(id);
    }
    let views = repo.ownerships_for(&many).await.expect("chunked batch");
    assert_eq!(views.len(), many.len(), "chunked length matches input");
    assert_eq!(
        views[0].as_ref().map(|view| view.owner_user_id.as_str()),
        Some("own_batch_owner")
    );
    assert!(
        views[1..].iter().all(Option::is_none),
        "version-0 tail stays None in every chunk"
    );

    // A corrupt member is a strict batch error, never a silent None.
    let corrupt = "bot_own_batch_corrupt";
    fixture.seed_bot(env, corrupt, 1).await;
    fixture.grant_edge(env, corrupt, &human_actor_id("owner_a"), BotAccessRelation::Owner).await;
    fixture.seed_corrupt_duplicate_owner(env, corrupt, &human_actor_id("owner_b")).await;
    assert!(
        repo.ownerships_for(&[owned_bot.to_string(), corrupt.to_string()]).await.is_err(),
        "batch must propagate the corrupt-owner error"
    );
}
