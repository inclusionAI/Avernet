//! Atomic ownership lifecycle storage boundary (plan Task 5): first-ownership
//! initialization consumed in the same commit as Bot/Provider creation,
//! the governed `initialize_existing_ownership` entry, and the atomic
//! deletion boundary (role-edge withdrawal, pending-transfer termination,
//! Human-owner protection).
//!
//! Every asserted variable comes from a real repository query (`try_get`,
//! `try_load_token`, `find_bot_by_token`) or a direct row projection of the
//! store's tables — never a mocked boolean. Failure injection replaces a
//! marked transaction step with a failing statement, so the failure happens
//! INSIDE the single commit and the rollback is the real store rollback.
//!
//! Convergence: each case drives deterministic interleavings with
//! `Barrier`; the transfer-acceptance probe is the future acceptance
//! primitive reusing the Task 2 proven shape (bot lock first, version CAS,
//! pending-slot consume) — acceptance is a later task's production entry,
//! so the probe encodes its locking discipline to prove retirement's
//! leader-wins semantics without importing a store that does not exist yet.

use support::*;

#[path = "common/ownership.rs"]
mod support;

// ---------------------------------------------------------------------------
// Step-1 RED: failure atomicity of new-Bot registration with initialization.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn sqlite_new_registration_with_initialization_is_atomic_per_failing_step() {
    for marker in [
        CAS_MARKER,
        OWNER_EDGE_MARKER,
        PROFILE_MARKER,
        INIT_AUDIT_MARKER,
    ] {
        let db = sqlite().await;
        let injected = InjectedStepDb::new(db.clone());
        injected.arm(marker);
        let repo = persistent(injected.clone());
        let token = format!("token-{marker}");
        let result = repo
            .create_registration_if_absent_with_initialization(
                "bot-new".into(),
                caps("new"),
                "owner-registration",
                &token,
                human_init("user-1"),
            )
            .await;
        let new_registration_result = result;
        assert!(
            new_registration_result.is_err(),
            "injected failure at {marker} must fail the whole registration"
        );
        assert!(
            !new_registration_result
                .unwrap_err()
                .to_string()
                .contains(&token),
            "failures never echo the runtime token"
        );
        // Real repo queries: the registration, token indexes and every
        // authority row of the would-be Bot must all be absent.
        assert!(repo.try_get("bot-new").await.unwrap().is_none());
        assert!(repo.load_token("bot-new").await.is_none());
        assert!(repo.find_bot_by_token(&token).await.is_none());
        assert_eq!(ownership_version_of(db.as_ref(), "bot-new").await, 0);
        assert_eq!(
            scalar(
                db.as_ref(),
                "SELECT COUNT(*) AS value FROM bcs_bots WHERE bot_uuid = 'bot-new'",
                vec![],
            )
            .await,
            0,
            "the rolled-back registration must not leave a Bot row"
        );
        assert_eq!(approved_owner_edge_count(db.as_ref(), "bot-new").await, 0);
        assert_eq!(initialization_count(db.as_ref(), "bot-new").await, 0);
        assert_eq!(default_profile_count(db.as_ref(), "bot-new").await, 0);
        // A clean retry after the injected failure recreates everything once.
        injected.disarm();
        assert!(
            repo.create_registration_if_absent_with_initialization(
                "bot-new".into(),
                caps("new"),
                "owner-registration",
                &token,
                human_init("user-1"),
            )
            .await
            .unwrap(),
            "retry after rollback must create the Bot"
        );
        assert_eq!(ownership_version_of(db.as_ref(), "bot-new").await, 1);
        assert_eq!(approved_owner_edge_count(db.as_ref(), "bot-new").await, 1);
        assert_eq!(initialization_count(db.as_ref(), "bot-new").await, 1);
        assert_eq!(default_profile_count(db.as_ref(), "bot-new").await, 1);
    }
}

