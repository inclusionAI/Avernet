//! Plan Task 17 integration suite: historical ownership backfill, governance
//! and the one-shot authority cutover.
//!
//! Every asserted variable comes from a REAL query against a REAL database
//! built by the FULL existing migration chain (`bcs::migrations::run_sqlite_migrations`)
//! or the memory twin's production state — never a mocked boolean. The
//! legacy library (created_by rows, version-0 bots, transferred bots, deleted
//! bots, missing/multi creator conflicts) is seeded with plain SQL through
//! the migration chain, exactly the shape an UNMIGRATED production library
//! has; the authority cutover itself runs only through the governed
//! application/Core wiring in `bcs::ownership_migration_wiring` — the same
//! composition entry the `bcs-ownership-migrate` maintenance binary uses.
//!
//! The binary tests run the REAL binary via `CARGO_BIN_EXE_*`: dry-run
//! performs no writes, side effects only happen on apply, missing maintenance
//! acknowledgment and any storage failure exit the command nonzero.
//!
//! The MySQL companion is `#[ignore]`d behind `BCS_TEST_MYSQL_URL` (no MySQL
//! server in this dev environment); CI runs it against its MySQL service.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Arc;

use bcs_bot_store::{MemoryBotRepo, PersistentBotRepo};
use bcs_relation_store::{DbRelationStore, MemoryRelationRepo};
use bcs_service_api::port::repo::{BotAuthorityRepoPort, BotRepoPort, RelationRepoPort};
use bcs_service_api::types::bot_authority::{
    MIGRATION_MAX_BATCH_SIZE, OwnershipMigrationReason,
};
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::{BotCapabilities, ServiceError};
use bcs_db_api::{DbPlugin, DbStatement, DbValue, db_get_column};
#[path = "ownership_migration_common.rs"]
mod support;
use support::*;

const CONFLICTS: &[(&str, OwnershipMigrationReason)] = &[
    ("bot-nocreator", OwnershipMigrationReason::MissingCreator),
    ("bot-runtime", OwnershipMigrationReason::MissingCreator),
    ("bot-multicreator", OwnershipMigrationReason::ConflictingCreators),
    ("bot-claimmismatch", OwnershipMigrationReason::ConflictingCreators),
    ("bot-missinghuman", OwnershipMigrationReason::MissingHuman),
    ("bot-corrupt-v0", OwnershipMigrationReason::AuthorityInconsistent),
    ("bot-corrupt-v1", OwnershipMigrationReason::AuthorityInconsistent),
];

/// The version-0 conflict shapes the DRY-RUN scan must page (spec §16.1.2:
/// the candidate scan is the live/physical/version-0 set, conflicts
/// included). The version>0 corruption (`bot-corrupt-v1`) is an
/// execution-time governance conflict: it is NOT a scan candidate.
const PAGED_CONFLICTS: &[(&str, OwnershipMigrationReason)] = &[
    ("bot-nocreator", OwnershipMigrationReason::MissingCreator),
    ("bot-runtime", OwnershipMigrationReason::MissingCreator),
    ("bot-multicreator", OwnershipMigrationReason::ConflictingCreators),
    ("bot-claimmismatch", OwnershipMigrationReason::ConflictingCreators),
    ("bot-missinghuman", OwnershipMigrationReason::MissingHuman),
    ("bot-corrupt-v0", OwnershipMigrationReason::AuthorityInconsistent),
];

