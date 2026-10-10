//! `BotAuthorityRepoPort` / `BotAuthorityCoreService` conformance harnesses
//! (Rule 25, plan Task 3).
//!
//! The same shared snapshot suite runs against every authority driver
//! (SQLite via `bcs-edge-permission-store`, in-memory via `bcs-bot-store`)
//! and the production Core (`bcs-edge-permission::authority`), so driver
//! drift is impossible: a strictness rule asserted here holds for every
//! implementation.
//!
//! Strictness baseline (spec §5, §12.4):
//! - `roles_for` results are position-aligned with the input pairs; an
//!   absent active role is `None`, and any corruption/query failure is
//!   `Err` — a missing row is never treated as allowed and no method may
//!   default to an empty-success.
//! - Uninitialized ownership (`version = 0`) is the
//!   `OwnershipNotInitialized` business branch, never a snapshot.
//! - An initialized Bot whose approved owner edge is missing is corrupt
//!   (`CorruptAuthority`), never an ordinary deny.
//! - Failed writes never fabricate readable state or audit rows.
//!
//! The seeding surface ([`AuthorityHarnessDriver`]) is test-only. Its
//! `seed_owned` writes a legal Bot + version + OwnerEdge per the Task 2
//! schema atomically; it exists to build read-test preconditions before
//! the Task 5 production initialization contract lands and must never be
//! reachable from production claim paths. Corruption cases are injected
//! through the driver (`seed_uninitialized_bot`, `break_owner_edge`).

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{BotAccessRelation, OwnershipState};
use bcs_service_api::core::BotAuthorityCoreService;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::{ServiceError, ServiceResult};

/// Driver-implemented test seeding surface for the authority contract.
///
/// One implementation per driver (Memory / SQL). Every method exists only
/// to build test state; none of them is a production claim entry.
#[async_trait]
pub trait AuthorityHarnessDriver: Send + Sync {
    /// Atomically write one legal `Bot` (live, version 1) plus its approved
    /// `owner` role edge, per the Task 2 schema. Test-only stand-in for the
    /// Task 5 production initialization contract.
    async fn seed_owned(&self, bot_id: &str, owner_user_id: &str) -> ServiceResult<()>;

    /// Materialize a real Human actor for `user_id` (the production
    /// `ensure_human_actor` path: `human_<user_id>` actor row).
    async fn seed_human(&self, user_id: &str) -> ServiceResult<()>;

    /// Arm a one-shot failure of the next authority write (drivers surface
    /// the failure; reads must keep reporting the truth unchanged).
    fn fail_next_write(&self);

    /// Count authority audit rows (`bot_manager_changes`); Task 3 performs
    /// no audited authority writes, so drivers must report 0 until the
    /// Task 4/5 mutation contracts land.
    async fn audit_count(&self) -> ServiceResult<u64>;

    /// Corruption lever: insert a live `Bot` row whose ownership_version
    /// is 0 (uninitialized). Driver-injected test state only.
    async fn seed_uninitialized_bot(&self, bot_id: &str) -> ServiceResult<()>;

    /// Corruption lever: remove the Bot's approved owner edge while the
    /// Bot keeps ownership_version > 0 (initialized-but-ownerless).
    /// Driver-injected test state only.
    async fn break_owner_edge(&self, bot_id: &str) -> ServiceResult<()>;
}

/// Cross-driver authority harness: the strict repo under test plus the
/// driver-implemented seeding surface.
///
/// `repo` is the production `Arc<dyn BotAuthorityRepoPort>`; the driver
/// only builds preconditions. Tests must reach the implementation through
/// the port, never through the driver.
pub struct AuthorityRepoHarness {
    /// The production authority repo under conformance.
    pub repo: Arc<dyn BotAuthorityRepoPort>,
    /// Test-only seeding/corruption lever implemented per driver.
    pub driver: Arc<dyn AuthorityHarnessDriver>,
}

impl AuthorityRepoHarness {
    pub fn new(
        repo: Arc<dyn BotAuthorityRepoPort>,
        driver: Arc<dyn AuthorityHarnessDriver>,
    ) -> Self {
        Self { repo, driver }
    }

    /// See [`AuthorityHarnessDriver::seed_owned`].
    pub async fn seed_owned(&self, bot_id: &str, owner_user_id: &str) -> ServiceResult<()> {
        self.driver.seed_owned(bot_id, owner_user_id).await
    }