#[tokio::test]
async fn memory_new_registration_with_initialization_is_atomic_on_injected_failure() {
    let temp = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(temp.path().into());
    repo.arm_authority_write_failure();
    let new_registration_result = repo
        .create_registration_if_absent_with_initialization(
            "bot-new".into(),
            caps("new"),
            "owner-registration",
            "token-new",
            human_init("user-1"),
        )
        .await;
    assert!(new_registration_result.is_err());
    assert!(!new_registration_result.unwrap_err().to_string().contains("token-new"));
    assert!(repo.get("bot-new").await.is_none());
    assert!(repo.load_token("bot-new").await.is_none());
    assert!(
        !temp.path().join("bot-new/bot.json").exists(),
        "no partial registration file may survive"
    );
    assert_eq!(repo.authority_ownership_initialization_count("bot-new").await.unwrap(), 0);
    // Retry publishes exactly one registration with all authority rows.
    assert!(
        repo.create_registration_if_absent_with_initialization(
            "bot-new".into(),
            caps("new"),
            "owner-registration",
            "token-new",
            human_init("user-1"),
        )
        .await
        .unwrap()
    );
    assert!(repo.get("bot-new").await.is_some());
    assert_eq!(
        repo.ownership("bot-new").await.unwrap().owner_user_id,
        "user-1"
    );
    assert_eq!(repo.authority_ownership_initialization_count("bot-new").await.unwrap(), 1);
}

// ---------------------------------------------------------------------------
// Existing runtime Bot (version 0) through the governed initialization
// entry: failed attempts leave the pre-existing registration untouched.
// ---------------------------------------------------------------------------

async fn runtime_bot_sqlite(db: &Arc<dyn DbPlugin>, repo: &PersistentBotRepo) {
    assert!(
        repo.create_registration_if_absent(
            "bot-runtime".into(),
            caps("runtime"),
            "owner-runtime",
            "token-runtime",
        )
        .await
        .unwrap()
    );
    assert_eq!(ownership_version_of(db.as_ref(), "bot-runtime").await, 0);
}

#[tokio::test]
async fn sqlite_initialize_existing_ownership_failure_leaves_the_runtime_bot_intact() {
    for marker in [OWNER_EDGE_MARKER, PROFILE_MARKER, INIT_AUDIT_MARKER] {
        let db = sqlite().await;
        let injected = InjectedStepDb::new(db.clone());
        let repo = persistent(injected.clone());
        runtime_bot_sqlite(&db, &repo).await;
        injected.arm(marker);
        let existing_initialization_result = repo
            .initialize_existing_ownership("bot-runtime", human_init("user-1"))
            .await;
        assert!(
            existing_initialization_result.is_err(),
            "injected failure at {marker} must fail the initialization"
        );
        // Real repo queries: the pre-existing registration, its fields and
        // its version-0 slot are untouched; no authority rows leaked.
        let bot = repo.try_get("bot-runtime").await.unwrap().unwrap();
        assert_eq!(bot.capabilities.name.as_deref(), Some("runtime"));
        assert!(
            repo.try_load_token("bot-runtime")
                .await
                .unwrap()
                .as_deref()
                == Some("token-runtime")
        );
        let runtime_bot_version_after_failure = ownership_version_of(db.as_ref(), "bot-runtime").await;
        assert_eq!(runtime_bot_version_after_failure, 0);
        assert_eq!(approved_owner_edge_count(db.as_ref(), "bot-runtime").await, 0);
        assert_eq!(initialization_count(db.as_ref(), "bot-runtime").await, 0);
        assert_eq!(
            default_profile_count(db.as_ref(), "bot-runtime").await, 0,
            "the audit-marker case must roll back the profile ensure too"
        );
        assert!(!is_deleted(db.as_ref(), "bot-runtime").await);
        injected.disarm();
        let state = repo
            .initialize_existing_ownership("bot-runtime", human_init("user-1"))
            .await
            .unwrap();
        assert_eq!(state.owner_user_id, "user-1");
        assert_eq!(state.ownership_version, 1);
        assert_eq!(ownership_version_of(db.as_ref(), "bot-runtime").await, 1);
        assert_eq!(approved_owner_edge_count(db.as_ref(), "bot-runtime").await, 1);
    }
}

