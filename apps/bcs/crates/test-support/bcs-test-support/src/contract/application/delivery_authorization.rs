//! `DeliveryAuthorizationService` conformance harness (Rule 25, plan Task 15,
//! spec §14).
//!
//! The same shared suite runs against every driver (the real Memory stores
//! with the production authority Core, and the application-level map double),
//! so decision drift between implementations is impossible: an ordering or
//! batching rule asserted here holds for every implementation.
//!
//! Contract baseline (spec §14, binding for the suite):
//! - Binding eligibility is judged FIRST; a revoked/corrupted binding is
//!   [`DeliveryAuthorizationDecision::InvalidateBinding`] even when the
//!   current frame would have been `SkipMessage` anyway — the scope filter
//!   never masks a revocation.
//! - A valid binding with an out-of-audience frame is `SkipMessage` for THIS
//!   frame only; the next public/own-directed frame still delivers.
//! - The same User's different views resolve INDEPENDENTLY; contexts with
//!   different env/resource/scope never share a per-user merged boolean.
//! - One batch of at most
//!   [`DELIVERY_AUTHORIZATION_MAX_BATCH`] contexts reads the authority pairs
//!   ONCE (Task 3's `roles_for`) and each distinct resource once — the suites
//!   assert the real query counts, never per-context repetition.
//! - Read failures are `Err`, never `SkipMessage`/empty success. Size above
//!   the bound is `Err` — the adapter (Task 16) splits bigger events; the
//!   service never merges them.
//!
//! Driver fixture requirements (binding for every
//! [`DeliveryAuthorizationDriver`] implementation):
//! - One Group `G` and one Session `S` in `G` whose participants are exactly:
//!   Bot `X` and Bot `Y` (Bot actors, `MessageViewScope::Full`), the manager
//!   user's own human actor `human_U` (`MessageViewScope::Full`), and the
//!   participant user's human actor `human_P`
//!   (`MessageViewScope::Participant`).
//! - The manager user `U` holds a revocable non-team manager authority over
//!   BOTH `X` and `Y`; [`DeliveryAuthorizationDriver::revoke_view_x_authority`]
//!   removes ONLY `U` over `X` through the driver's real revoke lane and
//!   leaves `Y` and every other fact untouched.
//! - `P` has no bot-view authority: `P`'s eligible view is its own human
//!   participant row.
//! - [`DeliveryAuthorizationDriver::remove_participant_user`] removes
//!   `human_P` from `S` through the driver's real session-membership lane
//!   (a true membership-loss fact, not a flag the service reads).

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_domain::{
    AuditActor, BotAccessRelation, ManagerMutation, ManagerMutationResult, MessageAudience,
    MessageVisibilityDomain, OwnershipState, TransferAction,
};
use bcs_service_api::application::v1::delivery_authorization::{
    DeliveryAuthorizationContext, DeliveryAuthorizationContextBuilder,
    DeliveryAuthorizationDecision, DeliveryAuthorizationService, DeliveryResourceKind,
    DELIVERY_AUTHORIZATION_MAX_BATCH,
};
use bcs_service_api::core::{BotAuthorityCoreService, GroupCoreService};
use bcs_service_api::port::repo::{SessionRepoPort, bot_authority::human_actor_id};
use bcs_service_api::types::error::ServiceResult;
use bcs_service_api::core::DmActorSpec;
use bcs_service_api::port::repo::NewSessionParams;
use bcs_service_api::types::{
    BotManagerList, Group, GroupMessage, GroupStatus, Participant, ParticipantMode, ServiceError,
    Session, ServiceSpec, Workspace,
};
use bcs_service_api::types::ownership_transfer::{
    CommittedTransferOutcome, CreateOwnershipTransfer, CreateTransferResult,
    ListOwnershipTransfers, OwnershipTransfer, OwnershipTransferPage,
};
use bcs_service_api::types::team_manager_sync::{TeamManagerSync, TeamSyncReceipt};

// ── harness ─────────────────────────────────────────────────────────────

/// Cross-driver delivery-authorization harness: the production service under
/// conformance plus the driver-implemented fixture/observation surface.
pub struct DeliveryAuthorizationHarness {
    /// The production `Arc<dyn DeliveryAuthorizationService>` under
    /// conformance. Tests must reach the implementation through the service
    /// trait, never through driver internals.
    pub service: Arc<dyn DeliveryAuthorizationService>,
    /// Test-only fixture/observation levers implemented per driver.
    pub driver: Arc<dyn DeliveryAuthorizationDriver>,
}