    /// See [`AuthorityHarnessDriver::seed_human`].
    pub async fn seed_human(&self, user_id: &str) -> ServiceResult<()> {
        self.driver.seed_human(user_id).await
    }

    /// See [`AuthorityHarnessDriver::fail_next_write`].
    pub fn fail_next_write(&self) {
        self.driver.fail_next_write();
    }

    /// See [`AuthorityHarnessDriver::audit_count`].
    pub async fn audit_count(&self) -> ServiceResult<u64> {
        self.driver.audit_count().await
    }

    /// See [`AuthorityHarnessDriver::seed_uninitialized_bot`].
    pub async fn seed_uninitialized_bot(&self, bot_id: &str) -> ServiceResult<()> {
        self.driver.seed_uninitialized_bot(bot_id).await
    }

    /// See [`AuthorityHarnessDriver::break_owner_edge`].
    pub async fn break_owner_edge(&self, bot_id: &str) -> ServiceResult<()> {
        self.driver.break_owner_edge(bot_id).await
    }
}

/// Rule 25 shared conformance suite for `BotAuthorityRepoPort`.
///
/// Run from each production crate's `tests/conformance_bot_authority.rs`
/// driver with its harness. Every assertion here must hold identically
/// for the SQL and the in-memory implementations.
pub async fn bot_authority_repo_port_contract_tests(h: &AuthorityRepoHarness) {
    // Task 3 performs no audited authority writes: the harness seeding is
    // test-only, so any audit growth would mean a production write path
    // leaked in (spec §5.4 audits record mutations only).
    assert_eq!(h.audit_count().await.unwrap(), 0, "no audit rows before seeding");

    // -- Plan RED snapshot (brief Task 3 Step 1), verbatim semantics ------
    h.seed_owned("bot-a", "a").await.unwrap();
    assert_eq!(
        h.repo.role("a", "bot-a").await.unwrap(),
        Some(BotAccessRelation::Owner)
    );
    assert_eq!(h.repo.role("b", "bot-a").await.unwrap(), None);
    let roles = h
        .repo
        .roles_for(&[("a".into(), "bot-a".into()), ("b".into(), "bot-a".into())])
        .await
        .unwrap();
    assert_eq!(roles, vec![Some(BotAccessRelation::Owner), None]);

    // -- Ownership snapshots are strict and typed --------------------------
    assert_eq!(
        h.repo.ownership("bot-a").await.unwrap(),
        OwnershipState {
            owner_user_id: "a".to_string(),
            ownership_version: 1,
        }
    );

    // -- Batch positions stay aligned across bots, users and duplicates ----
    h.seed_owned("bot-b", "a").await.unwrap();
    let roles = h
        .repo
        .roles_for(&[
            ("a".into(), "bot-a".into()),
            ("b".into(), "bot-a".into()),
            ("a".into(), "bot-b".into()),
            ("a".into(), "bot-b".into()),
            ("b".into(), "bot-b".into()),
        ])
        .await
        .unwrap();
    assert_eq!(
        roles,
        vec![
            Some(BotAccessRelation::Owner),
            None,
            Some(BotAccessRelation::Owner),
            Some(BotAccessRelation::Owner),
            None,
        ],
        "roles_for results must be position-aligned with the input pairs"
    );

    // Unknown subjects and unknown bots stay positional `None`s; absence is
    // a deny, never an error and never an allow.
    let roles = h
        .repo
        .roles_for(&[("nobody".into(), "bot-a".into()), ("a".into(), "ghost".into())])
        .await
        .unwrap();
    assert_eq!(roles, vec![None, None]);

    // Empty batch: empty result (an empty answer for a non-empty batch can
    // never be allowed — see the error cases below).
    assert!(h.repo.roles_for(&[]).await.unwrap().is_empty());

    // Real Human materialization is visible as a subject that still holds
    // no role (friend/human existence never grants authority — spec §4.5).
    h.seed_human("c").await.unwrap();
    assert_eq!(h.repo.role("c", "bot-a").await.unwrap(), None);

    // -- Uninitialized ownership is the OwnershipNotInitialized branch -----
    h.seed_uninitialized_bot("bot-zero").await.unwrap();
    match h.repo.ownership("bot-zero").await {
        Err(ServiceError::Authority(AuthorityError::OwnershipNotInitialized { bot_id, .. })) => {
            assert_eq!(bot_id, "bot-zero");
        }
        other => panic!("expected OwnershipNotInitialized, got {:?}", other.err()),
    }

    // -- Initialized-but-ownerless is CorruptAuthority, never a snapshot --
    h.seed_owned("bot-broken", "brk").await.unwrap();
    h.break_owner_edge("bot-broken").await.unwrap();
    match h.repo.ownership("bot-broken").await {
        Err(ServiceError::Authority(AuthorityError::CorruptAuthority { bot_id, .. })) => {
            assert_eq!(bot_id, "bot-broken");
        }
        other => panic!("expected CorruptAuthority, got {:?}", other.err()),
    }
    // The corrupted Bot's authority snapshot does not degrade into an
    // ordinary permission answer either: querying it for any subject keeps
    // failing closed.
    assert!(matches!(
        h.repo.ownership("bot-broken").await,
        Err(ServiceError::Authority(AuthorityError::CorruptAuthority { .. }))
    ));

    // -- Failed writes never fabricate readable state or audit rows -------
    h.fail_next_write();
    assert!(
        h.seed_owned("bot-fail", "f").await.is_err(),
        "an armed write failure must surface, never be swallowed"
    );
    assert_eq!(
        h.repo.role("f", "bot-fail").await.unwrap(),
        None,
        "the failed seed must leave no readable authority behind"
    );
    assert_eq!(
        h.repo.role("a", "bot-a").await.unwrap(),
        Some(BotAccessRelation::Owner),
        "previously seeded truth stays intact after a failed write"
    );
    assert_eq!(
        h.audit_count().await.unwrap(),
        0,
        "a failed write must not append audit rows"
    );
}