#[tokio::test]
async fn memory_initialize_existing_ownership_failure_leaves_the_runtime_bot_intact() {
    let temp = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(temp.path().into());
    assert!(
        repo.create_registration_if_absent(
            "bot-runtime".into(),
            caps("runtime"),
            "owner-runtime",
            "token-runtime",
        )
        .await
        .unwrap()
    );
    repo.arm_authority_write_failure();
    let result = repo.initialize_existing_ownership("bot-runtime", human_init("user-1")).await;
    assert!(result.is_err());
    let runtime_bot_exists_after_failure = repo.get("bot-runtime").await.is_some();
    assert!(runtime_bot_exists_after_failure);
    let bot = repo.get("bot-runtime").await.unwrap();
    assert_eq!(bot.capabilities.name.as_deref(), Some("runtime"));
    assert!(matches!(
        repo.ownership("bot-runtime").await.unwrap_err(),
        ServiceError::Authority(AuthorityError::OwnershipNotInitialized { .. })
    ));
    assert_eq!(repo.authority_ownership_initialization_count("bot-runtime").await.unwrap(), 0);
    assert_eq!(repo.authority_default_profile_id("bot-runtime").await.unwrap(), None);
    // The clean retry initializes exactly once.
    let state = repo
        .initialize_existing_ownership("bot-runtime", human_init("user-1"))
        .await
        .unwrap();
    assert_eq!(state.owner_user_id, "user-1");
    assert_eq!(state.ownership_version, 1);
    assert_eq!(repo.authority_ownership_initialization_count("bot-runtime").await.unwrap(), 1);
}

// ---------------------------------------------------------------------------
// Default profile id stability: a failed retry must neither leak a partial
// profile row nor rebuild/recreate the existing default on the next success.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn sqlite_default_profile_id_is_stable_across_a_failed_retry() {
    let db = sqlite().await;
    let injected = InjectedStepDb::new(db.clone());
    let repo = persistent(injected.clone());
    runtime_bot_sqlite(&db, &repo).await;
    // Pre-existing default profile row (id captured from a real query).
    db.execute(DbStatement::with_params(
        "INSERT INTO permission_profiles (bot_id, env, name, description, rules_template, \
         revision, digest, is_default, status, created_by, updated_by) \
         VALUES ('bot-runtime', ?, 'default-preseeded', NULL, ?, 3, ?, 1, 'active', 'system', NULL)",
        vec![
            bcs_config::resolve_env_str().into(),
            "[{\"tool\":\"*\",\"specifier\":\"*\",\"effect\":\"allow\"}]".into(),
            "id-stable-digest".into(),
        ],
    ))
    .await
    .unwrap();
    let default_profile_id_before_retry = default_profile_id(db.as_ref(), "bot-runtime").await;
    injected.arm(INIT_AUDIT_MARKER);
    assert!(
        repo.initialize_existing_ownership("bot-runtime", human_init("user-1"))
            .await
            .is_err()
    );
    assert_eq!(
        default_profile_count(db.as_ref(), "bot-runtime").await,
        1,
        "the failed attempt must not add a second default profile"
    );
    injected.disarm();
    repo.initialize_existing_ownership("bot-runtime", human_init("user-1"))
        .await
        .unwrap();
    let default_profile_id_after_retry = default_profile_id(db.as_ref(), "bot-runtime").await;
    assert_eq!(
        default_profile_id_after_retry, default_profile_id_before_retry,
        "the ensure must not rebuild, re-key or re-id the pre-existing default"
    );
    assert_eq!(default_profile_count(db.as_ref(), "bot-runtime").await, 1);
}

