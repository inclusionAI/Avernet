//! Group human-mention notify Store conformance run on the real MySQL chain.
//!
//! This is separate evidence from migration-only success: it lives in the
//! ignored `full_mysql_migration_chain_applies_and_preserves_history` test
//! (CI: `.github/workflows/unit-tests.yml`) because static SQL parsing is not
//! a substitute for Store behavior on a MySQL-compatible backend.
//!
//! The same verification body also runs NON-ignored against a SQLite-backed
//! migration chain (`sqlite_migration_chain_backs_the_group_notify_contract`
//! in `migrate_mysql_chain_tests.rs`): the contract is dialect-independent,
//! so the SQLite run pins Group-notify Store behavior in every CI run while
//! the MySQL run adds chain-specific evidence.

use super::*;
use bcs_domain::{Participant, ParticipantRole};
use bcs_group_store::MySqlGroupStore;
use bcs_service_api::port::repo::{
    CommitGroupEventfulMutation, GroupEventfulMutation, GroupRepoPort,
};
use bcs_service_api::types::GroupMutableFieldsPatch;
use bcs_service_api::{Group, HumanMentionNotifyMode, ServiceError};
use bcs_test_support::contract::group_human_notify::group_human_notify_contract;

const ENV: &str = "test";

fn contract_group(id: &str, driver: &str) -> Group {
    Group::new(
        id,
        driver,
        vec![Participant::bot(driver, ParticipantRole::Driver)],
    )
}

/// Run the whole notify-contract verification against `db`. `build_store`
/// constructs the dialect-specific Store (`MySqlGroupStore::new` for the
/// MySQL chain, `MySqlGroupStore::sqlite` for the SQLite chain) so every
/// read in this body is a cold read over the shared database.
pub(super) async fn verify<F>(db: Arc<dyn DbPlugin>, build_store: F) -> Result<()>
where
    F: Fn(&Arc<dyn DbPlugin>, &str) -> MySqlGroupStore + Copy,
{
    let mysql_store = |db: &Arc<dyn DbPlugin>, env: &str| build_store(db, env);
    // Shared three-mode / cold-read / policy-read contract. The reader factory
    // must build a NEW store over the same database on every call so every
    // read is a cold read that proves persistence, not cache state.
    let reader_db = Arc::clone(&db);
    let writer = mysql_store(&db, ENV);
    group_human_notify_contract(
        &writer,
        move || Arc::new(mysql_store(&reader_db, ENV)) as Arc<dyn GroupRepoPort>,
    )
    .await;

    // Mutable Patch path lands on `none`, observed by a fresh Store and the
    // scoped policy read.
    let mut mutable_group = contract_group("notify-contract-mysql", "driver");
    mutable_group.human_mention_notify_mode = HumanMentionNotifyMode::DriverBotOnly;
    mysql_store(&db, ENV)
        .upsert(mutable_group.clone())
        .await
        .context("seed mutable Group row")?;
    let reader = mysql_store(&db, ENV);
    reader
        .patch_mutable_fields(
            &mutable_group.id,
            GroupMutableFieldsPatch {
                human_mention_notify_mode: Some(HumanMentionNotifyMode::None),
                ..Default::default()
            },
        )
        .await
        .context("mutable mode patch")?;
    drop(reader);
    let cold = mysql_store(&db, ENV);
    assert_eq!(
        cold.try_get(&mutable_group.id)
            .await
            .context("cold read after mutable patch")?
            .context("mutable Group row")?
            .human_mention_notify_mode,
        HumanMentionNotifyMode::None
    );

    // Eventful versioned Patch path lands on `none` with a version bump.
    let mut eventful_group = contract_group("notify-contract-eventful", "driver");
    eventful_group.human_mention_notify_mode = HumanMentionNotifyMode::DriverBotOnly;
    let writer = mysql_store(&db, ENV);
    writer
        .upsert(eventful_group.clone())
        .await
        .context("seed eventful Group row")?;
    let committed = writer
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: eventful_group.id.clone(),
            expected_version: eventful_group.version,
            mutated_at_ms: eventful_group.updated_at.max(1),
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                human_mention_notify_mode: Some(HumanMentionNotifyMode::None),
                ..Default::default()
            }),
            event: None,
        })
        .await
        .context("eventful mode patch")?;
    assert_eq!(committed.human_mention_notify_mode, HumanMentionNotifyMode::None);
    assert_eq!(committed.version, eventful_group.version + 1);
    let cold = mysql_store(&db, ENV);
    assert_eq!(
        cold.read_human_notify_policy(&eventful_group.id)
            .await
            .context("policy read after eventful patch")?
            .context("policy")?
            .mode,
        HumanMentionNotifyMode::None
    );

    // A persisted value outside the three canonical forms must surface as an
    // error rather than fail open to the default.
    db.execute(DbStatement::with_params(
        "UPDATE bcs_groups SET human_mention_notify_mode = 'weird' WHERE group_id = ? AND env = ?",
        vec![
            DbValue::from(mutable_group.id.as_str()),
            DbValue::from(ENV),
        ],
    ))
    .await
    .context("corrupt the persisted mode")?;
    let error = mysql_store(&db, ENV)
        .read_human_notify_policy(&mutable_group.id)
        .await
        .expect_err("unknown persisted mode must fail closed");
    assert!(
        error
            .to_string()
            .contains("unknown human_mention_notify_mode"),
        "{error}"
    );

    // Environment isolation: the same Group id is invisible across envs.
    assert_eq!(
        mysql_store(&db, "other-env")
            .read_human_notify_policy(&mutable_group.id)
            .await
            .context("cross-env policy read")?,
        None
    );

    // Write failures propagate.
    let missing = mysql_store(&db, ENV)
        .patch_mutable_fields(
            "notify-mysql-missing",
            GroupMutableFieldsPatch {
                human_mention_notify_mode: Some(HumanMentionNotifyMode::None),
                ..Default::default()
            },
        )
        .await
        .expect_err("patching a missing Group must fail");
    assert!(matches!(missing, ServiceError::GroupNotFound(id) if id == "notify-mysql-missing"));
    let stale = mysql_store(&db, ENV)
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: eventful_group.id.clone(),
            expected_version: eventful_group.version.checked_add(99).expect("version"),
            mutated_at_ms: eventful_group.updated_at.max(1),
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                human_mention_notify_mode: Some(HumanMentionNotifyMode::None),
                ..Default::default()
            }),
            event: None,
        })
        .await
        .expect_err("stale expected_version must conflict");
    assert!(matches!(stale, ServiceError::Conflict(_)), "{stale}");
    Ok(())
}
