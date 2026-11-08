//! Manager write paths (`edit_manager` / `sync_managers`) against
//! [`DbBotAuthorityStore`] on SQLite (bot-authority plan Task
//! 2.1–2.5), running the SAME shared harness scenarios as the
//! memory projection so results and errors cannot drift
//! (spec §5.1/§5.2, feasibility doc §2 sketch).
//!
//! The fixture DB mirrors `migrations/sqlite/037_bot_authority.sql`;
//! the Human registry the Task-2 write paths validate targets
//! against is the `bcs_bots` actor rows (`actor_kind='human'`,
//! env-bound), so a seeded Human IS a live actor row. MySQL
//! still runs the same store in CI (plan §6); locally there is
//! no MySQL server.
//!
//! The stock-bound (>10,000) rejection is beyond what the shared
//! harness intentionally seeds; the SQLite bulk-seeding scenarios
//! below cover it.

#![allow(clippy::expect_used, clippy::unwrap_used)]

use bcs_db_api::DbValue;
use bcs_service_api::types::bot_authority::{
    AuthorityActor, AuthorityMutation, BotAccessRelation, ManagerEdit, ManagerEditAction,
    ManagerSync,
};
use bcs_test_support::contract::bot_authority_contract::{
    BotAuthorityContractFixture, run_bot_authority_contract,
};

const ENV: &str = "authority-env";
const OTHER_ENV: &str = "authority-env-other";

use bcs_test_support::bot_authority_sqlite::{execute, SqlAuthorityFixture};

async fn new_fixture() -> SqlAuthorityFixture {
    bcs_test_support::bot_authority_sqlite::new_fixture(ENV).await
}

#[tokio::test]
async fn authority_manager_contract_sqlite() {
    let fixture = new_fixture().await;
    run_bot_authority_contract(&fixture, ENV, OTHER_ENV).await;
}

// Real predefined manager set: the mutation actor carries the
// literal human actor id an Application would map.
fn raw_edit(bot: &str, actor: &str, action: ManagerEditAction) -> ManagerEdit {
    ManagerEdit {
        bot_id: bot.to_string(),
        user_id: actor.to_string(),
        action,
        mutation: AuthorityMutation {
            mutation_id: "mutation-raw".to_string(),
            actor: AuthorityActor::Human { user_id: "own".to_string() },
        },
    }
}

/// A mutation actor that is not a `human_` actor id must be
/// rejected as an unknown Human (never inserted raw).
#[tokio::test]
async fn manager_target_must_be_human_actor_shape() {
    let fixture = new_fixture().await;
    fixture.seed_bot(ENV, "bot_shape", 1).await;
    fixture
        .grant_edge(ENV, "bot_shape", "human_own", BotAccessRelation::Owner)
        .await;
    fixture.seed_human(ENV, "own").await;
    let result = fixture
        .authority_repo()
        .edit_manager(raw_edit("bot_shape", "bot_someone", ManagerEditAction::Grant))
        .await;
    assert!(result.is_err(), "non-Human actor target must be rejected");
    assert_eq!(
        fixture
            .authority_repo()
            .role("human_own", "bot_shape")
            .await
            .unwrap(),
        Some(BotAccessRelation::Owner),
        "unchanged owner"
    );
}

/// The stock bound (feasibility §5.2, frozen 10,000): |S| over the
/// bound rejects the whole sync with an explicit error, no
/// truncation.
#[tokio::test]
async fn sync_rejects_stock_over_10000() {
    // >10,000 current managers is only practical to build here via
    // bulk multi-row INSERTs (the shared harness works at 1,001).
    let fixture = new_fixture().await;
    let db = fixture.db.clone();
    fixture.seed_bot(ENV, "bot_huge", 1).await;
    fixture.seed_human(ENV, "own").await;
    // Bulk humans (live `bcs_bots` actor rows).
    for chunk in (0..10_110usize).collect::<Vec<_>>().chunks(100) {
        let mut values = String::new();
        let mut params = Vec::new();
        for (index, id) in chunk.iter().enumerate() {
            if index > 0 {
                values.push_str(",");
            }
            values.push_str("(?, 'human', ?, ?, ?, 'protected', 'online', ?, 0)");
            params.push(DbValue::from(format!("human_stock{id:0>5}")));
            params.push(DbValue::from(format!("stock{id:0>5}")));
            params.push(DbValue::from(format!("fixture-token-stock{id:0>5}")));
            params.push(DbValue::from(format!("stock{id:0>5}")));
            params.push(DbValue::from(ENV));
        }
        execute(
            db.as_ref(),
            &format!(
                "INSERT INTO bcs_bots \
                 (bot_uuid, actor_kind, name, session_token, created_by, \
                  visibility, status, env, is_deleted) \
                 VALUES {values} \
                 ON CONFLICT(bot_uuid, env) DO NOTHING"
            ),
            params,
        )
        .await;
    }
    fixture
        .grant_edge(ENV, "bot_huge", "human_own", BotAccessRelation::Owner)
        .await;
    // Bulk manager edges.
    for chunk in (0..10_101usize).collect::<Vec<_>>().chunks(100) {
        let mut values = String::new();
        let mut params = Vec::new();
        for id in chunk {
            if !values.is_empty() {
                values.push_str(",");
            }
            values.push_str("(?, ?, 'bot_huge', 'manager', 0, NULL, 'approved', 'same_as_from', NULL)");
            params.push(DbValue::from(ENV));
            params.push(DbValue::from(format!("human_stock{id:0>5}")));
        }
        execute(
            db.as_ref(),
            &format!(
                "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, \
                 status, originator_policy_type, originator_policy_data) VALUES {values}"
            ),
            params,
        )
        .await;
    }

    let started = std::time::Instant::now();
    let result = fixture
        .authority_repo()
        .sync_managers(ManagerSync {
            bot_id: "bot_huge".to_string(),
            user_ids: vec![],
            mutation: AuthorityMutation {
                mutation_id: "mutation-huge".to_string(),
                actor: AuthorityActor::Service { app_id: 9 },
            },
        })
        .await;
    assert!(
        result.is_err(),
        "sync must reject a stock beyond the frozen bound, got {result:?} \
         after {:?}",
        started.elapsed()
    );
    assert!(
        result
            .as_ref()
            .unwrap_err()
            .to_string()
            .contains("bot_manager_stock_limit"),
        "the stock bound must surface its pinned code, got {result:?}"
    );
    let page = fixture
        .authority_repo()
        .managers(bcs_service_api::types::bot_authority::ManagerListQuery {
            bot_id: "bot_huge".to_string(),
            ..Default::default()
        })
        .await
        .expect("page after rejection");
    assert_eq!(page.total, 10_101, "no truncation, whole request rejected");
}