#[tokio::test]
async fn memory_default_profile_id_is_stable_across_failed_and_rejected_retries() {
    let temp = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(temp.path().into());
    assert!(
        repo.create_registration_if_absent(
            "bot-runtime".into(),
            caps("runtime"),
            "owner-runtime",
            "token-runtime",
        )
        .await
        .unwrap()
    );
    repo.initialize_existing_ownership("bot-runtime", human_init("user-1"))
        .await
        .unwrap();
    let default_profile_id_before_retry = repo
        .authority_default_profile_id("bot-runtime")
        .await
        .unwrap();
    let after_first_success = default_profile_id_before_retry;
    assert!(after_first_success.is_some());
    // An armed failure consumes nothing of the committed state...
    repo.arm_authority_write_failure();
    assert!(
        repo.initialize_existing_ownership("bot-runtime", human_init("user-1"))
            .await
            .is_err()
    );
    // ...and the already-initialized Bot refuses re-initialization.
    assert!(matches!(
        repo.initialize_existing_ownership("bot-runtime", human_init("user-1")).await.unwrap_err(),
        ServiceError::Authority(AuthorityError::Conflict(_))
    ));
    let default_profile_id_after_retry = repo
        .authority_default_profile_id("bot-runtime")
        .await
        .unwrap();
    assert_eq!(
        default_profile_id_after_retry, default_profile_id_before_retry,
        "neither a failed nor a rejected retry may recreate the default profile"
    );
}

// ---------------------------------------------------------------------------
// Runtime semantics and create-once: no initialization supplied -> version
// stays 0; a duplicate registration cannot replay credentials or initialize.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn sqlite_runtime_registration_stays_uninitialized_until_initialized() {
    let db = sqlite().await;
    let repo = persistent(db.clone());
    runtime_bot_sqlite(&db, &repo).await;
    assert_eq!(ownership_version_of(db.as_ref(), "bot-runtime").await, 0);
    let state = repo
        .initialize_existing_ownership("bot-runtime", system_init("user-1"))
        .await
        .unwrap();
    assert_eq!(state.ownership_version, 1);
    // Second initialization is refused (CAS exactness, never auto re-claim).
    assert!(matches!(
        repo.initialize_existing_ownership("bot-runtime", human_init("user-2")).await.unwrap_err(),
        ServiceError::Authority(AuthorityError::Conflict(_))
    ));
    assert_eq!(ownership_version_of(db.as_ref(), "bot-runtime").await, 1);
    assert_eq!(approved_owner_edge_count(db.as_ref(), "bot-runtime").await, 1);
    // The human actor materialization is preserved across failures/retries.
    let env = bcs_config::resolve_env_str();
    assert_eq!(
        scalar(
            db.as_ref(),
            "SELECT COUNT(*) AS value FROM bcs_bots \
             WHERE bot_uuid = 'human_user-1' AND env = ? AND actor_kind = 'human'",
            vec![env.into()],
        )
        .await,
        1
    );
}

#[tokio::test]
async fn sqlite_duplicate_create_with_initialization_never_replays_or_reinitializes() {
    let db = sqlite().await;
    let repo = persistent(db.clone());
    let token = "token-original";
    assert!(
        repo.create_registration_if_absent_with_initialization(
            "bot-dup".into(),
            caps("original"),
            "owner-registration",
            token,
            human_init("user-1"),
        )
        .await
        .unwrap()
    );
    assert!(
        !repo.create_registration_if_absent_with_initialization(
            "bot-dup".into(),
            caps("stale"),
            "other-owner",
            "token-stale",
            human_init("user-2"),
        )
        .await
        .unwrap(),
        "create-once keeps the original registration"
    );
    assert_eq!(ownership_version_of(db.as_ref(), "bot-dup").await, 1);
    assert_eq!(approved_owner_edge_count(db.as_ref(), "bot-dup").await, 1);
    assert_eq!(initialization_count(db.as_ref(), "bot-dup").await, 1);
    let bot = repo.try_get("bot-dup").await.unwrap().unwrap();
    assert_eq!(bot.capabilities.name.as_deref(), Some("original"));
    assert!(
        repo.try_load_token("bot-dup")
            .await
            .unwrap()
            .as_deref()
            == Some(token)
    );
    assert!(repo.find_bot_by_token("token-stale").await.is_none());
}