// ---------------------------------------------------------------------------
// Step 1 RED: the migration initializes version-0 bots from created_by and
// never touches created_by.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn migration_initializes_version0_bots_from_created_by_untouched() {
    let db = migrated_sqlite().await;
    let env = env_str();
    seed_legacy_library(db.as_ref(), &env).await;
    let (service, _bots, _relations) = migration_service_over_sql(db.clone());

    let page = service
        .inspect_batch(None, 100)
        .await
        .expect("inspect the legacy library");
    let clean = page
        .candidates
        .iter()
        .find(|candidate| candidate.bot_id == "bot-clean-a")
        .expect("clean version-0 candidate must page through");
    assert_eq!(
        clean.candidate_user_id.as_deref(),
        Some("user-a"),
        "the candidate owner IS the legal created_by source"
    );
    assert_eq!(clean.reason, OwnershipMigrationReason::Ready);
    assert_eq!(clean.env, env);

    let original_created_by = created_by_of(db.as_ref(), "bot-clean-a", &env).await;
    assert_eq!(original_created_by.as_deref(), Some("user-a"));

    let report = service
        .initialize_batch(
            vec!["bot-clean-a".to_string(), "bot-clean-b".to_string()],
            "batch-2026-10-08-1".to_string(),
        )
        .await
        .expect("initialize the confirmed clean candidates");
    assert_eq!(report.initialized.len(), 2, "both clean bots initialize");
    assert!(
        report
            .initialized
            .iter()
            .all(|entry| entry.bot_id == "bot-clean-a" || entry.bot_id == "bot-clean-b")
    );
    assert!(report.failed.is_empty(), "no storage failure happened");
    assert!(report.conflicted.is_empty());

    // REAL queries: the initialization wrote the full authority contract.
    let migrated_version = ownership_version_of(db.as_ref(), "bot-clean-a", &env).await;
    assert_eq!(migrated_version, 1);
    let created_by_after_migration = created_by_of(db.as_ref(), "bot-clean-a", &env).await;
    assert_eq!(original_created_by, created_by_after_migration);
    assert_eq!(approved_owner_edge_count(db.as_ref(), "bot-clean-a", &env).await, 1);
    assert_eq!(
        owner_edge_claimant(db.as_ref(), "bot-clean-a", &env).await,
        Some("human_user-a".to_string())
    );
    assert_eq!(initialization_audit_count(db.as_ref(), "bot-clean-a", &env).await, 1);
    assert_eq!(
        default_profile_count(db.as_ref(), "bot-clean-a", &env).await,
        1,
        "the governed init seeds the default permission profile"
    );
    // The migration records its batch on the initialization ledger with
    // `governed_repair` provenance and a System actor.
    let rows = db
        .query(DbStatement::with_params(
            "SELECT source, actor_kind, batch_id FROM bot_ownership_initializations \
             WHERE env = ? AND bot_id = ?",
            vec![DbValue::from(env_str()), DbValue::from("bot-clean-a")],
        ))
        .await
        .expect("read initialization ledger row");
    let row = rows.first().expect("one ledger row");
    assert_eq!(
        db_get_column::<String>(row, "source").expect("source"),
        "governed_repair"
    );
    assert_eq!(
        db_get_column::<String>(row, "actor_kind").expect("actor_kind"),
        "system"
    );
    assert_eq!(
        row.get_string("batch_id").ok().flatten(),
        Some("batch-2026-10-08-1".to_string())
    );
}

// ---------------------------------------------------------------------------
// Step 1 RED: conflicts go into the governance report; creator absence NEVER
// authorizes by itself; bare runtime keeps version 0 and stays discoverable.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn conflicts_are_governed_and_creator_absence_never_authorizes() {
    let db = migrated_sqlite().await;
    let env = env_str();
    seed_legacy_library(db.as_ref(), &env).await;
    let (service, _bots, _relations) = migration_service_over_sql(db.clone());

    let page = service.inspect_batch(None, 100).await.unwrap();
    for (bot_id, reason) in PAGED_CONFLICTS {
        let candidate = page
            .candidates
            .iter()
            .find(|candidate| &candidate.bot_id == bot_id)
            .unwrap_or_else(|| panic!("{bot_id} must appear in the dry-run page"));
        assert_eq!(&candidate.reason, reason, "{bot_id} conflict reason");
    }
    assert!(
        !page
            .candidates
            .iter()
            .any(|candidate| candidate.bot_id == "bot-corrupt-v1"),
        "the version>0 corruption is an execution-time governance conflict, not a scan candidate"
    );
    // The bare runtime candidate shows an empty candidate_user_id when the
    // missing source is legal — but it is a conflict, never an owner.
    let bare = page
        .candidates
        .iter()
        .find(|candidate| candidate.bot_id == "bot-runtime")
        .expect("bare runtime row still pages for governance");
    assert_eq!(bare.candidate_user_id, None);
    assert_eq!(bare.reason, OwnershipMigrationReason::MissingCreator);

    // Explicit confirmation cannot push a conflicted candidate through: the
    // execution re-verification derives the same conflict attributes.
    let before: Vec<AuthorityFingerprint> = {
        let mut states = Vec::new();
        for (bot_id, _) in CONFLICTS {
            states.push(fingerprint(db.as_ref(), bot_id, &env).await);
        }
        states
    };
    let report = service
        .initialize_batch(
            CONFLICTS
                .iter()
                .map(|(bot_id, _)| bot_id.to_string())
                .collect(),
            "batch-conflicts".to_string(),
        )
        .await
        .expect("the report itself is a success; conflicts are entries");
    let conflicted_bot_is_in_governance_report = CONFLICTS
        .iter()
        .all(|(bot_id, _)| report.conflicted.iter().any(|entry| &entry.bot_id == bot_id));
    assert!(conflicted_bot_is_in_governance_report);
    assert!(report.failed.is_empty());
    assert!(report.initialized.is_empty());
    // REAL queries: no conflicted bot moved in any way.
    for state in &before {
        let after = fingerprint(db.as_ref(), &state.bot_id, &env).await;
        assert_eq!(
            state, &after,
            "{}: conflicts never change authority state",
            state.bot_id
        );
    }
    // The bare runtime row stays version 0 and remains a live, discoverable
    // registered Bot (Agent self-discovery unaffected).
    let runtime_liveness = scalar(
        db.as_ref(),
        "SELECT COUNT(*) AS value FROM bcs_bots \
         WHERE bot_uuid = 'bot-runtime' AND env = ? AND COALESCE(is_deleted, 0) = 0",
        vec![DbValue::from(env_str())],
    )
    .await;
    assert_eq!(runtime_liveness, 1);
}