/// Driver-implemented test surface for the delivery-authorization contract.
/// Every method exists only to build or observe fixture state; none of them
/// is a production claim entry.
#[async_trait]
pub trait DeliveryAuthorizationDriver: Send + Sync {
    /// The trusted environment string fixture contexts carry.
    fn env(&self) -> &'static str;
    fn group_id(&self) -> &'static str;
    fn session_id(&self) -> &'static str;
    /// `U` — the manager user viewing through `X` and `Y`.
    fn manager_user(&self) -> &'static str;
    /// `X` — the view Bot whose authority the suite revokes.
    fn view_bot_x(&self) -> &'static str;
    /// `Y` — the view Bot whose authority stays valid.
    fn view_bot_y(&self) -> &'static str;
    /// `P` — the participant-scope Human whose own directed frames deliver.
    fn participant_user(&self) -> &'static str;
    /// An unrelated actor id for other-directed audience frames.
    fn other_actor(&self) -> &'static str;

    /// How many `roles_for` authority batch reads the service issued so far.
    fn authority_batch_calls(&self) -> usize;
    /// How many Group snapshots the service loaded so far.
    fn group_load_calls(&self) -> usize;
    /// How many Session snapshots the service loaded so far.
    fn session_load_calls(&self) -> usize;

    /// Arm a one-shot failure: the NEXT resource read of this kind surfaces
    /// `Err` (storage read failure), proving fail-closed `Err` propagation.
    fn arm_resource_read_failure(&self, kind: DeliveryResourceKind);

    /// Arm a one-shot failure of the NEXT authority batch read (`roles_for`).
    fn arm_authority_batch_failure(&self);

    /// Remove ONLY the manager user's authority over `X` through the
    /// driver's REAL revoke lane.
    async fn revoke_view_x_authority(&self);

    /// Remove `human_P` from the session through the driver's REAL
    /// session-membership lane.
    async fn remove_participant_user(&self);
}

// ── the shared suite ─────────────────────────────────────────────────────

fn self_view_context(
    driver: &dyn DeliveryAuthorizationDriver,
    user_id: &str,
    kind: DeliveryResourceKind,
    message: (MessageVisibilityDomain, Option<MessageAudience>),
) -> DeliveryAuthorizationContext {
    DeliveryAuthorizationContextBuilder::self_view(
        driver.env(),
        user_id,
        kind,
        match kind {
            DeliveryResourceKind::Group => driver.group_id(),
            DeliveryResourceKind::Session => driver.session_id(),
        },
    )
    .message(message.0, message.1)
    .build()
}

fn bot_view_context(
    driver: &dyn DeliveryAuthorizationDriver,
    user_id: &str,
    view_bot_id: &str,
    kind: DeliveryResourceKind,
    message: (MessageVisibilityDomain, Option<MessageAudience>),
) -> DeliveryAuthorizationContext {
    DeliveryAuthorizationContextBuilder::bot_view(
        driver.env(),
        user_id,
        kind,
        match kind {
            DeliveryResourceKind::Group => driver.group_id(),
            DeliveryResourceKind::Session => driver.session_id(),
        },
        view_bot_id,
    )
    .message(message.0, message.1)
    .build()
}

fn state_machine(message: MessageAudience) -> (MessageVisibilityDomain, Option<MessageAudience>) {
    (MessageVisibilityDomain::StateMachine, Some(message))
}

fn public() -> (MessageVisibilityDomain, Option<MessageAudience>) {
    state_machine(MessageAudience::Public)
}

fn full_only() -> (MessageVisibilityDomain, Option<MessageAudience>) {
    state_machine(MessageAudience::FullOnly)
}

fn other_directed(
    driver: &dyn DeliveryAuthorizationDriver,
) -> (MessageVisibilityDomain, Option<MessageAudience>) {
    state_machine(MessageAudience::Directed {
        actor_ids: vec![driver.other_actor().to_string()],
    })
}

fn own_directed(
    driver: &dyn DeliveryAuthorizationDriver,
) -> (MessageVisibilityDomain, Option<MessageAudience>) {
    state_machine(MessageAudience::Directed {
        actor_ids: vec![human_actor_id(driver.participant_user())],
    })
}

fn chat() -> (MessageVisibilityDomain, Option<MessageAudience>) {
    (MessageVisibilityDomain::Chat, None)
}