#[tokio::test]
async fn service_actor_or_empty_owner_initialization_is_rejected_fail_closed() {
    let db = sqlite().await;
    let repo = persistent(db.clone());
    for initialization in [service_init("user-1"), {
        let mut empty = human_init("");
        empty.owner_user_id = "  ".into();
        empty
    }] {
        let error = repo
            .create_registration_if_absent_with_initialization(
                "bot-guard".into(),
                caps("guard"),
                "owner-registration",
                "token-guard",
                initialization,
            )
            .await
            .unwrap_err();
        assert!(
            matches!(error, ServiceError::InvalidOperation { .. }),
            "actor/owner shape must be rejected before any write: {error}"
        );
        assert!(repo.try_get("bot-guard").await.unwrap().is_none());
        assert_eq!(approved_owner_edge_count(db.as_ref(), "bot-guard").await, 0);
        assert_eq!(
            scalar(
                db.as_ref(),
                "SELECT COUNT(*) AS value FROM bcs_bots WHERE bot_uuid = 'bot-guard'",
                vec![],
            )
            .await,
            0
        );
    }
    // The same fail-closed rule bounds the memory repo.
    let temp = tempfile::tempdir().unwrap();
    let memory = MemoryBotRepo::with_base_dir(temp.path().into());
    assert!(
        memory
            .create_registration_if_absent_with_initialization(
                "bot-guard".into(),
                caps("guard"),
                "owner-registration",
                "token-guard",
                service_init("user-1"),
            )
            .await
            .is_err()
    );
    assert!(memory.get("bot-guard").await.is_none());
}

// ---------------------------------------------------------------------------
// Concurrency RED: create-once with initialization has exactly one winner
// and one authority slot, without replaying credentials or audits.
// ---------------------------------------------------------------------------

async fn race_either_store(repos: Vec<Arc<dyn BotRepoPort>>, bot: &str) {
    let barrier = Arc::new(Barrier::new(repos.len()));
    let mut tasks = Vec::new();
    for repo in repos {
        let barrier = barrier.clone();
        let bot = bot.to_string();
        tasks.push(tokio::spawn(async move {
            barrier.wait().await;
            (
                repo.create_registration_if_absent_with_initialization(
                    bot.clone(),
                    caps("racer"),
                    "owner-registration",
                    "token-raced",
                    human_init("user-racer"),
                )
                .await
                .unwrap(),
                repo,
            )
        }));
    }
    let mut winners = 0usize;
    let mut repo_of_winner: Option<Arc<dyn BotRepoPort>> = None;
    for task in tasks {
        let (created, repo) = task.await.unwrap();
        if created {
            winners += 1;
            repo_of_winner = Some(repo);
        }
    }
    assert_eq!(winners, 1, "exactly one concurrent registration may create");
    let repo = repo_of_winner.expect("one winner");
    let bot_row = repo.try_get(bot).await.unwrap().expect("winner's row");
    assert_eq!(bot_row.capabilities.name.as_deref(), Some("racer"));
    assert!(repo.load_token(bot).await.as_deref() == Some("token-raced"));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn concurrent_memory_registrations_have_one_winner_and_one_authority_slot() {
    let temp = tempfile::tempdir().unwrap();
    let shared: Arc<MemoryBotRepo> = Arc::new(MemoryBotRepo::with_base_dir(temp.path().into()));
    let repos: Vec<Arc<dyn BotRepoPort>> = (0..12)
        .map(|_| shared.clone() as Arc<dyn BotRepoPort>)
        .collect();
    race_either_store(repos, "bot-race").await;
    assert_eq!(shared.ownership("bot-race").await.unwrap().owner_user_id, "user-racer");
    assert_eq!(
        shared
            .authority_ownership_initialization_count("bot-race")
            .await
            .unwrap(),
        1
    );
    assert!(shared.authority_default_profile_id("bot-race").await.unwrap().is_some());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn concurrent_sqlite_registrations_have_one_winner_and_one_authority_slot() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("race.sqlite");
    sqlite_file(&path).await; // install the schema through one connection
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());
    let repos: Vec<Arc<dyn BotRepoPort>> = (0..12)
        .map(|_| Arc::new(persistent(db.clone())) as Arc<dyn BotRepoPort>)
        .collect();
    race_either_store(repos, "bot-race").await;
    assert_eq!(ownership_version_of(db.as_ref(), "bot-race").await, 1);
    assert_eq!(approved_owner_edge_count(db.as_ref(), "bot-race").await, 1);
    assert_eq!(initialization_count(db.as_ref(), "bot-race").await, 1);
    assert_eq!(default_profile_count(db.as_ref(), "bot-race").await, 1);
}