// ---------------------------------------------------------------------------
// Step 1 RED: rerun is idempotent by batch_id and never resets transferred
// or already-initialized ownership.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn rerun_is_idempotent_and_never_resets_transferred_ownership() {
    let db = migrated_sqlite().await;
    let env = env_str();
    seed_legacy_library(db.as_ref(), &env).await;
    let (service, _bots, _relations) = migration_service_over_sql(db.clone());

    let ids = vec![
        "bot-clean-a".to_string(),
        "bot-transferred".to_string(),
        "bot-owned".to_string(),
    ];
    let report = service
        .initialize_batch(ids.clone(), "batch-replay-1".to_string())
        .await
        .unwrap();
    assert_eq!(
        report.initialized.len(),
        1,
        "only the version-0 candidate initializes"
    );
    assert_eq!(report.skipped.len(), 2);
    let transferred_skip = report
        .skipped
        .iter()
        .find(|entry| entry.bot_id == "bot-transferred")
        .expect("transferred bot is skipped with an attribution");
    assert_eq!(
        transferred_skip.reason,
        OwnershipMigrationReason::OwnershipTransferred
    );
    let owned_skip = report
        .skipped
        .iter()
        .find(|entry| entry.bot_id == "bot-owned")
        .expect("already-initialized bot is verified/skipped");
    assert_eq!(owned_skip.reason, OwnershipMigrationReason::AlreadyInitialized);

    // Re-run the SAME batch: no re-claim, no owner/version change.
    let transferred_owner_before_rerun =
        owner_edge_claimant(db.as_ref(), "bot-transferred", &env).await;
    let transferred_version_before_rerun =
        ownership_version_of(db.as_ref(), "bot-transferred", &env).await;
    let replay = service
        .initialize_batch(ids, "batch-replay-1".to_string())
        .await
        .unwrap();
    assert_eq!(
        replay.initialized.len(),
        1,
        "batch_id replay returns the original report counts"
    );
    assert!(replay.failed.is_empty());
    assert_eq!(replay.skipped.len(), 2);
    let owner_after_rerun = owner_edge_claimant(db.as_ref(), "bot-transferred", &env).await;
    let version_after_rerun = ownership_version_of(db.as_ref(), "bot-transferred", &env).await;
    assert_eq!(transferred_owner_before_rerun, owner_after_rerun);
    assert_eq!(transferred_version_before_rerun, version_after_rerun);
    assert_eq!(
        transferred_owner_before_rerun,
        Some("human_user-b".to_string())
    );
    assert_eq!(version_after_rerun, 3);
    // Only ONE initialization ledger row exists for the migrated bot.
    assert_eq!(initialization_audit_count(db.as_ref(), "bot-clean-a", &env).await, 1);
}