/// The full continuous-authorization evidence suite (plan Task 15 Step 1/4).
/// Every driver mounts this from its implementation via
/// `tests/conformance_delivery_authorization.rs` (R25 naming).
#[allow(clippy::too_many_lines)]
pub async fn delivery_authorization_service_contract_tests(
    harness: &DeliveryAuthorizationHarness,
) {
    let driver = harness.driver.as_ref();
    let service = harness.service.as_ref();
    use DeliveryAuthorizationDecision::{Deliver, InvalidateBinding, SkipMessage};

    // ── Baseline sanity: every fixture view is a valid binding and the
    //    public frame is visible to it. Catches broken fixtures early.
    let x_baseline = bot_view_context(driver, driver.manager_user(), driver.view_bot_x(),
        DeliveryResourceKind::Session, public());
    let y_baseline = bot_view_context(driver, driver.manager_user(), driver.view_bot_y(),
        DeliveryResourceKind::Session, public());
    let decisions = service
        .authorize_batch(vec![x_baseline, y_baseline])
        .await
        .expect("baseline fixture views authorize");
    assert_eq!(decisions, vec![Deliver, Deliver], "fixture must start valid");

    // ── RED block 1 (spec §14.6, AC14/AC16): same User, same Session, two
    //    views; ONLY X's authority is revoked. The X view is invalidated,
    //    the independent Y view still delivers, and the batch reads the
    //    scoped authority evidence with ONE `roles_for` call.
    driver.revoke_view_x_authority().await;
    let x_context = bot_view_context(driver, driver.manager_user(), driver.view_bot_x(),
        DeliveryResourceKind::Session, public());
    let y_context = bot_view_context(driver, driver.manager_user(), driver.view_bot_y(),
        DeliveryResourceKind::Session, public());
    let authority_calls_before = driver.authority_batch_calls();
    let session_loads_before = driver.session_load_calls();
    let decisions = service
        .authorize_batch(vec![x_context, y_context])
        .await
        .expect("position-aligned decisions for two views");
    assert_eq!(
        decisions,
        vec![InvalidateBinding, Deliver],
        "revoking only X must invalidate the X view and keep the Y view"
    );
    let recording_authority_batch_calls =
        driver.authority_batch_calls() - authority_calls_before;
    assert_eq!(
        recording_authority_batch_calls, 1,
        "one bounded batch makes exactly one authority batch read"
    );
    assert_eq!(
        driver.session_load_calls() - session_loads_before,
        1,
        "both views share one session snapshot; loads are per distinct resource, never per context"
    );

    // ── RED block 2: a valid Participant-scope member sees public frames but
    //    FullOnly / other-Directed frames are SkipMessage for THIS frame
    //    only; the binding stays and the next public frame still delivers
    //    (spec §14 table, AC16). Self views need no authority pair read.
    let full_only_context = self_view_context(driver, driver.participant_user(),
        DeliveryResourceKind::Session, full_only());
    let other_directed_context = self_view_context(driver, driver.participant_user(),
        DeliveryResourceKind::Session, other_directed(driver));
    let public_context = self_view_context(driver, driver.participant_user(),
        DeliveryResourceKind::Session, public());
    let authority_calls_before = driver.authority_batch_calls();
    let session_loads_before = driver.session_load_calls();
    let decisions = service
        .authorize_batch(vec![full_only_context, other_directed_context, public_context])
        .await
        .expect("position-aligned decisions for the participant fixture");
    assert_eq!(
        decisions,
        vec![SkipMessage, SkipMessage, Deliver],
        "FullOnly and other-Directed filter this frame only; public still delivers"
    );
    assert_eq!(
        driver.authority_batch_calls() - authority_calls_before,
        0,
        "a human self view reads no bot-authority pairs"
    );
    assert_eq!(
        driver.session_load_calls() - session_loads_before,
        1,
        "three contexts over one session still load the session exactly once"
    );
    // Own-directed frames DO reach the participant (AC16: 本人 Directed 可收到).
    let own_directed_context = self_view_context(driver, driver.participant_user(),
        DeliveryResourceKind::Session, own_directed(driver));
    let decisions = service
        .authorize_batch(vec![own_directed_context])
        .await
        .expect("own-directed participant frame authorizes");
    assert_eq!(decisions, vec![Deliver], "the participant's own Directed frame delivers");

    // ── Binding before scope, explicitly: the revoked X view meets a Chat
    //    frame (visible to any member) and still reports InvalidateBinding —
    //    and the same User's own independent participation still delivers;
    //    no view auto-switch ever rescues the revoked bot view (spec §14.7).
    let x_chat_context = bot_view_context(driver, driver.manager_user(), driver.view_bot_x(),
        DeliveryResourceKind::Session, chat());
    let u_self_context = self_view_context(driver, driver.manager_user(),
        DeliveryResourceKind::Session, public());
    let decisions = service
        .authorize_batch(vec![x_chat_context, u_self_context])
        .await
        .expect("binding eligibility composes with independent participation");
    assert_eq!(
        decisions,
        vec![InvalidateBinding, Deliver],
        "the explicit revoked view must never auto-switch to the user's self view"
    );

    // ── Scope independence within one batch and one resource: the same
    //    FullOnly frame is Deliver for a Full-scope member and SkipMessage
    //    for the Participant-scope member — the scope fact is part of the
    //    decision key, never a per-user boolean.
    let u_full_only_context = self_view_context(driver, driver.manager_user(),
        DeliveryResourceKind::Session, full_only());
    let p_full_only_context = self_view_context(driver, driver.participant_user(),
        DeliveryResourceKind::Session, full_only());
    let decisions = service
        .authorize_batch(vec![u_full_only_context, p_full_only_context])
        .await
        .expect("scope-keyed decisions for one resource");
    assert_eq!(decisions, vec![Deliver, SkipMessage], "scope changes the decision key");

    // ── Resource-kind independence: Group contexts reuse the same authority
    //    facts and are still bound to the Group's own membership; the revoked
    //    X view stays invalid on the Group resource too, the participant
    //    scope still filters FullOnly. Group loads are real, distinct calls.
    let x_group_public_context = bot_view_context(driver, driver.manager_user(),
        driver.view_bot_x(), DeliveryResourceKind::Group, public());
    let u_group_public_context = self_view_context(driver, driver.manager_user(),
        DeliveryResourceKind::Group, public());
    let group_loads_before = driver.group_load_calls();
    let session_loads_before = driver.session_load_calls();
    let decisions = service
        .authorize_batch(vec![x_group_public_context, u_group_public_context])
        .await
        .expect("group-keyed decisions");
    assert_eq!(
        decisions,
        vec![InvalidateBinding, Deliver],
        "a revoke is a fact about the view, not about one resource"
    );
    assert_eq!(driver.group_load_calls() - group_loads_before, 1);
    assert_eq!(driver.session_load_calls() - session_loads_before, 0);

    // ── Resource-id independence: a context against a missing session is a
    //    binding fact about THIS binding only — a missing resource means the
    //    membership is gone; the neighboring context stays untouched.
    let ghost_session_context = DeliveryAuthorizationContextBuilder::self_view(
        driver.env(),
        driver.manager_user(),
        DeliveryResourceKind::Session,
        "ghost-delivery-session",
    )
    .message(public().0, public().1)
    .build();
    let live_session_context = self_view_context(driver, driver.manager_user(),
        DeliveryResourceKind::Session, public());
    let decisions = service
        .authorize_batch(vec![ghost_session_context, live_session_context])
        .await
        .expect("resource-keyed decisions");
    assert_eq!(decisions, vec![InvalidateBinding, Deliver]);

    // ── Env independence: full identity boundaries only ever share read
    //    evidence when the COMPLETE context matches; two contexts of
    //    different envs decide independently per position.
    let env_a_x_context = DeliveryAuthorizationContextBuilder::bot_view(
        "delivery-env-a",
        driver.manager_user(),
        DeliveryResourceKind::Session,
        driver.session_id(),
        driver.view_bot_x(),
    )
    .message(chat().0, chat().1)
    .build();
    let env_b_y_context = DeliveryAuthorizationContextBuilder::bot_view(
        "delivery-env-b",
        driver.manager_user(),
        DeliveryResourceKind::Session,
        driver.session_id(),
        driver.view_bot_y(),
    )
    .message(chat().0, chat().1)
    .build();
    let authority_calls_before = driver.authority_batch_calls();
    let decisions = service
        .authorize_batch(vec![env_a_x_context, env_b_y_context])
        .await
        .expect("positions stay aligned across different envs");
    assert_eq!(decisions, vec![InvalidateBinding, Deliver]);
    assert_eq!(driver.authority_batch_calls() - authority_calls_before, 1);

    // ── 128 at the bound is legal: a full mixed batch (both kinds, revoked
    //    and valid views, several audience shapes) stays position-aligned
    //    and reads each distinct resource exactly once and the authority
    //    pairs in exactly ONE `roles_for` call — the bounded-batch evidence
    //    contract of spec §14.5/§17.2.
    let mut contexts = Vec::with_capacity(DELIVERY_AUTHORIZATION_MAX_BATCH);
    let mut expected = Vec::with_capacity(DELIVERY_AUTHORIZATION_MAX_BATCH);
    for index in 0..DELIVERY_AUTHORIZATION_MAX_BATCH {
        let kind = if index % 2 == 0 {
            DeliveryResourceKind::Session
        } else {
            DeliveryResourceKind::Group
        };
        let (context, decision) = match index % 4 {
            0 => (
                bot_view_context(driver, driver.manager_user(), driver.view_bot_x(), kind, public()),
                InvalidateBinding,
            ),
            1 => (
                bot_view_context(driver, driver.manager_user(), driver.view_bot_y(), kind, public()),
                Deliver,
            ),
            2 => (self_view_context(driver, driver.manager_user(), kind, full_only()), Deliver),
            _ => (self_view_context(driver, driver.participant_user(), kind, public()), Deliver),
        };
        contexts.push(context);
        expected.push(decision);
    }
    let group_loads_before = driver.group_load_calls();
    let session_loads_before = driver.session_load_calls();
    let authority_calls_before = driver.authority_batch_calls();
    let decisions = service
        .authorize_batch(contexts)
        .await
        .expect("128 contexts authorize at the bound");
    assert_eq!(decisions, expected, "128 results stay position-aligned");
    assert_eq!(
        driver.group_load_calls() - group_loads_before,
        1,
        "one distinct Group loaded once for the whole batch"
    );
    assert_eq!(
        driver.session_load_calls() - session_loads_before,
        1,
        "one distinct Session loaded once for the whole batch"
    );
    assert_eq!(
        driver.authority_batch_calls() - authority_calls_before,
        1,
        "the whole batch reads its authority pairs through one roles_for call"
    );

    // ── 129 must be split by the adapter (Task 16): the service rejects an
    //    oversized batch fail-closed BEFORE any read — counters stay put
    //    and the remaining targets are never treated as authorized.
    let mut oversize = Vec::with_capacity(DELIVERY_AUTHORIZATION_MAX_BATCH + 1);
    for _ in 0..=DELIVERY_AUTHORIZATION_MAX_BATCH {
        oversize.push(self_view_context(driver, driver.manager_user(),
            DeliveryResourceKind::Session, public()));
    }
    let group_loads_before = driver.group_load_calls();
    let session_loads_before = driver.session_load_calls();
    let authority_calls_before = driver.authority_batch_calls();
    let error = service
        .authorize_batch(oversize)
        .await
        .expect_err("oversized batches are the adapter's split responsibility");
    assert!(
        matches!(error, bcs_service_api::application::v1::ApplicationError::InvalidInput { .. }),
        "oversize is an invalid-input contraction, got: {error:?}"
    );
    assert_eq!(driver.group_load_calls(), group_loads_before);
    assert_eq!(driver.session_load_calls(), session_loads_before);
    assert_eq!(driver.authority_batch_calls(), authority_calls_before);

    // ── Duplicated complete contexts share read evidence but each
    //    connection position is still judged independently (spec §14.5).
    let x_again_context = bot_view_context(driver, driver.manager_user(), driver.view_bot_x(),
        DeliveryResourceKind::Session, public());
    let x_twice_context = bot_view_context(driver, driver.manager_user(), driver.view_bot_x(),
        DeliveryResourceKind::Session, public());
    let authority_calls_before = driver.authority_batch_calls();
    let session_loads_before = driver.session_load_calls();
    let decisions = service
        .authorize_batch(vec![x_again_context, x_twice_context])
        .await
        .expect("duplicate contexts stay position-aligned");
    assert_eq!(decisions, vec![InvalidateBinding, InvalidateBinding]);
    assert_eq!(driver.authority_batch_calls() - authority_calls_before, 1);
    assert_eq!(driver.session_load_calls() - session_loads_before, 1);

    // ── Read failure is Err, never SkipMessage or empty success (spec
    //    §14.6/§17.2 fail-closed): a session snapshot failure invalidates
    //    the whole batch decision, never silently skips frames.
    driver.arm_resource_read_failure(DeliveryResourceKind::Session);
    let valid_context = self_view_context(driver, driver.manager_user(),
        DeliveryResourceKind::Session, public());
    let result = service
        .authorize_batch(vec![valid_context])
        .await;
    assert!(
        result.is_err(),
        "a storage read failure must surface as Err, got {:?}",
        result.map(|decisions| decisions.len())
    );

    // ── The authority batch read failing is Err too (DB/decode failures
    //    are never flattened into denies or empty success).
    driver.arm_authority_batch_failure();
    let bot_view = bot_view_context(driver, driver.manager_user(), driver.view_bot_y(),
        DeliveryResourceKind::Session, public());
    let result = service
        .authorize_batch(vec![bot_view])
        .await;
    assert!(result.is_err(), "an authority read failure must surface as Err");

    // ── True membership loss is InvalidateBinding even for a frame that is
    //    invisible anyway (binding eligibility precedes the scope filter and
    //    is never masked by it): P's human actor is removed from the session
    //    through the REAL membership lane, then even an invisible FullOnly
    //    frame reports the invalid binding — and the manager's untouched
    //    binding in the same batch still delivers.
    driver.remove_participant_user().await;
    let p_ghost_full_only_context = self_view_context(driver, driver.participant_user(),
        DeliveryResourceKind::Session, full_only());
    let u_public_context = self_view_context(driver, driver.manager_user(),
        DeliveryResourceKind::Session, public());
    let decisions = service
        .authorize_batch(vec![p_ghost_full_only_context, u_public_context])
        .await
        .expect("membership-loss decisions");
    assert_eq!(
        decisions,
        vec![InvalidateBinding, Deliver],
        "a removed member's invisible frame must report the invalid binding, not SkipMessage"
    );
    // The invalid binding does not recover for a public frame either — the
    // binding fact is per-context and deterministic (no TTL games).
    let p_ghost_public_context = self_view_context(driver, driver.participant_user(),
        DeliveryResourceKind::Session, public());
    let decisions = service
        .authorize_batch(vec![p_ghost_public_context])
        .await
        .expect("removed membership stays invalidated");
    assert_eq!(decisions, vec![InvalidateBinding]);
}