// ---------------------------------------------------------------------------
// Provider creation consumes the initialization in the same commit; the
// Provider/ref tombstone never replays credentials.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn sqlite_provider_creation_consumes_initialization_in_the_same_commit() {
    let db = sqlite().await;
    let store = DbProviderStore::sqlite(db.clone());
    store
        .create_provider_bot_with_initialization(
            record("bot-gw", BotConnectionMode::Gateway),
            caps("gateway"),
            "owner-registration",
            "token-gateway",
            human_init("user-1"),
        )
        .await
        .unwrap();
    assert_eq!(ownership_version_of(db.as_ref(), "bot-gw").await, 1);
    assert_eq!(approved_owner_edge_count(db.as_ref(), "bot-gw").await, 1);
    assert_eq!(initialization_count(db.as_ref(), "bot-gw").await, 1);
    assert_eq!(default_profile_count(db.as_ref(), "bot-gw").await, 1);
    assert_eq!(
        scalar(
            db.as_ref(),
            "SELECT COUNT(*) AS value FROM bcs_provider_bot_bindings WHERE bot_uuid = 'bot-gw'",
            vec![],
        )
        .await,
        1
    );
    // Upstream (non-gateway) creation shares the same one-commit contract.
    store
        .create_provider_bot_with_initialization(
            record("bot-up", BotConnectionMode::Plugin),
            caps("upstream"),
            "owner-registration",
            "token-upstream",
            system_init("user-2"),
        )
        .await
        .unwrap();
    assert_eq!(ownership_version_of(db.as_ref(), "bot-up").await, 1);
    assert_eq!(approved_owner_edge_count(db.as_ref(), "bot-up").await, 1);
    let owner_source = scalar(
        db.as_ref(),
        "SELECT COUNT(*) AS value FROM bot_ownership_initializations WHERE bot_id = 'bot-up' AND \
         source = 'governed_repair'",
        vec![],
    )
    .await;
    assert_eq!(owner_source, 0, "Provider creation initializes as 'registration'");
}

#[tokio::test]
async fn memory_provider_creation_consumes_initialization_in_the_same_commit() {
    let temp = tempfile::tempdir().unwrap();
    let bots: Arc<MemoryBotRepo> = Arc::new(MemoryBotRepo::with_base_dir(temp.path().into()));
    let repo: Arc<dyn BotRepoPort> = bots.clone();
    let bindings = Arc::new(MemoryProviderStore::new());
    let store = MemoryBotProviderStore::new(repo, bindings);
    store
        .create_provider_bot_with_initialization(
            record("bot-gw", BotConnectionMode::Gateway),
            caps("gateway"),
            "owner-registration",
            "token-gateway",
            human_init("user-1"),
        )
        .await
        .unwrap();
    assert!(bots.try_get("bot-gw").await.unwrap().is_some());
    assert_eq!(
        bots.ownership("bot-gw").await.unwrap().owner_user_id,
        "user-1",
        "the Provider bot's authority lives in the same shared critical section"
    );
    assert_eq!(
        bots.authority_ownership_initialization_count("bot-gw")
            .await
            .unwrap(),
        1
    );
    assert!(bots.authority_default_profile_id("bot-gw").await.unwrap().is_some());
}