/// Rule 25 shared conformance suite for `BotAuthorityCoreService`.
///
/// Extends the repo contract with the Core's fail-closed validation rules:
/// before answering any role question the Core must have validated the
/// involved Bot (existence / ownership_version / the unique owner). The
/// suite is self-contained; the driver only supplies the assembled
/// production Core over its harness repo.
pub async fn bot_authority_core_service_contract_tests(
    core: &dyn BotAuthorityCoreService,
    h: &AuthorityRepoHarness,
) {
    // Seeded fixture state is visible through the Core.
    h.seed_owned("core-bot-a", "ca").await.unwrap();
    assert_eq!(
        core.ownership("core-bot-a").await.unwrap(),
        OwnershipState {
            owner_user_id: "ca".to_string(),
            ownership_version: 1,
        }
    );
    assert_eq!(
        core.role("ca", "core-bot-a").await.unwrap(),
        Some(BotAccessRelation::Owner)
    );
    assert_eq!(core.role("cb", "core-bot-a").await.unwrap(), None);

    // Batch semantics hold at Core level too: position-aligned, and invalid
    // bots inside the batch fail the WHOLE batch closed (spec §12.4: corrupt
    // authority data denies, never returns partial roles).
    let roles = core
        .roles_for(&[
            ("ca".into(), "core-bot-a".into()),
            ("cb".into(), "core-bot-a".into()),
        ])
        .await
        .unwrap();
    assert_eq!(roles, vec![Some(BotAccessRelation::Owner), None]);

    // Uninitialized Bot: the Core surfaces OwnershipNotInitialized for the
    // single read, never a silent None-deny.
    h.seed_uninitialized_bot("core-bot-zero").await.unwrap();
    assert!(matches!(
        core.role("ca", "core-bot-zero").await,
        Err(ServiceError::Authority(AuthorityError::OwnershipNotInitialized { .. }))
    ));
    assert!(matches!(
        core.roles_for(&[("ca".into(), "core-bot-zero".into())]).await,
        Err(ServiceError::Authority(AuthorityError::OwnershipNotInitialized { .. }))
    ));

    // Initialized-but-ownerless Bot: CorruptAuthority.
    h.seed_owned("core-bot-broken", "cbrk").await.unwrap();
    h.break_owner_edge("core-bot-broken").await.unwrap();
    assert!(matches!(
        core.role("ca", "core-bot-broken").await,
        Err(ServiceError::Authority(AuthorityError::CorruptAuthority { .. }))
    ));
    assert!(matches!(
        core.ownership("core-bot-broken").await,
        Err(ServiceError::Authority(AuthorityError::CorruptAuthority { .. }))
    ));

    // A missing (never-seeded) Bot fails the whole batch too.
    assert!(matches!(
        core.roles_for(&[
            ("ca".into(), "core-bot-a".into()),
            ("ca".into(), "core-ghost".into()),
        ])
        .await,
        Err(ServiceError::BotNotFound(_))
    ));
}