// ── shared observation wrappers ─────────────────────────────────────────
//
// Counting and one-shot failure levers for the collaborators the production
// `DeliveryAuthorizationServiceImpl` reads. They delegate to the driver's
// real stores; ONLY `roles_for` / `try_get` are intercepted (exactly the
// evidence reads the contract budgets). The service under conformance must
// be assembled over these wrappers by each driver.

/// Counts/arms `BotAuthorityCoreService::roles_for` — the ONE authority
/// evidence read per bounded batch.
pub struct CountingAuthorityCore {
    inner: Arc<dyn BotAuthorityCoreService>,
    roles_for_calls: AtomicUsize,
    fail_next_roles_for: AtomicBool,
}

impl CountingAuthorityCore {
    pub fn new(inner: Arc<dyn BotAuthorityCoreService>) -> Self {
        Self {
            inner,
            roles_for_calls: AtomicUsize::new(0),
            fail_next_roles_for: AtomicBool::new(false),
        }
    }

    pub fn roles_for_calls(&self) -> usize {
        self.roles_for_calls.load(Ordering::SeqCst)
    }

    pub fn arm_authority_batch_failure(&self) {
        self.fail_next_roles_for.store(true, Ordering::SeqCst);
    }
}

#[async_trait]
impl BotAuthorityCoreService for CountingAuthorityCore {
    async fn ownership(&self, bot_id: &str) -> ServiceResult<OwnershipState> {
        self.inner.ownership(bot_id).await
    }