#[tokio::test]
async fn sqlite_provider_ref_tombstone_never_replays_credentials_or_reinitializes() {
    let db = sqlite().await;
    let store = DbProviderStore::sqlite(db.clone());
    let first = record("bot-tomb", BotConnectionMode::Plugin);
    store
        .create_provider_bot_with_initialization(
            first.clone(),
            caps("tombstone"),
            "owner-registration",
            "token-tomb",
            human_init("user-1"),
        )
        .await
        .unwrap();
    assert!(store.delete_provider_bot("provider-a", "bot-tomb", 1).await.unwrap());
    // The tombstoned ref is rejected for a new Bot, with new credentials.
    let second = record("bot-tomb", BotConnectionMode::Gateway);
    let error = store
        .create_provider_bot_with_initialization(
            second,
            caps("resurrection"),
            "owner-registration",
            "token-resurrect",
            human_init("user-2"),
        )
        .await
        .unwrap_err();
    assert_conflict(&error);
    // Real queries: deleted bot keeps no live credential and no new
    // registration materialized.
    let repo = persistent(db.clone());
    assert!(repo.try_load_token("bot-tomb").await.unwrap().is_none());
    assert!(repo.find_bot_by_token("token-tomb").await.is_none());
    assert!(repo.find_bot_by_token("token-resurrect").await.is_none());
    // Contract change (Task 17 orphan-edge carry): the Provider tombstone is
    // a RETIREMENT in the same commit — the approved owner edge (and every
    // role edge) is withdrawn, so no orphan owner edge survives a Provider
    // delete. The initialization LEDGER row stays: `bot_ownership_initializations`
    // is append-only audit (and the migration batch-recovery substrate), never
    // authority state — retirement never deletes history.
    assert_eq!(approved_owner_edge_count(db.as_ref(), "bot-tomb").await, 0);
    assert_eq!(
        approved_role_edge_count_to(db.as_ref(), "bot-tomb").await,
        0,
        "the tombstone withdraws every approved role edge"
    );
    assert_eq!(initialization_count(db.as_ref(), "bot-tomb").await, 1);
    // A duplicate living ref is rejected identically on the living bot.
    let store2 = DbProviderStore::sqlite(db.clone());
    store2
        .create_provider_bot_with_initialization(
            record("bot-live", BotConnectionMode::Plugin),
            caps("live"),
            "owner-registration",
            "token-live",
            human_init("user-3"),
        )
        .await
        .unwrap();
    let dup = store2
        .create_provider_bot_with_initialization(
            {
                let mut duplicate = record("bot-live", BotConnectionMode::Plugin);
                duplicate.provider_bot_ref = "bot-live".into();
                duplicate
            },
            caps("duplicate"),
            "owner-registration",
            "token-live-2",
            human_init("user-4"),
        )
        .await;
    assert!(dup.is_err());
    assert_eq!(ownership_version_of(db.as_ref(), "bot-live").await, 1);
    assert!(
        persistent(db.clone())
            .try_load_token("bot-live")
            .await
            .unwrap()
            .as_deref()
            == Some("token-live")
    );
}

#[tokio::test]
async fn memory_provider_ref_tombstone_never_replays_credentials_or_reinitializes() {
    let temp = tempfile::tempdir().unwrap();
    let bots: Arc<MemoryBotRepo> = Arc::new(MemoryBotRepo::with_base_dir(temp.path().into()));
    let repo: Arc<dyn BotRepoPort> = bots.clone();
    let bindings = Arc::new(MemoryProviderStore::new());
    let store = MemoryBotProviderStore::new(repo, bindings);
    store
        .create_provider_bot_with_initialization(
            record("bot-tomb", BotConnectionMode::Plugin),
            caps("tombstone"),
            "owner-registration",
            "token-tomb",
            human_init("user-1"),
        )
        .await
        .unwrap();
    assert!(store.delete_provider_bot("provider-a", "bot-tomb", 1).await.unwrap());
    let error = store
        .create_provider_bot_with_initialization(
            record("bot-tomb", BotConnectionMode::Gateway),
            caps("resurrection"),
            "owner-registration",
            "token-resurrect",
            human_init("user-2"),
        )
        .await
        .unwrap_err();
    assert_conflict(&error);
    assert!(bots.get("bot-tomb").await.is_none());
    assert!(bots.load_token("bot-tomb").await.is_none());
    // Contract change (Task 17 orphan-edge carry): the memory tombstone also
    // consumes the retirement lane — the owner edge is withdrawn inside the
    // same critical section and a `delete/bot/applied` audit record is
    // appended. The initialization LEDGER projection stays (append-only
    // history, never authority state).
    assert_eq!(bots.authority_ownership_initialization_count("bot-tomb").await.unwrap(), 1);
    assert!(
        bots.authority_action_audit_records()
            .await
            .unwrap()
            .iter()
            .any(|record| record.resource_id == "bot-tomb"
                && record.action == bcs_service_api::types::BotActionKind::Delete
                && record.phase == bcs_service_api::types::BotActionAuditPhase::Applied),
        "the tombstone appends the same-commit retirement audit row"
    );
    // The tombstoned authority surface is unreachable for reads.
    assert_bot_not_found(&bots.ownership("bot-tomb").await.unwrap_err());
}