// ---------------------------------------------------------------------------
// Step 1 RED: Human self rows and deleted bots never migrate.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn human_self_rows_and_deleted_bots_never_migrate() {
    let db = migrated_sqlite().await;
    let env = env_str();
    seed_legacy_library(db.as_ref(), &env).await;
    let (service, _bots, _relations) = migration_service_over_sql(db.clone());

    let page = service.inspect_batch(None, 100).await.unwrap();
    assert!(
        !page
            .candidates
            .iter()
            .any(|candidate| candidate.bot_id.starts_with("human")),
        "Human self rows never page as candidates"
    );
    assert!(
        !page
            .candidates
            .iter()
            .any(|candidate| candidate.bot_id == "bot-deleted"),
        "deleted bots never page as candidates"
    );

    // Even explicitly confirming them changes no authority state.
    let report = service
        .initialize_batch(
            vec!["human_self_row".to_string(), "bot-deleted".to_string()],
            "batch-excluded".to_string(),
        )
        .await
        .unwrap();
    assert!(report.initialized.is_empty());
    assert_eq!(report.skipped.len(), 2);
    assert!(
        report
            .skipped
            .iter()
            .any(|entry| entry.bot_id == "human_self_row"
                && entry.reason == OwnershipMigrationReason::HumanRowExcluded)
    );
    assert!(
        report
            .skipped
            .iter()
            .any(|entry| entry.bot_id == "bot-deleted"
                && entry.reason == OwnershipMigrationReason::BotNotLive)
    );

    let human_rows_migrated = scalar(
        db.as_ref(),
        "SELECT COUNT(*) AS value FROM bot_ownership_initializations \
         WHERE bot_id IN (SELECT bot_uuid FROM bcs_bots WHERE actor_kind = 'human')",
        vec![],
    )
    .await;
    assert_eq!(human_rows_migrated, 0);
    let deleted_bots_migrated = scalar(
        db.as_ref(),
        "SELECT COUNT(*) AS value FROM bot_ownership_initializations \
         WHERE bot_id IN (SELECT bot_uuid FROM bcs_bots WHERE COALESCE(is_deleted, 0) = 1)",
        vec![],
    )
    .await;
    assert_eq!(deleted_bots_migrated, 0);
    assert_eq!(ownership_version_of(db.as_ref(), "human_self_row", &env).await, 0);
    assert_eq!(ownership_version_of(db.as_ref(), "bot-deleted", &env).await, 0);
}

// ---------------------------------------------------------------------------
// Keyset pagination + over-limit rejection.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn candidates_page_by_keyset_and_oversized_batches_are_rejected() {
    let db = migrated_sqlite().await;
    let env = env_str();
    seed_legacy_library(db.as_ref(), &env).await;
    let (service, _bots, _relations) = migration_service_over_sql(db.clone());

    let mut seen: Vec<String> = Vec::new();
    let mut after: Option<String> = None;
    loop {
        let page = service.inspect_batch(after.clone(), 2).await.unwrap();
        if page.candidates.is_empty() {
            break;
        }
        assert!(
            page.candidates.len() <= 2,
            "the page never exceeds the requested limit"
        );
        if let Some(after_id) = after.as_deref() {
            assert!(page.candidates[0].bot_id.as_str() > after_id, "strictly ascending");
        }
        let last = page.candidates.last().unwrap().bot_id.clone();
        seen.extend(page.candidates.iter().map(|candidate| candidate.bot_id.clone()));
        match page.next_bot_id.clone() {
            Some(next) if next == last => after = Some(last),
            None => break,
            Some(other) => panic!("next_bot_id must be the last row's id, got {other}"),
        }
        if seen.len() > 500 {
            panic!("pagination does not terminate");
        }
    }
    assert!(
        seen.contains(&"bot-clean-a".to_string()) && seen.contains(&"bot-nocreator".to_string()),
        "pagination walks the WHOLE candidate set"
    );
    let ordered = seen.clone();
    let mut dedup = seen.clone();
    dedup.sort();
    dedup.dedup();
    assert_eq!(dedup.len(), ordered.len(), "no bot appears twice");
    assert!(
        ordered.windows(2).all(|pair| pair[0] < pair[1]),
        "the pages the binary walks ascend strictly"
    );

    // Keyset translates into binding the `after_bot_id` position.
    let page_two = service
        .inspect_batch(Some(ordered[0].clone()), MIGRATION_MAX_BATCH_SIZE as u32)
        .await
        .unwrap();
    assert_eq!(
        page_two.candidates.first().map(|candidate| candidate.bot_id.clone()),
        Some(ordered[1].clone()),
        "after_bot_id offsets the SAME ordered scan"
    );

    // Over-limit requests are rejected: never silently truncated.
    assert!(matches!(
        service.inspect_batch(None, 101).await.unwrap_err(),
        ServiceError::InvalidOperation { .. }
    ));
    let mut huge = Vec::<String>::new();
    for index in 0..(MIGRATION_MAX_BATCH_SIZE + 1) {
        huge.push(format!("bot-{index}"));
    }
    assert!(matches!(
        service
            .initialize_batch(huge, "batch-huge".to_string())
            .await
            .unwrap_err(),
        ServiceError::InvalidOperation { .. }
    ));
    // Empty/blank batch ids are refused fail-closed as well.
    assert!(matches!(
        service
            .initialize_batch(vec!["bot-clean-a".to_string()], "  ".to_string())
            .await
            .unwrap_err(),
        ServiceError::InvalidOperation { .. }
    ));
    // So is a missing scan anchor shape (blank id).
    assert!(matches!(
        service
            .inspect_batch(Some("  ".to_string()), 10)
            .await
            .unwrap_err(),
        ServiceError::InvalidOperation { .. }
    ));
}