    async fn role(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> ServiceResult<Option<BotAccessRelation>> {
        self.inner.role(user_id, bot_id).await
    }

    async fn roles_for(
        &self,
        pairs: &[(String, String)],
    ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
        self.roles_for_calls.fetch_add(1, Ordering::SeqCst);
        if self.fail_next_roles_for.swap(false, Ordering::SeqCst) {
            return Err(ServiceError::InternalError(
                "test-injected authority batch read failure".to_string(),
            ));
        }
        self.inner.roles_for(pairs).await
    }

    async fn mutate_manager(
        &self,
        actor: AuditActor,
        bot_id: &str,
        mutation: ManagerMutation,
    ) -> ServiceResult<ManagerMutationResult> {
        self.inner.mutate_manager(actor, bot_id, mutation).await
    }

    async fn list_managers(
        &self,
        bot_id: &str,
        offset: u64,
        limit: u64,
    ) -> ServiceResult<BotManagerList> {
        self.inner.list_managers(bot_id, offset, limit).await
    }

    async fn sync_team(&self, command: TeamManagerSync) -> ServiceResult<TeamSyncReceipt> {
        self.inner.sync_team(command).await
    }

    async fn create_transfer(
        &self,
        command: CreateOwnershipTransfer,
    ) -> ServiceResult<CreateTransferResult> {
        self.inner.create_transfer(command).await
    }

    async fn decide_transfer(
        &self,
        actor_user_id: &str,
        transfer_id: &str,
        action: TransferAction,
    ) -> ServiceResult<CommittedTransferOutcome> {
        self.inner
            .decide_transfer(actor_user_id, transfer_id, action)
            .await
    }

    async fn get_transfer(
        &self,
        viewer_user_id: &str,
        transfer_id: &str,
    ) -> ServiceResult<OwnershipTransfer> {
        self.inner.get_transfer(viewer_user_id, transfer_id).await
    }

    async fn list_transfers(
        &self,
        query: ListOwnershipTransfers,
    ) -> ServiceResult<OwnershipTransferPage> {
        self.inner.list_transfers(query).await
    }
}

/// Counts/arms `GroupCoreService::try_get` — the one Group snapshot read per
/// distinct Group per batch.
pub struct CountingGroupCore {
    inner: Arc<dyn GroupCoreService>,
    try_get_calls: AtomicUsize,
    fail_next: Mutex<bool>,
}

impl CountingGroupCore {
    pub fn new(inner: Arc<dyn GroupCoreService>) -> Self {
        Self {
            inner,
            try_get_calls: AtomicUsize::new(0),
            fail_next: Mutex::new(false),
        }
    }