// ---------------------------------------------------------------------------
// Ignored MySQL live conformance (BCS_TEST_MYSQL_URL): the initialization and
// retirement contract on the MySQL dialect. UNVERIFIED in this environment
// (no MySQL server); CI runs it against its MySQL service.
// ---------------------------------------------------------------------------

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; CI runs this test against its MySQL service"]
async fn mysql_ownership_lifecycle_conformance() {
    let db = mysql_url_plugin().await;
    let repo = persistent(db.clone());
    assert!(
        repo.create_registration_if_absent_with_initialization(
            "bot-mysql".into(),
            caps("mysql"),
            "owner-registration",
            "token-mysql",
            human_init("user-1"),
        )
        .await
        .unwrap(),
        "MySQL create-with-initialization commits in one transaction"
    );
    assert_eq!(ownership_version_of(db.as_ref(), "bot-mysql").await, 1);
    assert_eq!(approved_owner_edge_count(db.as_ref(), "bot-mysql").await, 1);
    assert_eq!(initialization_count(db.as_ref(), "bot-mysql").await, 1);
    assert_eq!(default_profile_count(db.as_ref(), "bot-mysql").await, 1);
    // Duplicate create-once: no re-initialization, no credential replay.
    assert!(
        !repo.create_registration_if_absent_with_initialization(
            "bot-mysql".into(),
            caps("stale"),
            "other-owner",
            "token-stale",
            human_init("user-2"),
        )
        .await
        .unwrap()
    );
    assert_eq!(ownership_version_of(db.as_ref(), "bot-mysql").await, 1);
    assert_eq!(approved_owner_edge_count(db.as_ref(), "bot-mysql").await, 1);
    assert_eq!(initialization_count(db.as_ref(), "bot-mysql").await, 1);
    // Governed entry + CAS conflict on the initialized row.
    assert!(matches!(
        repo.initialize_existing_ownership("bot-mysql", human_init("user-2"))
            .await
            .unwrap_err(),
        ServiceError::Authority(AuthorityError::Conflict(_))
    ));
    assert!(matches!(
        repo.initialize_existing_ownership("bot-missing", human_init("user-2"))
            .await
            .unwrap_err(),
        ServiceError::BotNotFound(_)
    ));
    // Retirement terminates the pending slot and withdraws both role edges.
    seed_manager_edge(db.as_ref(), "bot-mysql", "user-mgr").await;
    seed_pending_transfer(db.as_ref(), "bot-mysql", "user-1", "user-2").await;
    assert!(repo.retire_bot_lifecycle("bot-mysql", operation("user-1")).await.unwrap());
    assert!(is_deleted(db.as_ref(), "bot-mysql").await);
    assert_eq!(approved_role_edge_count_to(db.as_ref(), "bot-mysql").await, 0);
    let (_, status, reason) = pending_transfer_row(db.as_ref(), "bot-mysql").await;
    assert_eq!(status, "invalidated");
    assert_eq!(reason.as_deref(), Some("bot_deleted"));
    // Live owner protection and the human deletion lane.
    assert!(
        repo.create_registration_if_absent_with_initialization(
            "bot-owned".into(),
            caps("owned"),
            "owner-registration",
            "token-owned",
            human_init("user-owner"),
        )
        .await
        .unwrap()
    );
    assert!(matches!(
        repo.delete_human_actor("user-owner", operation("admin-1"))
            .await
            .unwrap_err(),
        ServiceError::Authority(AuthorityError::Forbidden(_))
    ));
    assert!(repo.retire_bot_lifecycle("bot-owned", operation("user-owner")).await.unwrap());
    assert!(repo.delete_human_actor("user-owner", operation("admin-1")).await.unwrap());
    assert_eq!(
        approved_role_edge_count_from(db.as_ref(), "human_user-owner").await,
        0
    );
}