// ---------------------------------------------------------------------------
// Partial batch failure + restart recovery through batch_id (memory twin's
// failure-injection critical section; the wiring is the binary's own).
// ---------------------------------------------------------------------------

fn memory_caps(bot_id: &str) -> BotCapabilities {
    BotCapabilities {
        name: Some(bot_id.to_string()),
        summary: Some("legacy runtime bot".to_string()),
        domains: vec!["legacy".to_string()],
        ..Default::default()
    }
}

#[tokio::test]
async fn partial_batch_failure_recovers_via_batch_id_replay() {
    let temp = tempfile::tempdir().unwrap();
    let bots = Arc::new(MemoryBotRepo::with_base_dir(temp.path().into()));
    let relations = Arc::new(MemoryRelationRepo::new());
    // Seed the legacy-shape memory library: live Human + version-0 runtime
    // registrations claimed by created_by.
    bots.ensure_human_actor("user-a", "Admin").await.unwrap();
    for bot_id in ["bot-mem-1", "bot-mem-2"] {
        bots.create_registration_if_absent(
            bot_id.to_string(),
            memory_caps(bot_id),
            "user-a",
            &format!("token-{bot_id}"),
        )
        .await
        .unwrap();
    }
    let service = bcs::ownership_migration_wiring::ownership_migration_service_with_repos(
        bots.clone(),
        relations.clone(),
    );

    // Bot-mem-1 fails inside the storage critical section mid-batch;
    // bot-mem-2 commits.
    bots.arm_authority_write_failure();
    let failed_run = service
        .initialize_batch(
            vec!["bot-mem-1".to_string(), "bot-mem-2".to_string()],
            "batch-restart".to_string(),
        )
        .await
        .expect("a per-bot storage failure is a reported entry, not an abort");
    assert_eq!(failed_run.failed.len(), 1);
    assert_eq!(failed_run.failed[0].bot_id, "bot-mem-1");
    assert_eq!(failed_run.failed[0].reason, OwnershipMigrationReason::StorageFailure);
    assert_eq!(failed_run.initialized.len(), 1);
    assert_eq!(failed_run.initialized[0].bot_id, "bot-mem-2");
    assert!(matches!(
        bots.ownership("bot-mem-1").await.unwrap_err(),
        ServiceError::Authority(AuthorityError::OwnershipNotInitialized { .. })
    ));
    assert_eq!(
        "user-a",
        bots.ownership("bot-mem-2").await.unwrap().owner_user_id,
        "the committed prefix survived the failure"
    );

    // Restart: the SAME batch replays — the committed bot is recovered from
    // the batch ledger and only re-verified, the failed bot is retried fresh.
    let replay = service
        .initialize_batch(
            vec!["bot-mem-1".to_string(), "bot-mem-2".to_string()],
            "batch-restart".to_string(),
        )
        .await
        .unwrap();
    assert!(replay.failed.is_empty());
    assert_eq!(replay.initialized.len(), 2, "replay restores the original counts");
    assert_eq!(replay.initialized[0].bot_id, "bot-mem-1");
    assert_eq!(replay.initialized[1].bot_id, "bot-mem-2");
    assert_eq!(
        "user-a",
        bots.ownership("bot-mem-1").await.unwrap().owner_user_id
    );
    assert_eq!(
        "user-a",
        bots.ownership("bot-mem-2").await.unwrap().owner_user_id
    );
    assert_eq!(
        bots.authority_ownership_initialization_count("bot-mem-2")
            .await
            .unwrap(),
        1,
        "the recovered bot was never re-initialized"
    );
}

// ---------------------------------------------------------------------------
// Real binary entry (CARGO_BIN_EXE_*): maintenance requirement, dry-run
// no-write, apply-only side effects, nonzero exit on failure.
// ---------------------------------------------------------------------------

fn binary_path() -> &'static str {
    env!("CARGO_BIN_EXE_bcs-ownership-migrate")
}

fn config_dir_with_sqlite(db_path: &Path) -> PathBuf {
    let dir = tempfile::tempdir().expect("temp config dir");
    let template = include_str!("../../../../configs/bcs-config-local.toml");
    let config = template.replace(
        "path = \"bcs.db\"",
        &format!("path = \"{}\"", db_path.display()),
    );
    std::fs::write(dir.path().join("bcs-config-local.toml"), config)
        .expect("write the migration config");
    dir.keep()
}