    /// The number of Group snapshot reads the service issued so far.
    pub fn try_get_calls(&self) -> usize {
        self.try_get_calls.load(Ordering::SeqCst)
    }

    /// Arm the one-shot `try_get` failure (still visible in `try_get_calls`).
    pub fn arm_resource_read_failure(&self) {
        *self.fail_next.lock().unwrap() = true;
    }
}

#[async_trait]
impl GroupCoreService for CountingGroupCore {
    async fn upsert(&self, group: Group) -> ServiceResult<()> {
        self.inner.upsert(group).await
    }

    async fn get(&self, id: &str) -> Option<Group> {
        self.inner.get(id).await
    }

    async fn try_get(&self, id: &str) -> ServiceResult<Option<Group>> {
        self.try_get_calls.fetch_add(1, Ordering::SeqCst);
        if *self.fail_next.lock().unwrap() {
            *self.fail_next.lock().unwrap() = false;
            return Err(ServiceError::InternalError(
                "test-injected group snapshot read failure".to_string(),
            ));
        }
        self.inner.try_get(id).await
    }

    async fn add_message(&self, id: &str, message: GroupMessage) -> ServiceResult<()> {
        self.inner.add_message(id, message).await
    }

    async fn add_participant(&self, id: &str, participant: Participant) -> ServiceResult<()> {
        self.inner.add_participant(id, participant).await
    }

    async fn remove_participant(&self, group_id: &str, bot_uuid: &str) -> ServiceResult<()> {
        self.inner.remove_participant(group_id, bot_uuid).await
    }

    async fn update_participant_mode(
        &self,
        group_id: &str,
        actor_id: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<()> {
        self.inner
            .update_participant_mode(group_id, actor_id, mode)
            .await
    }

    async fn update_workspace(
        &self,
        id: &str,
        workspace: Workspace,
        operation: bcs_service_api::types::BotOperationContext,
    ) -> ServiceResult<()> {
        self.inner.update_workspace(id, workspace, operation).await
    }

    async fn update_label(&self, id: &str, label: Option<String>) -> ServiceResult<()> {
        self.inner.update_label(id, label).await
    }

    async fn update_status(&self, id: &str, status: GroupStatus) -> ServiceResult<()> {
        self.inner.update_status(id, status).await
    }

    async fn update_service_spec(
        &self,
        id: &str,
        service_spec: Option<ServiceSpec>,
    ) -> ServiceResult<()> {
        self.inner.update_service_spec(id, service_spec).await
    }

    async fn terminate(&self, id: &str, caller_bot_id: &str) -> ServiceResult<Group> {
        self.inner.terminate(id, caller_bot_id).await
    }

    async fn delete(&self, id: &str) -> ServiceResult<Option<Group>> {
        self.inner.delete(id).await
    }

    async fn list(&self) -> Vec<Group> {
        self.inner.list().await
    }

    async fn list_paginated(&self, offset: u64, limit: u64) -> Vec<Group> {
        self.inner.list_paginated(offset, limit).await
    }

    async fn find_by_participant(&self, bot_uuid: &str) -> Vec<Group> {
        self.inner.find_by_participant(bot_uuid).await
    }

    async fn count(&self) -> u64 {
        self.inner.count().await
    }

    async fn count_by_participant(&self, bot_uuid: &str) -> u64 {
        self.inner.count_by_participant(bot_uuid).await
    }

    async fn find_by_participant_paginated(
        &self,
        bot_uuid: &str,
        offset: u64,
        limit: u64,
    ) -> Vec<Group> {
        self.inner
            .find_by_participant_paginated(bot_uuid, offset, limit)
            .await
    }

    async fn message_count(&self, id: &str) -> ServiceResult<usize> {
        self.inner.message_count(id).await
    }

    async fn increment_message_count(&self, id: &str) -> ServiceResult<()> {
        self.inner.increment_message_count(id).await
    }

    async fn reset_message_count(&self, id: &str) -> ServiceResult<()> {
        self.inner.reset_message_count(id).await
    }

    async fn create_or_reuse_actor_dm_group(
        &self,
        id: &str,
        actor_a: DmActorSpec,
        actor_b: DmActorSpec,
        legacy_driver_bot: &str,
        originator_actor_id: &str,
        label: Option<String>,
        context: Option<String>,
    ) -> ServiceResult<(Group, bool)> {
        self.inner
            .create_or_reuse_actor_dm_group(
                id,
                actor_a,
                actor_b,
                legacy_driver_bot,
                originator_actor_id,
                label,
                context,
            )
            .await
    }
}

/// Counts/arms `SessionRepoPort::try_get` — the one Session snapshot read per
/// distinct Session per batch.
pub struct CountingSessionRepo {
    inner: Arc<dyn SessionRepoPort>,
    try_get_calls: AtomicUsize,
    fail_next: Mutex<bool>,
}

impl CountingSessionRepo {
    pub fn new(inner: Arc<dyn SessionRepoPort>) -> Self {
        Self {
            inner,
            try_get_calls: AtomicUsize::new(0),
            fail_next: Mutex::new(false),
        }
    }