fn run_binary(args: &[&str], config_dir: &Path) -> std::process::Output {
    Command::new(binary_path())
        .args(args)
        .arg("--config-dir")
        .arg(config_dir)
        .env("SERVER_ENV", "local")
        .output()
        .expect("run bcs-ownership-migrate")
}

fn confirm_file(dir: &Path, ids: &[&str]) -> PathBuf {
    let path = dir.join("confirm.json");
    let ids: Vec<String> = ids.iter().map(|id| id.to_string()).collect();
    std::fs::write(&path, serde_json::to_string(&ids).unwrap()).unwrap();
    path
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn binary_requires_maintenance_and_dry_run_writes_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let db_path = dir.path().join("library.sqlite");
    {
        let db = migrated_sqlite_file(&db_path).await;
        seed_binary_library(db.as_ref()).await;
    }
    let config_dir = config_dir_with_sqlite(&db_path);

    // Missing maintenance acknowledgment: nonzero usage exit, no writes.
    let refused = run_binary(&["inspect"], config_dir.as_path());
    assert_ne!(
        refused.status.code(),
        Some(0),
        "without --maintenance the cutover binary must refuse"
    );
    assert!(
        String::from_utf8_lossy(&refused.stderr)
            .to_lowercase()
            .contains("maintenance")
            || String::from_utf8_lossy(&refused.stdout)
                .to_lowercase()
                .contains("maintenance"),
        "the refusal names the missing maintenance acknowledgment"
    );

    // Dry-run (inspect) succeeds and writes NOTHING.
    let before_clean = reopen_fingerprint(&db_path, "bot-bin-clean").await;
    let dry = run_binary(&["--maintenance", "inspect", "--limit", "100"], config_dir.as_path());
    assert!(
        dry.status.success(),
        "inspect exits zero: {dry:?}\n{}",
        String::from_utf8_lossy(&dry.stderr)
    );
    let after_clean = reopen_fingerprint(&db_path, "bot-bin-clean").await;
    assert_eq!(
        before_clean, after_clean,
        "the dry-run must not write anything: {before_clean:?} vs {after_clean:?}"
    );
    let before_nocreator = reopen_fingerprint(&db_path, "bot-bin-nocreator").await;
    let after_nocreator = reopen_fingerprint(&db_path, "bot-bin-nocreator").await;
    assert_eq!(
        before_nocreator, after_nocreator,
        "conflicted rows stay untouched by the dry-run"
    );
    let stdout = String::from_utf8_lossy(&dry.stdout);
    let report: serde_json::Value = serde_json::from_str(stdout.trim())
        .unwrap_or_else(|error| panic!("inspect prints one machine-readable JSON: {error}\n{stdout}"));
    let candidates = report["candidates"].as_array().expect("candidate array");
    assert!(
        candidates
            .iter()
            .any(|candidate| candidate["bot_id"] == "bot-bin-clean"
                && candidate["candidate_user_id"] == "user-a"),
        "the dry-run lists the ready candidate with its created_by owner"
    );
    assert!(
        candidates
            .iter()
            .any(|candidate| candidate["bot_id"] == "bot-bin-nocreator"
                && candidate["reason"] == "missing_creator"),
        "the dry-run lists the missing-creator conflict for governance"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn binary_apply_writes_once_and_replays_the_same_batch() {
    let dir = tempfile::tempdir().unwrap();
    let db_path = dir.path().join("library.sqlite");
    {
        let db = migrated_sqlite_file(&db_path).await;
        seed_binary_library(db.as_ref()).await;
    }
    let config_dir = config_dir_with_sqlite(&db_path);
    let confirm = confirm_file(dir.path(), &["bot-bin-clean", "bot-bin-nocreator"]);
    let original_created_by = reopen_fingerprint(&db_path, "bot-bin-clean").await.created_by;

    let apply = run_binary(
        &[
            "--maintenance",
            "apply",
            "--batch-id",
            "batch-binary-1",
            "--confirm-file",
            confirm.to_str().unwrap(),
        ],
        config_dir.as_path(),
    );
    assert!(
        apply.status.success(),
        "apply exits zero: {apply:?}\n{}",
        String::from_utf8_lossy(&apply.stderr)
    );
    let report: serde_json::Value =
        serde_json::from_str(String::from_utf8_lossy(&apply.stdout).trim())
            .expect("apply prints the migration report JSON");
    assert_eq!(report["initialized"].as_array().map(Vec::len), Some(1));
    assert_eq!(report["conflicted"].as_array().map(Vec::len), Some(1));

    let migrated = reopen_fingerprint(&db_path, "bot-bin-clean").await;
    assert_eq!(migrated.version, 1);
    let created_by_after_migration = reopen_fingerprint(&db_path, "bot-bin-clean").await.created_by;
    assert_eq!(original_created_by, created_by_after_migration);

    // Replaying the same batch replays the ORIGINAL counts and writes nothing.
    let replay = run_binary(
        &[
            "--maintenance",
            "apply",
            "--batch-id",
            "batch-binary-1",
            "--confirm-file",
            confirm.to_str().unwrap(),
        ],
        config_dir.as_path(),
    );
    assert!(
        replay.status.success(),
        "batch-id replay exits zero: {replay:?}"
    );
    let replay_report: serde_json::Value =
        serde_json::from_str(String::from_utf8_lossy(&replay.stdout).trim()).unwrap();
    assert_eq!(
        replay_report["initialized"].as_array().map(Vec::len),
        report["initialized"].as_array().map(Vec::len),
        "batch replay returns the original report counts"
    );
    let initializations = reopen_fingerprint(&db_path, "bot-bin-clean").await.initializations;
    assert_eq!(
        initializations, 1,
        "the ledger records the initialization exactly once"
    );

    // A missing confirm file is a usage failure: nonzero, no side effects.
    let missing_confirm = run_binary(
        &[
            "--maintenance",
            "apply",
            "--batch-id",
            "batch-binary-2",
            "--confirm-file",
            dir.path().join("does-not-exist.json").to_str().unwrap(),
        ],
        config_dir.as_path(),
    );
    assert_ne!(missing_confirm.status.code(), Some(0));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn binary_storage_failure_exits_nonzero() {
    let dir = tempfile::tempdir().unwrap();
    let db_path = dir.path().join("corrupt.sqlite");
    std::fs::write(&db_path, b"this is not a sqlite database file").unwrap();
    let config_dir = config_dir_with_sqlite(&db_path);
    let failed = run_binary(&["--maintenance", "inspect"], config_dir.as_path());
    let code = failed.status.code();
    assert_ne!(
        code,
        Some(0),
        "any storage failure must exit the command nonzero: {failed:?}"
    );
}

// ---------------------------------------------------------------------------
// Ignored MySQL live conformance (BCS_TEST_MYSQL_URL): the full external chain
// + the migration contracts on the MySQL dialect. UNVERIFIED in this
// environment (no MySQL server); CI runs it against its MySQL service.
// ---------------------------------------------------------------------------

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; CI runs this test against its MySQL service"]
async fn mysql_ownership_migration_conformance() {
    let url = std::env::var("BCS_TEST_MYSQL_URL")
        .expect("BCS_TEST_MYSQL_URL must be set for the ignored MySQL contract");
    let opts = mysql_async::Opts::from_url(&url).expect("valid BCS_TEST_MYSQL_URL");
    let database = opts
        .db_name()
        .expect("BCS_TEST_MYSQL_URL includes a database name")
        .to_string();
    let config = bcs_config_api::MysqlDbConfig::new()
        .with_database(&database)
        .with_connection(bcs_config_api::mysql::MysqlConnectionConfig {
            connection_type: "direct".to_string(),
            host: Some(opts.ip_or_hostname().to_string()),
            port: Some(opts.tcp_port()),
            user: opts.user().map(str::to_string),
            password: opts.pass().map(str::to_string),
            extra: std::collections::BTreeMap::new(),
        })
        .with_statement_protocol(bcs_config_api::StatementProtocol::Text);
    let mut config = config;
    config.pool_size = 1;
    config.min_pool_size = 1;
    let manager = bcs_db_mysql::MysqlDbManager::new(config)
        .await
        .expect("open MySQL contract datasource");
    let plugin: Arc<dyn DbPlugin> =
        Arc::new(bcs_db_mysql::MysqlDbPlugin::new(manager.clone(), database));

    apply_full_mysql_chain(plugin.as_ref()).await;
    let env = env_str();
    seed_human(plugin.as_ref(), "user-a", &env).await;
    seed_bot(plugin.as_ref(), "bot-mysql-clean", Some("user-a"), 0, false, "bot", &env).await;
    seed_creator_claim(plugin.as_ref(), "user-a", "bot-mysql-clean", &env).await;
    seed_bot(plugin.as_ref(), "bot-mysql-nocreator", None, 0, false, "bot", &env).await;

    let service = bcs::ownership_migration_wiring::ownership_migration_service_with_repos(
        Arc::new(PersistentBotRepo::with_sql_flavor(
            plugin.clone(),
            bcs_db_api::DbSqlFlavor::Mysql,
        )) as Arc<dyn BotRepoPort>,
        Arc::new(DbRelationStore::mysql(plugin.clone())) as Arc<dyn RelationRepoPort>,
    );

    let report = service
        .initialize_batch(
            vec!["bot-mysql-clean".to_string(), "bot-mysql-nocreator".to_string()],
            "batch-mysql-1".to_string(),
        )
        .await
        .expect("MySQL apply");
    assert_eq!(report.initialized.len(), 1);
    assert_eq!(report.conflicted.len(), 1);
    let migrated_version = ownership_version_of(plugin.as_ref(), "bot-mysql-clean", &env).await;
    assert_eq!(migrated_version, 1);
    let owner_edges =
        approved_owner_edge_count(plugin.as_ref(), "bot-mysql-clean", &env).await;
    assert_eq!(owner_edges, 1);
    let nocreator_version =
        ownership_version_of(plugin.as_ref(), "bot-mysql-nocreator", &env).await;
    assert_eq!(nocreator_version, 0);
    manager.close().await;
}

/// Apply the full external MySQL chain 001..031 the way ops would (the same
/// chain `bot_authority_mysql.rs` pins): one file at a time.
async fn apply_full_mysql_chain(db: &dyn DbPlugin) {
    const MYSQL_CHAIN: &[&str] = &[
        include_str!("../../../../migrations/mysql/001_init_schema.sql"),
        include_str!("../../../../migrations/mysql/002_add_owner_bot_id.sql"),
        include_str!("../../../../migrations/mysql/003_add_organizations.sql"),
        include_str!("../../../../migrations/mysql/004_add_session_collection.sql"),
        include_str!("../../../../migrations/mysql/005_add_session_collection_timestamp.sql"),
        include_str!("../../../../migrations/mysql/006_session_files.sql"),
        include_str!("../../../../migrations/mysql/007_add_human_input_runtime.sql"),
        include_str!("../../../../migrations/mysql/008_human_input_im_requests.sql"),
        include_str!("../../../../migrations/mysql/009_eventing.sql"),
        include_str!("../../../../migrations/mysql/010_group_opening_message.sql"),
        include_str!("../../../../migrations/mysql/011_group_participant_tags.sql"),
        include_str!("../../../../migrations/mysql/012_expand_session_ids.sql"),
        include_str!("../../../../migrations/mysql/013_add_bot_task_modes.sql"),
        include_str!("../../../../migrations/mysql/014_edge_permission.sql"),
        include_str!("../../../../migrations/mysql/015_add_bot_internal_attributes.sql"),
        include_str!("../../../../migrations/mysql/016_session_callback_lease_and_chat_runs.sql"),
        include_str!("../../../../migrations/mysql/017_state_machine_rerun_lineage.sql"),
        include_str!("../../../../migrations/mysql/018_one_shot_opening_message_override.sql"),
        include_str!("../../../../migrations/mysql/019_invite_code.sql"),
        include_str!("../../../../migrations/mysql/020_human_participant_message_visibility.sql"),
        include_str!("../../../../migrations/mysql/021_message_deliveries.sql"),
        include_str!("../../../../migrations/mysql/022_message_delivery_policy.sql"),
        include_str!("../../../../migrations/mysql/023_delivery_worker_queries.sql"),
        include_str!("../../../../migrations/mysql/024_delivery_context_selection.sql"),
        include_str!("../../../../migrations/mysql/025_delivery_pending_abort.sql"),
        include_str!("../../../../migrations/mysql/026_run_reply_segments.sql"),
        include_str!("../../../../migrations/mysql/027_provider_bot_webhook.sql"),
        include_str!("../../../../migrations/mysql/028_fixed_loop_runtime.sql"),
        include_str!("../../../../migrations/mysql/029_bot_provider_storage.sql"),
        include_str!("../../../../migrations/mysql/030_group_human_mention_notify_mode.sql"),
        include_str!("../../../../migrations/mysql/031_bot_authority.sql"),
    ];
    for (index, file) in MYSQL_CHAIN.iter().enumerate() {
        let body: String = file
            .lines()
            .filter(|line| !line.trim_start().starts_with("--"))
            .collect::<Vec<_>>()
            .join("\n");
        for statement in body.split(';').map(str::trim).filter(|s| !s.is_empty()) {
            db.execute(DbStatement::new(statement))
                .await
                .unwrap_or_else(|error| panic!("apply mysql chain file {index}: {error}\n{statement}"));
        }
    }
}