    pub fn try_get_calls(&self) -> usize {
        self.try_get_calls.load(Ordering::SeqCst)
    }

    pub fn arm_resource_read_failure(&self) {
        *self.fail_next.lock().unwrap() = true;
    }
}

#[async_trait]
impl SessionRepoPort for CountingSessionRepo {
    async fn create(&self, group_id: &str, params: NewSessionParams) -> ServiceResult<Session> {
        self.inner.create(group_id, params).await
    }

    async fn get(&self, session_id: &str) -> Option<Session> {
        self.inner.get(session_id).await
    }

    async fn try_get(&self, session_id: &str) -> ServiceResult<Option<Session>> {
        self.try_get_calls.fetch_add(1, Ordering::SeqCst);
        if *self.fail_next.lock().unwrap() {
            *self.fail_next.lock().unwrap() = false;
            return Err(ServiceError::InternalError(
                "test-injected session snapshot read failure".to_string(),
            ));
        }
        self.inner.try_get(session_id).await
    }

    async fn belongs_to_group(&self, session_id: &str, group_id: &str) -> bool {
        self.inner.belongs_to_group(session_id, group_id).await
    }

    async fn list_by_group(
        &self,
        group_id: &str,
        status: Option<bcs_service_api::types::SessionStatus>,
        offset: u64,
        limit: u64,
        title_contains: Option<&str>,
        participant_id: Option<&str>,
    ) -> Vec<Session> {
        self.inner
            .list_by_group(group_id, status, offset, limit, title_contains, participant_id)
            .await
    }

    async fn latest_running(&self, group_id: &str) -> Option<Session> {
        self.inner.latest_running(group_id).await
    }

    async fn count_running_service(&self, group_id: &str) -> u64 {
        self.inner.count_running_service(group_id).await
    }

    async fn list_running_service(&self, offset: u64, limit: u64) -> Vec<Session> {
        self.inner.list_running_service(offset, limit).await
    }

    async fn complete_if_running(
        &self,
        session_id: &str,
        output: Option<serde_json::Value>,
        error: Option<String>,
    ) -> ServiceResult<Option<Session>> {
        self.inner.complete_if_running(session_id, output, error).await
    }

    async fn reactivate(
        &self,
        session_id: &str,
        new_input: Option<serde_json::Value>,
    ) -> ServiceResult<Session> {
        self.inner.reactivate(session_id, new_input).await
    }

    async fn add_participant(
        &self,
        session_id: &str,
        participant: Participant,
    ) -> ServiceResult<Session> {
        self.inner.add_participant(session_id, participant).await
    }

    async fn remove_participant(&self, session_id: &str, bot_uuid: &str) -> ServiceResult<Session> {
        self.inner.remove_participant(session_id, bot_uuid).await
    }

    async fn update_participant_mode(
        &self,
        session_id: &str,
        bot_uuid: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<Session> {
        self.inner
            .update_participant_mode(session_id, bot_uuid, mode)
            .await
    }

    async fn update_callback_status(&self, session_id: &str, status: &str) -> ServiceResult<()> {
        self.inner.update_callback_status(session_id, status).await
    }

    async fn update_title(
        &self,
        session_id: &str,
        title: Option<String>,
    ) -> ServiceResult<Session> {
        self.inner.update_title(session_id, title).await
    }

    async fn list_group_ids_by_session_participant(&self, bot_uuid: &str) -> Vec<String> {
        self.inner.list_group_ids_by_session_participant(bot_uuid).await
    }

    async fn delete(&self, session_id: &str) -> ServiceResult<bool> {
        self.inner.delete(session_id).await
    }
}
