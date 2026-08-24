//! Repository contract harnesses.
//!
//! Concrete repository implementations call these functions from
//! `tests/conformance_*.rs`.

pub mod edge_grant;
pub mod permission_profile;
pub mod permission_request;

pub use edge_grant::run_edge_grant_repo_contract;
pub use permission_profile::run_permission_profile_repo_contract;
pub use permission_request::run_permission_request_repo_contract;

use bcs_domain::{MessageOwnerFilter, MessageQuery, NewMessage, SenderType};
use bcs_service_api::ServiceError;
use bcs_service_api::port::repo::{
    AppendEventRecord, ClaimEventDeliveries, ClaimFanoutTargets, CompleteEventDeliveryAttempt,
    CreateEventReplayTarget, CreateEventSubscriptionRecord, CreateOrganizationRecord,
    EventDeliveryAttemptRecordResult, EventDeliveryRecord, EventRepoError, EventRepoPort,
    EventRetentionRequest, ListEventDeliveryRecords, ListOrganizationMembersPageQuery,
    ListOrganizationMembersQuery, MaterializeFanoutTarget, MessageRepoPort, OrganizationRepoPort,
    RenewEventDeliveryLease, ReplaceEventSubscriptionRevision, SkipDeadLetteredEventDelivery,
    UpsertOrganizationMemberRecord,
};
use bcs_service_api::types::{EventDeliveryStatus, EventSubscriptionStatus};
use bcs_service_api::{
    BindingChannel, BotCapabilities, BotControlPlaneRepoPort, BotRepoPort, DefaultDelivery,
    FriendRepoPort, FriendRequest, FriendRequestDirection, FriendRequestRepoPort,
    FriendRequestStatus, Group, GroupChatProposal, GroupKind, GroupMutableFieldsPatch,
    GroupRepoPort, GroupStatus, NewSessionParams, Participant, ParticipantMode, ParticipantRole,
    ProposalCoreService, RelationEdge, RelationRepoPort, RoutingMode, RoutingPolicy, ServiceSpec,
    Session, SessionKind, SessionRepoPort, SessionStatus, Skill,
};
use sha2::{Digest, Sha256};

pub async fn event_repo_port_contract_tests<T: EventRepoPort + ?Sized>(
    repo: &T,
    subscription: CreateEventSubscriptionRecord,
    append: AppendEventRecord,
) {
    let original_subscription = subscription.clone();
    let base_revision = subscription.revision.clone();
    let actor = subscription.subscription.created_by.clone();
    let env = subscription.subscription.env.clone();
    let subscription_id = subscription.subscription.subscription_id.clone();
    let event_id = append.event.event_id.clone();

    let mut quota_a = original_subscription.clone();
    quota_a.subscription.subscription_id = format!("{subscription_id}-quota-a");
    quota_a.revision.subscription_id = quota_a.subscription.subscription_id.clone();
    quota_a.subscription.scope.id = format!("{}-quota", quota_a.subscription.scope.id);
    quota_a.scope_limit = 1;
    let mut quota_b = quota_a.clone();
    quota_b.subscription.subscription_id = format!("{subscription_id}-quota-b");
    quota_b.revision.subscription_id = quota_b.subscription.subscription_id.clone();
    let (quota_a_result, quota_b_result) = tokio::join!(
        repo.create_subscription(quota_a),
        repo.create_subscription(quota_b)
    );
    assert_eq!(
        usize::from(quota_a_result.is_ok()) + usize::from(quota_b_result.is_ok()),
        1,
        "scope limit check and Subscription insert must be atomic"
    );
    assert_eq!(
        usize::from(matches!(quota_a_result, Err(EventRepoError::LimitReached(_))))
            + usize::from(matches!(quota_b_result, Err(EventRepoError::LimitReached(_)))),
        1,
        "the losing concurrent create must return the resource-limit error"
    );

    let created = repo
        .create_subscription(subscription)
        .await
        .expect("create immutable Event Subscription revision");
    assert_eq!(created.subscription_id, subscription_id);
    assert_eq!(created.current_revision, 1);

    let fetched = repo
        .get_subscription(&subscription_id, &env)
        .await
        .expect("get Event Subscription")
        .expect("created Event Subscription exists");
    assert_eq!(fetched.0.subscription_id, subscription_id);
    assert_eq!(fetched.1.revision, created.current_revision);

    let first = repo
        .append_event(append.clone())
        .await
        .expect("append Event and target snapshot atomically");
    assert_eq!(first.event.envelope.event_id, event_id);
    assert!(first.event.envelope.stream.sequence > 0);
    assert_eq!(
        first.fanout_target_ids.len(),
        1,
        "the active matching revision is snapshotted with the Event"
    );
    assert!(!first.deduplicated);

    let duplicate = repo
        .append_event(append.clone())
        .await
        .expect("producer idempotency returns the canonical Event");
    assert_eq!(
        duplicate.event.envelope.event_id,
        first.event.envelope.event_id
    );
    assert_eq!(
        duplicate.event.envelope.stream.sequence,
        first.event.envelope.stream.sequence
    );
    assert!(duplicate.deduplicated);

    let fetched_event = repo
        .get_event(&event_id, &env)
        .await
        .expect("get canonical Event")
        .expect("appended Event exists");
    assert_eq!(fetched_event.envelope, first.event.envelope);

    let mut invalid_cause = append.clone();
    invalid_cause.event.event_id = format!("{event_id}-invalid-cause");
    invalid_cause.event.producer_key =
        format!("{}:invalid-cause", invalid_cause.event.producer_key);
    invalid_cause.event.causation_event_id = Some(format!("{event_id}-future"));
    assert!(matches!(
        repo.append_event(invalid_cause).await,
        Err(EventRepoError::CausationViolation(_))
    ));

    let mut second = append.clone();
    second.event.event_id = format!("{event_id}-second");
    second.event.producer_key = format!("{}:second", second.event.producer_key);
    second.event.causation_event_id = Some(event_id.clone());
    let second = repo
        .append_event(second)
        .await
        .expect("append causally-linked Event");
    assert_eq!(
        second.event.envelope.stream.sequence,
        first.event.envelope.stream.sequence + 1,
        "failed append must not consume a visible stream sequence"
    );
    assert_eq!(second.fanout_target_ids.len(), 1);

    let mut revision_two = base_revision.clone();
    revision_two.revision = 2;
    revision_two.event_filters = vec!["contract.unmatched".to_string()];
    revision_two.activated_at_ms += 1;
    let updated = repo
        .replace_subscription_revision(ReplaceEventSubscriptionRevision {
            subscription_id: subscription_id.clone(),
            expected_revision: 1,
            name: "updated contract subscription".to_string(),
            status: EventSubscriptionStatus::Active,
            revision: revision_two.clone(),
            cancel_retired_pending_deliveries: false,
            actor: actor.clone(),
            reason: Some("contract filter update".to_string()),
            updated_at_ms: original_subscription.subscription.updated_at_ms + 1,
            env: env.clone(),
        })
        .await
        .expect("replace immutable Event Subscription revision");
    assert_eq!(updated.current_revision, 2);

    let mut unmatched = append.clone();
    unmatched.event.event_id = format!("{event_id}-unmatched");
    unmatched.event.producer_key = format!("{}:unmatched", unmatched.event.producer_key);
    let unmatched = repo
        .append_event(unmatched)
        .await
        .expect("append Event after filter replacement");
    assert!(unmatched.fanout_target_ids.is_empty());

    let stale = repo
        .replace_subscription_revision(ReplaceEventSubscriptionRevision {
            subscription_id: subscription_id.clone(),
            expected_revision: 1,
            name: "stale contract update".to_string(),
            status: EventSubscriptionStatus::Active,
            revision: revision_two,
            cancel_retired_pending_deliveries: false,
            actor: actor.clone(),
            reason: None,
            updated_at_ms: original_subscription.subscription.updated_at_ms + 2,
            env: env.clone(),
        })
        .await;
    assert!(matches!(stale, Err(EventRepoError::Conflict(_))));

    let mut revision_three = base_revision.clone();
    revision_three.revision = 3;
    revision_three.activated_at_ms += 2;
    repo.replace_subscription_revision(ReplaceEventSubscriptionRevision {
        subscription_id: subscription_id.clone(),
        expected_revision: 2,
        name: "disabled contract subscription".to_string(),
        status: EventSubscriptionStatus::Disabled,
        revision: revision_three,
        cancel_retired_pending_deliveries: true,
        actor: actor.clone(),
        reason: Some("contract disable".to_string()),
        updated_at_ms: original_subscription.subscription.updated_at_ms + 3,
        env: env.clone(),
    })
    .await
    .expect("disable Subscription at the scope linearization point");

    let mut while_disabled = append.clone();
    while_disabled.event.event_id = format!("{event_id}-disabled");
    while_disabled.event.producer_key = format!("{}:disabled", while_disabled.event.producer_key);
    let while_disabled = repo
        .append_event(while_disabled)
        .await
        .expect("append while Subscription is disabled");
    assert!(while_disabled.fanout_target_ids.is_empty());

    let mut revision_four = base_revision;
    revision_four.revision = 4;
    revision_four.activated_at_ms += 3;
    revision_four.event_filters.push("session.*".to_string());
    repo.replace_subscription_revision(ReplaceEventSubscriptionRevision {
        subscription_id: subscription_id.clone(),
        expected_revision: 3,
        name: "enabled contract subscription".to_string(),
        status: EventSubscriptionStatus::Active,
        revision: revision_four,
        cancel_retired_pending_deliveries: false,
        actor,
        reason: Some("contract enable".to_string()),
        updated_at_ms: original_subscription.subscription.updated_at_ms + 4,
        env: env.clone(),
    })
    .await
    .expect("re-enable Subscription at the scope linearization point");

    let mut after_enable = append.clone();
    after_enable.event.event_id = format!("{event_id}-enabled");
    after_enable.event.producer_key = format!("{}:enabled", after_enable.event.producer_key);
    let after_enable = repo
        .append_event(after_enable)
        .await
        .expect("append after Subscription is enabled");
    assert_eq!(after_enable.fanout_target_ids.len(), 1);

    let mut descendant = append.clone();
    descendant.event.event_id = format!("{event_id}-descendant");
    descendant.event.event_type = "session.created".to_string();
    descendant.event.producer_key = format!("{}:descendant", descendant.event.producer_key);
    descendant.event.subject.subject_type = "session".to_string();
    descendant.event.subject.id = "contract-session".to_string();
    descendant.event.scope.session_id = Some("contract-session".to_string());
    descendant.event.stream_key = "session:contract-session".to_string();
    let descendant = repo
        .append_event(descendant)
        .await
        .expect("group Subscription matches descendant session scope");
    assert_eq!(descendant.fanout_target_ids.len(), 1);
    assert_eq!(descendant.event.envelope.stream.sequence, 1);

    assert!(
        repo.get_subscription(&subscription_id, "other-env")
            .await
            .expect("environment-isolated Subscription read")
            .is_none()
    );

    let mut duplicate_subscription_id = original_subscription;
    duplicate_subscription_id.subscription.env = "other-env".to_string();
    assert!(matches!(
        repo.create_subscription(duplicate_subscription_id).await,
        Err(EventRepoError::Conflict(_))
    ));

    let mut duplicate_event_id = append;
    duplicate_event_id.env = "other-env".to_string();
    duplicate_event_id.event.producer_key =
        format!("{}:other-env", duplicate_event_id.event.producer_key);
    assert!(matches!(
        repo.append_event(duplicate_event_id).await,
        Err(EventRepoError::Conflict(_))
    ));
}

/// Shared fanout/Delivery state-machine contract. Store implementations must
/// call this harness from a `conformance_*.rs` integration test.
pub async fn event_delivery_repo_port_contract_tests<T: EventRepoPort + ?Sized>(
    repo: &T,
    subscription: CreateEventSubscriptionRecord,
    first_append: AppendEventRecord,
) {
    const BASE: u64 = 1_755_561_610_000;
    let subscription_template = subscription.clone();
    let env = subscription.subscription.env.clone();
    let subscription_id = subscription.subscription.subscription_id.clone();
    repo.create_subscription(subscription)
        .await
        .expect("create Delivery contract Subscription");

    let first = repo
        .append_event(first_append.clone())
        .await
        .expect("append first strict-lane Event");
    let mut second_append = first_append.clone();
    second_append.event.event_id = format!("{}-second", first_append.event.event_id);
    second_append.event.producer_key = format!("{}:second", first_append.event.producer_key);
    let second = repo
        .append_event(second_append)
        .await
        .expect("append second strict-lane Event");
    assert_eq!(second.event.envelope.stream.sequence, 2);

    let targets = repo
        .claim_fanout_targets(ClaimFanoutTargets {
            worker_id: "fanout-a".to_string(),
            now_ms: BASE,
            lease_until_ms: BASE + 1_000,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("claim fanout targets");
    assert_eq!(targets.len(), 2);
    assert!(
        repo.claim_fanout_targets(ClaimFanoutTargets {
            worker_id: "fanout-b".to_string(),
            now_ms: BASE,
            lease_until_ms: BASE + 1_000,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("second fanout claimant")
        .is_empty()
    );

    let first_target = targets
        .iter()
        .find(|target| target.event_id == first.event.envelope.event_id)
        .expect("first target");
    let first_materialization = materialization(
        first_target,
        &first.event,
        "fanout-a",
        "delivery-first",
        BASE + 10,
    );
    let mut stale_materialization = first_materialization.clone();
    stale_materialization.expected_lease_owner = "fanout-stale".to_string();
    let stale_result = repo.materialize_fanout_target(stale_materialization).await;
    assert!(
        matches!(stale_result, Err(EventRepoError::LeaseLost(_))),
        "stale fanout lease must be fenced, got {stale_result:?}"
    );
    let first_delivery = repo
        .materialize_fanout_target(first_materialization.clone())
        .await
        .expect("materialize first target");
    assert_eq!(
        repo.materialize_fanout_target(first_materialization)
            .await
            .expect("target materialization is idempotent")
            .delivery_id,
        first_delivery.delivery_id
    );

    let second_target = targets
        .iter()
        .find(|target| target.event_id == second.event.envelope.event_id)
        .expect("second target");
    let second_delivery = repo
        .materialize_fanout_target(materialization(
            second_target,
            &second.event,
            "fanout-a",
            "delivery-second",
            BASE + 11,
        ))
        .await
        .expect("materialize second target");

    let claimed_first = repo
        .claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-a".to_string(),
            now_ms: BASE + 20,
            lease_until_ms: BASE + 120,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("claim strict lane head");
    assert_eq!(claimed_first.len(), 1);
    assert_eq!(claimed_first[0].delivery_id, first_delivery.delivery_id);
    assert!(
        repo.claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-b".to_string(),
            now_ms: BASE + 20,
            lease_until_ms: BASE + 120,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("active lease and strict lane are exclusive")
        .is_empty()
    );

    let lease_owner = claimed_first[0]
        .lease_owner
        .clone()
        .expect("claimed Delivery lease owner");
    let renewed = repo
        .renew_delivery_lease(RenewEventDeliveryLease {
            delivery_id: first_delivery.delivery_id.clone(),
            expected_lease_owner: lease_owner.clone(),
            attempt_no: claimed_first[0].attempt_count,
            now_ms: BASE + 100,
            lease_until_ms: BASE + 220,
            env: env.clone(),
        })
        .await
        .expect("renew active Delivery lease");
    assert_eq!(renewed.lease_until_ms, Some(BASE + 220));
    assert!(matches!(
        repo.renew_delivery_lease(RenewEventDeliveryLease {
            delivery_id: first_delivery.delivery_id.clone(),
            expected_lease_owner: "stale-owner".to_string(),
            attempt_no: claimed_first[0].attempt_count,
            now_ms: BASE + 101,
            lease_until_ms: BASE + 221,
            env: env.clone(),
        })
        .await,
        Err(EventRepoError::LeaseLost(_))
    ));
    assert!(
        repo.claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-after-original-expiry".to_string(),
            now_ms: BASE + 121,
            lease_until_ms: BASE + 221,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("renewed lease remains exclusive")
        .is_empty()
    );

    repo.complete_delivery_attempt(completion(
        &claimed_first[0],
        "delivery-a",
        BASE + 20,
        BASE + 130,
        EventDeliveryAttemptRecordResult::Retryable,
        EventDeliveryStatus::RetryWait,
        Some(BASE + 150),
    ))
    .await
    .expect("persist retryable Attempt");
    assert!(
        repo.claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-before-due".to_string(),
            now_ms: BASE + 140,
            lease_until_ms: BASE + 240,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("retry wait blocks its lane")
        .is_empty()
    );

    let retry = repo
        .claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-retry".to_string(),
            now_ms: BASE + 150,
            lease_until_ms: BASE + 250,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("claim due retry");
    assert_eq!(retry.len(), 1);
    assert_eq!(retry[0].attempt_count, 2);
    repo.complete_delivery_attempt(completion(
        &retry[0],
        "delivery-retry",
        BASE + 150,
        BASE + 160,
        EventDeliveryAttemptRecordResult::Success,
        EventDeliveryStatus::Succeeded,
        None,
    ))
    .await
    .expect("complete retry successfully");
    let (stored_first, first_attempts) = repo
        .get_delivery(&first_delivery.delivery_id, &env)
        .await
        .expect("read completed retry Delivery")
        .expect("completed retry Delivery exists");
    assert_eq!(stored_first.status, EventDeliveryStatus::Succeeded);
    assert_eq!(stored_first.payload_bytes, first_delivery.payload_bytes);
    assert_eq!(first_attempts.len(), 2);
    assert_eq!(first_attempts[0].attempt_no, 1);
    assert_eq!(first_attempts[1].attempt_no, 2);
    assert!(
        repo.list_deliveries(ListEventDeliveryRecords {
            subscription_id: Some(subscription_id.clone()),
            event_id: Some(first.event.envelope.event_id.clone()),
            status: Some(EventDeliveryStatus::Succeeded),
            after_delivery_id: None,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("list persisted Delivery")
        .iter()
        .any(|delivery| delivery.delivery_id == first_delivery.delivery_id)
    );

    let crashed = repo
        .claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-crashed".to_string(),
            now_ms: BASE + 61,
            lease_until_ms: BASE + 80,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("claim second Delivery");
    assert_eq!(crashed.len(), 1);
    assert_eq!(crashed[0].delivery_id, second_delivery.delivery_id);
    let recovered = repo
        .claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-recovered".to_string(),
            now_ms: BASE + 81,
            lease_until_ms: BASE + 181,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("recover expired Delivery lease");
    assert_eq!(recovered.len(), 1);
    assert_eq!(recovered[0].attempt_count, 2);
    assert!(matches!(
        repo.complete_delivery_attempt(completion(
            &crashed[0],
            "delivery-crashed",
            BASE + 61,
            BASE + 82,
            EventDeliveryAttemptRecordResult::Success,
            EventDeliveryStatus::Succeeded,
            None,
        ))
        .await,
        Err(EventRepoError::LeaseLost(_))
    ));
    let dead_letter = repo
        .complete_delivery_attempt(completion(
            &recovered[0],
            "delivery-recovered",
            BASE + 81,
            BASE + 90,
            EventDeliveryAttemptRecordResult::Terminal,
            EventDeliveryStatus::DeadLettered,
            None,
        ))
        .await
        .expect("terminal Attempt enters DLQ");

    let mut strict_revision_two = subscription_template.revision.clone();
    strict_revision_two.revision = 2;
    strict_revision_two.activated_at_ms += 1;
    repo.replace_subscription_revision(ReplaceEventSubscriptionRevision {
        subscription_id: subscription_id.clone(),
        expected_revision: 1,
        name: "strict cross-revision subscription".to_string(),
        status: EventSubscriptionStatus::Active,
        revision: strict_revision_two,
        cancel_retired_pending_deliveries: false,
        actor: subscription_template.subscription.created_by.clone(),
        reason: Some("contract timeout-only revision".to_string()),
        updated_at_ms: BASE + 90,
        env: env.clone(),
    })
    .await
    .expect("activate a new revision without cancelling the old lane blocker");

    let mut third_append = first_append.clone();
    third_append.event.event_id = format!("{}-third", first_append.event.event_id);
    third_append.event.producer_key = format!("{}:third", first_append.event.producer_key);
    let third = repo
        .append_event(third_append)
        .await
        .expect("append Delivery behind DLQ blocker");
    let third_target = repo
        .claim_fanout_targets(ClaimFanoutTargets {
            worker_id: "fanout-third".to_string(),
            now_ms: BASE + 91,
            lease_until_ms: BASE + 191,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("claim third target")
        .into_iter()
        .find(|target| target.event_id == third.event.envelope.event_id)
        .expect("third target claimed");
    let third_delivery = repo
        .materialize_fanout_target(materialization(
            &third_target,
            &third.event,
            "fanout-third",
            "delivery-third",
            BASE + 92,
        ))
        .await
        .expect("materialize third Delivery");
    assert!(
        repo.claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-blocked".to_string(),
            now_ms: BASE + 93,
            lease_until_ms: BASE + 193,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("DLQ blocks ordinary successors")
        .is_empty()
    );

    let replay_command = CreateEventReplayTarget {
        original_delivery_id: dead_letter.delivery_id.clone(),
        subscription_id: subscription_id.clone(),
        subscription_revision: 2,
        replay_request_id: "replay-request-1".to_string(),
        target_id: "target-replay-1".to_string(),
        actor: first.event.envelope.actor.clone().expect("fixture actor"),
        reason: Some("contract replay".to_string()),
        created_at_ms: BASE + 94,
        env: env.clone(),
    };
    let replay_target = repo
        .create_replay_target(replay_command.clone())
        .await
        .expect("create replay target");
    assert_eq!(
        repo.create_replay_target(replay_command)
            .await
            .expect("replay request is idempotent")
            .target_id,
        replay_target.target_id
    );
    let replay_target = repo
        .claim_fanout_targets(ClaimFanoutTargets {
            worker_id: "fanout-replay".to_string(),
            now_ms: BASE + 95,
            lease_until_ms: BASE + 195,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("claim replay target")
        .into_iter()
        .find(|target| target.target_id == replay_target.target_id)
        .expect("replay target claimed");
    let replay_delivery = repo
        .materialize_fanout_target(materialization(
            &replay_target,
            &second.event,
            "fanout-replay",
            "delivery-replay-1",
            BASE + 96,
        ))
        .await
        .expect("materialize replay replacement");
    let claimed_replay = repo
        .claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-replay".to_string(),
            now_ms: BASE + 97,
            lease_until_ms: BASE + 197,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("replacement may cross its own blocker");
    assert_eq!(claimed_replay.len(), 1);
    assert_eq!(claimed_replay[0].delivery_id, replay_delivery.delivery_id);
    repo.complete_delivery_attempt(completion(
        &claimed_replay[0],
        "delivery-replay",
        BASE + 97,
        BASE + 98,
        EventDeliveryAttemptRecordResult::Success,
        EventDeliveryStatus::Succeeded,
        None,
    ))
    .await
    .expect("successful replay resolves original DLQ");
    let resolved = repo
        .get_delivery(&dead_letter.delivery_id, &env)
        .await
        .expect("read resolved original")
        .expect("original Delivery exists")
        .0;
    assert_eq!(
        resolved.resolved_by_delivery_id.as_deref(),
        Some(replay_delivery.delivery_id.as_str())
    );

    let unblocked = repo
        .claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-third".to_string(),
            now_ms: BASE + 99,
            lease_until_ms: BASE + 199,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("successful replay unblocks strict lane");
    assert_eq!(unblocked.len(), 1);
    assert_eq!(unblocked[0].delivery_id, third_delivery.delivery_id);
    let third_dead_letter = repo
        .complete_delivery_attempt(completion(
            &unblocked[0],
            "delivery-third",
            BASE + 99,
            BASE + 100,
            EventDeliveryAttemptRecordResult::Terminal,
            EventDeliveryStatus::DeadLettered,
            None,
        ))
        .await
        .expect("third Delivery enters DLQ");

    let mut fourth_append = first_append.clone();
    fourth_append.event.event_id = format!("{}-fourth", fourth_append.event.event_id);
    fourth_append.event.producer_key = format!("{}:fourth", fourth_append.event.producer_key);
    let fourth = repo
        .append_event(fourth_append)
        .await
        .expect("append fourth Event");
    let fourth_target = repo
        .claim_fanout_targets(ClaimFanoutTargets {
            worker_id: "fanout-fourth".to_string(),
            now_ms: BASE + 101,
            lease_until_ms: BASE + 201,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("claim fourth target")
        .into_iter()
        .find(|target| target.event_id == fourth.event.envelope.event_id)
        .expect("fourth target claimed");
    let fourth_delivery = repo
        .materialize_fanout_target(materialization(
            &fourth_target,
            &fourth.event,
            "fanout-fourth",
            "delivery-fourth",
            BASE + 102,
        ))
        .await
        .expect("materialize fourth Delivery");
    assert!(
        repo.claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-fourth-blocked".to_string(),
            now_ms: BASE + 103,
            lease_until_ms: BASE + 203,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("second DLQ blocks lane")
        .is_empty()
    );
    repo.skip_dead_lettered_delivery(SkipDeadLetteredEventDelivery {
        delivery_id: third_dead_letter.delivery_id,
        actor: first.event.envelope.actor.clone().expect("fixture actor"),
        reason: "contract acknowledges data loss".to_string(),
        skipped_at_ms: BASE + 104,
        env: env.clone(),
    })
    .await
    .expect("explicit skip resolves DLQ");
    let after_skip = repo
        .claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-fourth".to_string(),
            now_ms: BASE + 105,
            lease_until_ms: BASE + 205,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("skip unblocks strict lane");
    assert_eq!(after_skip.len(), 1);
    assert_eq!(after_skip[0].delivery_id, fourth_delivery.delivery_id);
    let retained = repo
        .purge_expired(EventRetentionRequest {
            now_ms: 2_000_000_000_001,
            event_limit: 100,
            audit_limit: 100,
            env: env.clone(),
        })
        .await
        .expect("retention scan");
    assert!(retained.events_deleted < 4);
    assert!(
        repo.get_event(&fourth.event.envelope.event_id, &env)
            .await
            .expect("read retained Event")
            .is_some()
    );
    repo.complete_delivery_attempt(completion(
        &after_skip[0],
        "delivery-fourth",
        BASE + 105,
        BASE + 106,
        EventDeliveryAttemptRecordResult::Success,
        EventDeliveryStatus::Succeeded,
        None,
    ))
    .await
    .expect("settle fourth Delivery");

    let causal_group_id = format!("{}-causal", subscription_template.subscription.scope.id);
    let mut causal_subscription = subscription_template.clone();
    causal_subscription.subscription.subscription_id = format!("{subscription_id}-causal");
    causal_subscription.subscription.scope.id = causal_group_id.clone();
    causal_subscription.revision.subscription_id =
        causal_subscription.subscription.subscription_id.clone();
    causal_subscription.revision.event_filters =
        vec!["task.*".to_string(), "message.*".to_string()];
    repo.create_subscription(causal_subscription.clone())
        .await
        .expect("create cross-stream causation Subscription");

    let mut cause_append = first_append.clone();
    cause_append.event.event_id = format!("{}-cause", first_append.event.event_id);
    cause_append.event.producer_key = format!("{}:cause", first_append.event.producer_key);
    cause_append.event.event_type = "task.started".to_string();
    cause_append.event.subject.subject_type = "task".to_string();
    cause_append.event.subject.id = "task-causal".to_string();
    cause_append.event.scope.group_id = Some(causal_group_id.clone());
    cause_append.event.scope.task_id = Some("task-causal".to_string());
    cause_append.event.stream_key = "task:task-causal".to_string();
    let cause = repo
        .append_event(cause_append)
        .await
        .expect("append cross-stream cause");
    let mut effect_append = first_append.clone();
    effect_append.event.event_id = format!("{}-effect", first_append.event.event_id);
    effect_append.event.producer_key = format!("{}:effect", first_append.event.producer_key);
    effect_append.event.event_type = "message.created".to_string();
    effect_append.event.subject.subject_type = "message".to_string();
    effect_append.event.subject.id = "message-causal".to_string();
    effect_append.event.scope.group_id = Some(causal_group_id);
    effect_append.event.scope.session_id = Some("session-causal".to_string());
    effect_append.event.scope.task_id = Some("task-causal".to_string());
    effect_append.event.stream_key = "session:session-causal".to_string();
    effect_append.event.causation_event_id = Some(cause.event.envelope.event_id.clone());
    let effect = repo
        .append_event(effect_append)
        .await
        .expect("append cross-stream effect");
    let causal_targets = repo
        .claim_fanout_targets(ClaimFanoutTargets {
            worker_id: "fanout-causal".to_string(),
            now_ms: BASE + 300,
            lease_until_ms: BASE + 400,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("claim cross-stream targets");
    assert_eq!(causal_targets.len(), 2);
    for (target, event, delivery_id) in [
        (
            causal_targets
                .iter()
                .find(|target| target.event_id == cause.event.envelope.event_id)
                .expect("cause target"),
            &cause.event,
            "delivery-cause",
        ),
        (
            causal_targets
                .iter()
                .find(|target| target.event_id == effect.event.envelope.event_id)
                .expect("effect target"),
            &effect.event,
            "delivery-effect",
        ),
    ] {
        repo.materialize_fanout_target(materialization(
            target,
            event,
            "fanout-causal",
            delivery_id,
            BASE + 301,
        ))
        .await
        .expect("materialize cross-stream target");
    }
    let cause_claim = repo
        .claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-cause".to_string(),
            now_ms: BASE + 302,
            lease_until_ms: BASE + 402,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("claim cross-stream cause only");
    assert_eq!(cause_claim.len(), 1);
    assert_eq!(cause_claim[0].event_id, cause.event.envelope.event_id);
    repo.complete_delivery_attempt(completion(
        &cause_claim[0],
        "delivery-cause",
        BASE + 302,
        BASE + 303,
        EventDeliveryAttemptRecordResult::Success,
        EventDeliveryStatus::Succeeded,
        None,
    ))
    .await
    .expect("settle cross-stream cause");
    let effect_claim = repo
        .claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-effect".to_string(),
            now_ms: BASE + 304,
            lease_until_ms: BASE + 404,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("cause success releases cross-stream effect");
    assert_eq!(effect_claim.len(), 1);
    assert_eq!(effect_claim[0].event_id, effect.event.envelope.event_id);
    let effect_dead_letter = repo
        .complete_delivery_attempt(completion(
            &effect_claim[0],
            "delivery-effect",
            BASE + 304,
            BASE + 305,
            EventDeliveryAttemptRecordResult::Terminal,
            EventDeliveryStatus::DeadLettered,
            None,
        ))
        .await
        .expect("cross-stream effect enters DLQ");
    let mut causal_revision_two = causal_subscription.revision.clone();
    causal_revision_two.revision = 2;
    causal_revision_two.activated_at_ms += 1;
    repo.replace_subscription_revision(ReplaceEventSubscriptionRevision {
        subscription_id: causal_subscription.subscription.subscription_id.clone(),
        expected_revision: 1,
        name: "cross-stream replay revision".to_string(),
        status: EventSubscriptionStatus::Active,
        revision: causal_revision_two,
        cancel_retired_pending_deliveries: false,
        actor: causal_subscription.subscription.created_by.clone(),
        reason: Some("rotate replay revision".to_string()),
        updated_at_ms: BASE + 306,
        env: env.clone(),
    })
    .await
    .expect("activate replay revision");
    let causal_replay = repo
        .create_replay_target(CreateEventReplayTarget {
            original_delivery_id: effect_dead_letter.delivery_id,
            subscription_id: causal_subscription.subscription.subscription_id.clone(),
            subscription_revision: 2,
            replay_request_id: "replay-causal-revision-2".to_string(),
            target_id: "target-replay-causal-revision-2".to_string(),
            actor: causal_subscription.subscription.created_by.clone(),
            reason: Some("contract causal replay".to_string()),
            created_at_ms: BASE + 307,
            env: env.clone(),
        })
        .await
        .expect("replay effect into new revision");
    let revision_two_targets = repo
        .claim_fanout_targets(ClaimFanoutTargets {
            worker_id: "fanout-causal-replay".to_string(),
            now_ms: BASE + 308,
            lease_until_ms: BASE + 408,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("claim rebuilt causal prerequisite and replay");
    assert_eq!(revision_two_targets.len(), 2);
    let revision_two_cause = revision_two_targets
        .iter()
        .find(|target| target.event_id == cause.event.envelope.event_id)
        .expect("revision-two causal prerequisite");
    let revision_two_effect = revision_two_targets
        .iter()
        .find(|target| target.target_id == causal_replay.target_id)
        .expect("revision-two replay target");
    assert_eq!(
        revision_two_effect.depends_on_target_id.as_deref(),
        Some(revision_two_cause.target_id.as_str()),
        "effect replay must depend on the causal target from its new revision"
    );
    repo.materialize_fanout_target(materialization(
        revision_two_cause,
        &cause.event,
        "fanout-causal-replay",
        "delivery-cause-revision-2",
        BASE + 309,
    ))
    .await
    .expect("materialize rebuilt causal prerequisite");
    repo.materialize_fanout_target(materialization(
        revision_two_effect,
        &effect.event,
        "fanout-causal-replay",
        "delivery-effect-revision-2",
        BASE + 309,
    ))
    .await
    .expect("materialize effect replay in new revision");
    let revision_two_cause_claim = repo
        .claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-cause-revision-2".to_string(),
            now_ms: BASE + 310,
            lease_until_ms: BASE + 410,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("new revision still enforces causal prerequisite");
    assert_eq!(revision_two_cause_claim.len(), 1);
    assert_eq!(
        revision_two_cause_claim[0].event_id,
        cause.event.envelope.event_id
    );
    repo.complete_delivery_attempt(completion(
        &revision_two_cause_claim[0],
        "delivery-cause-revision-2",
        BASE + 310,
        BASE + 311,
        EventDeliveryAttemptRecordResult::Success,
        EventDeliveryStatus::Succeeded,
        None,
    ))
    .await
    .expect("settle rebuilt causal prerequisite");
    let revision_two_effect_claim = repo
        .claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-effect-revision-2".to_string(),
            now_ms: BASE + 312,
            lease_until_ms: BASE + 412,
            limit: 10,
            env: env.clone(),
        })
        .await
        .expect("causal prerequisite releases new-revision replay");
    assert_eq!(revision_two_effect_claim.len(), 1);
    assert_eq!(
        revision_two_effect_claim[0].event_id,
        effect.event.envelope.event_id
    );
    repo.complete_delivery_attempt(completion(
        &revision_two_effect_claim[0],
        "delivery-effect-revision-2",
        BASE + 312,
        BASE + 313,
        EventDeliveryAttemptRecordResult::Success,
        EventDeliveryStatus::Succeeded,
        None,
    ))
    .await
    .expect("settle new-revision effect replay");
}

fn materialization(
    target: &bcs_service_api::port::repo::EventFanoutTargetRecord,
    event: &bcs_service_api::port::repo::EventRecord,
    _worker_id: &str,
    delivery_id: &str,
    materialized_at_ms: u64,
) -> MaterializeFanoutTarget {
    let payload_bytes = serde_json::to_vec(&event.envelope).expect("serialize Event payload");
    let payload_sha256 = format!("{:x}", Sha256::digest(&payload_bytes));
    MaterializeFanoutTarget {
        target_id: target.target_id.clone(),
        expected_lease_owner: target
            .lease_owner
            .clone()
            .expect("claimed target lease owner"),
        delivery: EventDeliveryRecord {
            delivery_id: delivery_id.to_string(),
            fanout_target_id: target.target_id.clone(),
            event_id: target.event_id.clone(),
            event_type: event.envelope.event_type.clone(),
            subscription_id: target.subscription_id.clone(),
            subscription_revision: target.subscription_revision,
            stream_key: event.envelope.stream.key.clone(),
            sequence: event.envelope.stream.sequence,
            payload_bytes,
            payload_sha256,
            status: EventDeliveryStatus::Pending,
            attempt_count: 0,
            first_attempt_at_ms: None,
            last_attempt_at_ms: None,
            next_attempt_at_ms: None,
            lease_owner: None,
            lease_until_ms: None,
            last_http_status: None,
            last_error_category: None,
            last_error_summary: None,
            dead_lettered_at_ms: None,
            cancelled_at_ms: None,
            skipped_at_ms: None,
            skip_actor: None,
            skip_reason: None,
            replay_of_delivery_id: target.replay_of_delivery_id.clone(),
            resolved_by_delivery_id: None,
            resolved_at_ms: None,
            created_at_ms: materialized_at_ms,
            succeeded_at_ms: None,
            env: target.env.clone(),
        },
        materialized_at_ms,
    }
}

fn completion(
    delivery: &EventDeliveryRecord,
    _worker_id: &str,
    started_at_ms: u64,
    completed_at_ms: u64,
    result: EventDeliveryAttemptRecordResult,
    next_status: EventDeliveryStatus,
    next_attempt_at_ms: Option<u64>,
) -> CompleteEventDeliveryAttempt {
    CompleteEventDeliveryAttempt {
        delivery_id: delivery.delivery_id.clone(),
        expected_lease_owner: delivery
            .lease_owner
            .clone()
            .expect("claimed Delivery lease owner"),
        attempt_no: delivery.attempt_count,
        started_at_ms,
        completed_at_ms,
        result,
        next_status,
        next_attempt_at_ms,
        http_status: Some(if result == EventDeliveryAttemptRecordResult::Success {
            204
        } else {
            500
        }),
        error_category: (result != EventDeliveryAttemptRecordResult::Success)
            .then(|| "contract_failure".to_string()),
        error_summary: None,
        response_bytes_observed: 0,
    }
}

pub async fn organization_repo_contract_tests<T: OrganizationRepoPort + ?Sized>(repo: &T) {
    let created = repo
        .create_organization(CreateOrganizationRecord {
            env: "contract".to_string(),
            code: "promo-2026".to_string(),
            name: "Promo 2026".to_string(),
            description: Some("contract organization".to_string()),
            managing_provider_id: "provider-a".to_string(),
        })
        .await
        .expect("create organization");
    assert!(!created.disabled);

    let duplicate = repo
        .create_organization(CreateOrganizationRecord {
            env: "contract".to_string(),
            code: "promo-2026".to_string(),
            name: "Duplicate".to_string(),
            description: None,
            managing_provider_id: "provider-a".to_string(),
        })
        .await;
    assert!(matches!(duplicate, Err(ServiceError::Conflict(_))));

    let member = repo
        .upsert_member(UpsertOrganizationMemberRecord {
            env: "contract".to_string(),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-b".to_string(),
            role: Some("traffic_analyst".to_string()),
        })
        .await
        .expect("upsert member");
    assert!(!member.disabled);

    repo.set_member_disabled("contract", "promo-2026", "bot-b", true)
        .await
        .expect("disable member");
    assert!(repo
        .list_members(ListOrganizationMembersQuery {
            env: "contract".to_string(),
            organization_code: "promo-2026".to_string(),
            include_disabled: false,
            role: None,
        })
        .await
        .expect("list active members")
        .is_empty());

    let restored = repo
        .upsert_member(UpsertOrganizationMemberRecord {
            env: "contract".to_string(),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-b".to_string(),
            role: Some("merchant_growth".to_string()),
        })
        .await
        .expect("restore member");
    assert_eq!(restored.role.as_deref(), Some("merchant_growth"));
    assert!(!restored.disabled);

    for bot_uuid in ["bot-z", "bot-a", "bot-c"] {
        repo.upsert_member(UpsertOrganizationMemberRecord {
            env: "contract".to_string(),
            organization_code: "promo-2026".to_string(),
            bot_uuid: bot_uuid.to_string(),
            role: Some("traffic_analyst".to_string()),
        })
        .await
        .expect("upsert traffic analyst");
    }
    repo.set_member_disabled("contract", "promo-2026", "bot-c", true)
        .await
        .expect("disable traffic analyst");

    let first_page = repo
        .list_members_page(ListOrganizationMembersPageQuery {
            env: "contract".to_string(),
            organization_code: "promo-2026".to_string(),
            include_disabled: false,
            role: Some("traffic_analyst".to_string()),
            offset: 0,
            limit: 1,
        })
        .await
        .expect("list first traffic analyst page");
    assert_eq!(first_page.total, 2);
    assert_eq!(first_page.members.len(), 1);
    assert_eq!(first_page.members[0].bot_uuid, "bot-a");

    let second_page = repo
        .list_members_page(ListOrganizationMembersPageQuery {
            env: "contract".to_string(),
            organization_code: "promo-2026".to_string(),
            include_disabled: false,
            role: Some("traffic_analyst".to_string()),
            offset: 1,
            limit: 1,
        })
        .await
        .expect("list second traffic analyst page");
    assert_eq!(second_page.total, 2);
    assert_eq!(second_page.members.len(), 1);
    assert_eq!(second_page.members[0].bot_uuid, "bot-z");

    let deep_page = repo
        .list_members_page(ListOrganizationMembersPageQuery {
            env: "contract".to_string(),
            organization_code: "promo-2026".to_string(),
            include_disabled: false,
            role: Some("traffic_analyst".to_string()),
            offset: 99,
            limit: 1,
        })
        .await
        .expect("list empty deep page");
    assert_eq!(deep_page.total, 2);
    assert!(deep_page.members.is_empty());

    let including_disabled = repo
        .list_members_page(ListOrganizationMembersPageQuery {
            env: "contract".to_string(),
            organization_code: "promo-2026".to_string(),
            include_disabled: true,
            role: Some("traffic_analyst".to_string()),
            offset: 0,
            limit: 10,
        })
        .await
        .expect("list traffic analysts including disabled");
    assert_eq!(including_disabled.total, 3);
    assert_eq!(
        including_disabled
            .members
            .iter()
            .map(|member| member.bot_uuid.as_str())
            .collect::<Vec<_>>(),
        ["bot-a", "bot-c", "bot-z"]
    );
}

pub async fn bot_repo_contract_tests<T: BotRepoPort + ?Sized>(repo: &T) {
    let bot_id = "repo-contract-bot";
    let token = "repo-contract-token";
    let owner = "repo-owner";

    assert!(repo.get("bcs-contract-missing-bot").await.is_none());

    let mut binding_channels = std::collections::HashMap::new();
    binding_channels.insert(
        "antding".to_string(),
        BindingChannel {
            binding_key: "repo-binding".to_string(),
        },
    );
    let caps = BotCapabilities {
        name: Some("Repo Contract Bot".to_string()),
        summary: Some("contract summary".to_string()),
        domains: vec!["contracts".to_string()],
        skills: vec![Skill::new("repo_contract")],
        visibility: "public".to_string(),
        binding_channels: Some(binding_channels),
        ..Default::default()
    };

    repo.register(bot_id.to_string(), caps)
        .await
        .expect("register");
    let stored = repo.get(bot_id).await.expect("registered bot");
    assert_eq!(stored.bot_uuid, bot_id);
    assert_eq!(
        stored.capabilities.name.as_deref(),
        Some("Repo Contract Bot")
    );
    assert_eq!(stored.capabilities.visibility, "public");

    assert_eq!(
        repo.get_by_ids(&[bot_id.to_string(), bot_id.to_string()])
            .await
            .len(),
        1
    );
    assert!(
        repo.list_active()
            .await
            .iter()
            .any(|bot| bot.bot_uuid == bot_id)
    );

    assert_eq!(
        repo.find_bot_by_binding_channel("antding", "repo-binding")
            .await
            .as_deref(),
        Some(bot_id)
    );

    repo.update_visibility(bot_id, "protected")
        .await
        .expect("update visibility");
    assert_eq!(
        repo.get(bot_id)
            .await
            .expect("bot after visibility update")
            .capabilities
            .visibility,
        "protected"
    );

    repo.save_created_by(bot_id, owner, true)
        .await
        .expect("save owner");
    assert!(
        repo.list_bots_by_creator(owner)
            .await
            .iter()
            .any(|bot| bot.bot_uuid == bot_id)
    );

    repo.save_token(bot_id, token).await.expect("save token");
    assert_eq!(repo.load_token(bot_id).await.as_deref(), Some(token));
    assert_eq!(repo.find_bot_by_token(token).await.as_deref(), Some(bot_id));

    // update_capabilities replaces capabilities wholesale, including cleared
    // (empty) domains/skills/scopes — unlike register, which skips empty
    // arrays on the existing-bot merge path. Seed non-empty arrays, then
    // clear them and assert the live registry reflects the clear.
    repo.update_capabilities(
        bot_id,
        BotCapabilities {
            name: Some("Repo Contract Bot".to_string()),
            domains: vec!["contracts".to_string()],
            skills: vec![Skill::new("repo_contract")],
            scopes: vec!["production".to_string()],
            visibility: "public".to_string(),
            ..Default::default()
        },
    )
    .await
    .expect("update capabilities with seeded arrays");
    let seeded = repo.get(bot_id).await.expect("bot after seeding");
    assert_eq!(seeded.capabilities.domains, vec!["contracts"]);
    assert_eq!(seeded.capabilities.scopes, vec!["production"]);
    assert!(!seeded.capabilities.skills.is_empty());

    // Owner/token (immutable identity) must survive the wholesale replace.
    assert_eq!(repo.load_token(bot_id).await.as_deref(), Some(token));
    assert!(
        repo.list_bots_by_creator(owner)
            .await
            .iter()
            .any(|bot| bot.bot_uuid == bot_id)
    );

    repo.update_capabilities(
        bot_id,
        BotCapabilities {
            name: Some("Repo Contract Bot".to_string()),
            domains: vec![],
            skills: vec![],
            scopes: vec![],
            visibility: "public".to_string(),
            ..Default::default()
        },
    )
    .await
    .expect("update capabilities clearing arrays");
    let cleared = repo.get(bot_id).await.expect("bot after clear");
    assert!(
        cleared.capabilities.domains.is_empty(),
        "update_capabilities must clear domains (register's empty-skip does not apply)"
    );
    assert!(cleared.capabilities.skills.is_empty());
    assert!(cleared.capabilities.scopes.is_empty());
    // Unmodified visibility preserved through the replace.
    assert_eq!(cleared.capabilities.visibility, "public");
}

pub async fn bot_repo_port_contract_tests<T: BotRepoPort + ?Sized>(repo: &T) {
    bot_repo_contract_tests(repo).await;
}

pub async fn bot_control_plane_repo_port_contract_tests<T: BotControlPlaneRepoPort + ?Sized>(
    repo: &T,
    env: &str,
    known_bot_id: &str,
) {
    assert!(
        repo.get_control_plane("control-plane-contract-missing", env)
            .await
            .expect("read missing control-plane Bot")
            .is_none()
    );

    let record = repo
        .get_control_plane(known_bot_id, env)
        .await
        .expect("read known control-plane Bot")
        .expect("known control-plane Bot exists");
    assert_eq!(record.bot_id, known_bot_id);
    assert_eq!(record.env, env);

    let batch = repo
        .get_control_plane_by_ids(
            &[
                known_bot_id.to_string(),
                "control-plane-contract-missing".to_string(),
                known_bot_id.to_string(),
            ],
            env,
        )
        .await
        .expect("batch read control-plane Bots");
    assert_eq!(batch.len(), 1);
    assert_eq!(batch[0].bot_id, known_bot_id);
}

pub async fn group_repo_contract_tests<T: GroupRepoPort + ?Sized>(repo: &T) {
    assert!(repo.get("repo-contract-missing-group").await.is_none());
    assert_eq!(repo.count().await, 0);

    let mut group = Group::new(
        "repo-contract-group",
        "repo-driver",
        vec![
            Participant::bot("repo-driver", ParticipantRole::Driver),
            Participant::bot("repo-helper", ParticipantRole::Consultant),
        ],
    );
    group.label = Some("initial label".to_string());
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        sender_routes: std::collections::HashMap::from([(
            "repo-helper".to_string(),
            vec!["repo-driver".to_string()],
        )]),
    });

    repo.upsert(group.clone()).await.expect("upsert group");
    let stored = repo
        .get(&group.id)
        .await
        .expect("group exists after upsert");
    assert_eq!(stored.label.as_deref(), Some("initial label"));
    assert_eq!(stored.participants.len(), 2);
    assert_eq!(repo.count().await, 1);

    repo.add_participant(
        &group.id,
        Participant::bot("repo-observer", ParticipantRole::Observer),
    )
    .await
    .expect("add participant");
    let stored = repo.get(&group.id).await.expect("group after add");
    assert!(
        stored
            .participants
            .iter()
            .any(|p| p.bot_uuid == "repo-observer")
    );

    repo.update_participant_mode(&group.id, "repo-helper", ParticipantMode::Muted)
        .await
        .expect("update participant mode");
    let stored = repo.get(&group.id).await.expect("group after mode update");
    let helper = stored
        .participants
        .iter()
        .find(|p| p.bot_uuid == "repo-helper")
        .expect("helper participant");
    assert_eq!(helper.mode, Some(ParticipantMode::Muted));

    repo.patch_mutable_fields(
        &group.id,
        GroupMutableFieldsPatch {
            label: Some("patched label".to_string()),
            default_bot_final_delivery: Some(DefaultDelivery::InjectObservers),
            ..Default::default()
        },
    )
    .await
    .expect("patch mutable fields");
    let stored = repo.get(&group.id).await.expect("group after mutable patch");
    let routing = stored.routing_policy.expect("routing policy preserved");
    assert_eq!(stored.label.as_deref(), Some("patched label"));
    assert_eq!(stored.participants.len(), 3);
    assert_eq!(routing.mode, RoutingMode::Structured);
    assert_eq!(
        routing.sender_routes.get("repo-helper"),
        Some(&vec!["repo-driver".to_string()])
    );
    assert_eq!(
        routing.default_bot_final_delivery,
        DefaultDelivery::InjectObservers
    );

    repo.update_label(&group.id, Some("updated label".to_string()))
        .await
        .expect("update label");
    assert_eq!(
        repo.get(&group.id).await.expect("group after label").label,
        Some("updated label".to_string())
    );

    repo.update_status(&group.id, GroupStatus::Completed)
        .await
        .expect("update status");
    assert_eq!(
        repo.get(&group.id)
            .await
            .expect("group after status")
            .status,
        GroupStatus::Completed
    );

    assert!(repo.list().await.iter().any(|listed| listed.id == group.id));
    assert!(
        repo.list_paginated(0, 10)
            .await
            .iter()
            .any(|listed| listed.id == group.id)
    );
    assert_eq!(repo.count_by_participant("repo-helper").await, 1);
    assert!(
        repo.find_by_participant("repo-helper")
            .await
            .iter()
            .any(|listed| listed.id == group.id)
    );
    assert!(
        repo.find_by_participant_paginated("repo-helper", 0, 10)
            .await
            .iter()
            .any(|listed| listed.id == group.id)
    );

    assert_eq!(
        repo.message_count(&group.id).await.expect("message count"),
        0
    );
    repo.increment_message_count(&group.id)
        .await
        .expect("increment message count");
    assert_eq!(
        repo.message_count(&group.id).await.expect("message count"),
        1
    );
    repo.reset_message_count(&group.id)
        .await
        .expect("reset message count");
    assert_eq!(
        repo.message_count(&group.id).await.expect("message count"),
        0
    );

    let pair_key = Group::compute_dm_pair_key("repo-dm-a", "repo-dm-b");
    let mut dm_group = Group::new(
        "repo-contract-dm",
        "repo-dm-a",
        vec![
            Participant::bot("repo-dm-a", ParticipantRole::Driver),
            Participant::bot("repo-dm-b", ParticipantRole::Consultant),
        ],
    );
    dm_group.group_kind = GroupKind::Dm;
    dm_group.dm_pair_key = Some(pair_key.clone());

    assert!(
        repo.insert_dm_group_if_absent(dm_group.clone())
            .await
            .expect("insert dm group")
    );

    let mut duplicate_dm = dm_group;
    duplicate_dm.id = "repo-contract-dm-duplicate".to_string();
    assert!(
        !repo
            .insert_dm_group_if_absent(duplicate_dm)
            .await
            .expect("reuse dm group")
    );
    assert_eq!(
        repo.find_dm_by_pair_key(&pair_key)
            .await
            .expect("find dm group")
            .id,
        "repo-contract-dm"
    );

    assert!(
        repo.delete(&group.id)
            .await
            .expect("delete group")
            .is_some()
    );
    assert!(repo.get(&group.id).await.is_none());

    // service_spec / version / record_status roundtrip (Task 10)
    // Re-upsert the group (was deleted above) with service_spec populated.
    let mut g = group.clone();
    g.service_spec = Some(ServiceSpec {
        callback_config: None,
        timeout_seconds: Some(60),
        max_concurrency: Some(8),
    });
    g.version = 1;
    g.record_status = "active".to_string();
    repo.upsert(g.clone()).await.expect("upsert service_spec");
    let fetched = repo
        .get(&g.id)
        .await
        .expect("get after service_spec upsert");
    let spec = fetched
        .service_spec
        .expect("service_spec should roundtrip");
    assert_eq!(spec.timeout_seconds, Some(60));
    assert_eq!(spec.max_concurrency, Some(8));
    assert!(spec.callback_config.is_none());
    assert_eq!(fetched.version, 1);
    assert_eq!(fetched.record_status, "active");
}

pub async fn group_repo_port_contract_tests<T: GroupRepoPort + ?Sized>(repo: &T) {
    group_repo_contract_tests(repo).await;
}

pub async fn friend_repo_contract_tests<T: FriendRepoPort + ?Sized>(repo: &T) {
    repo.add_friendship("repo-alice", "repo-bob")
        .await
        .expect("add friendship");
    assert!(
        repo.are_friends("repo-alice", "repo-bob")
            .await
            .expect("are friends")
    );
    assert!(
        repo.are_friends("repo-bob", "repo-alice")
            .await
            .expect("are friends reverse")
    );
    assert_eq!(
        repo.list_friends("repo-alice").await.expect("list friends"),
        vec!["repo-bob".to_string()]
    );

    assert_eq!(
        repo.remove_all_friendships("repo-alice")
            .await
            .expect("remove friendships"),
        1
    );
    assert!(
        !repo
            .are_friends("repo-alice", "repo-bob")
            .await
            .expect("are friends after remove")
    );
}

pub async fn friend_repo_port_contract_tests<T: FriendRepoPort + ?Sized>(repo: &T) {
    friend_repo_contract_tests(repo).await;
}

pub async fn friend_request_repo_contract_tests<T: FriendRequestRepoPort + ?Sized>(repo: &T) {
    let missing = repo
        .get_request("repo-request-missing")
        .await
        .expect_err("missing request should error");
    assert!(matches!(missing, ServiceError::FriendRequestNotFound(_)));

    let request = FriendRequest {
        id: "repo-request-1".to_string(),
        from_bot: "repo-alice".to_string(),
        to_bot: "repo-bob".to_string(),
        status: FriendRequestStatus::Pending,
        created_at: now_ms(),
        updated_at: now_ms(),
    };
    assert!(
        repo.insert_pending_request_if_absent(request.clone())
            .await
            .expect("insert pending request")
            .is_none()
    );
    assert_eq!(
        repo.insert_pending_request_if_absent(FriendRequest {
            id: "repo-request-duplicate".to_string(),
            from_bot: request.from_bot.clone(),
            to_bot: request.to_bot.clone(),
            status: FriendRequestStatus::Pending,
            created_at: now_ms(),
            updated_at: now_ms(),
        })
        .await
        .expect("duplicate pending request")
        .map(|found| found.id),
        Some(request.id.clone())
    );

    assert_eq!(
        repo.get_request(&request.id)
            .await
            .expect("get inserted request")
            .status,
        FriendRequestStatus::Pending
    );
    assert_eq!(
        repo.find_pending_request("repo-alice", "repo-bob")
            .await
            .expect("find pending")
            .map(|found| found.id),
        Some(request.id.clone())
    );
    assert!(
        repo.list_requests(
            "repo-alice",
            FriendRequestDirection::Sent,
            Some(FriendRequestStatus::Pending),
        )
        .await
        .iter()
        .any(|listed| listed.id == request.id)
    );

    repo.update_request_status(&request.id, FriendRequestStatus::Accepted)
        .await
        .expect("accept request");
    assert_eq!(
        repo.get_request(&request.id)
            .await
            .expect("get accepted request")
            .status,
        FriendRequestStatus::Accepted
    );

    let reverse = FriendRequest {
        id: "repo-request-reverse".to_string(),
        from_bot: "repo-bob".to_string(),
        to_bot: "repo-alice".to_string(),
        status: FriendRequestStatus::Pending,
        created_at: now_ms(),
        updated_at: now_ms(),
    };
    repo.insert_request(reverse.clone())
        .await
        .expect("insert reverse request");
    assert_eq!(
        repo.accept_reverse_pending_requests("repo-alice", "repo-bob")
            .await
            .expect("accept reverse"),
        1
    );
    assert_eq!(
        repo.get_request(&reverse.id)
            .await
            .expect("get reverse request")
            .status,
        FriendRequestStatus::Accepted
    );

    let pending = FriendRequest {
        id: "repo-request-pending-cancel".to_string(),
        from_bot: "repo-charlie".to_string(),
        to_bot: "repo-alice".to_string(),
        status: FriendRequestStatus::Pending,
        created_at: now_ms(),
        updated_at: now_ms(),
    };
    repo.insert_request(pending.clone())
        .await
        .expect("insert pending request");
    assert_eq!(
        repo.delete_pending_requests_for_bot("repo-alice")
            .await
            .expect("delete pending"),
        1
    );
    let missing_after_delete = repo
        .get_request(&pending.id)
        .await
        .expect_err("deleted pending request should be missing");
    assert!(matches!(
        missing_after_delete,
        ServiceError::FriendRequestNotFound(_)
    ));
}

pub async fn friend_request_repo_port_contract_tests<T: FriendRequestRepoPort + ?Sized>(repo: &T) {
    friend_request_repo_contract_tests(repo).await;
}

pub async fn proposal_repo_contract_tests<T: ProposalCoreService + ?Sized>(repo: &T) {
    let proposal = GroupChatProposal {
        token: "repo-proposal-token".to_string(),
        driver_bot: "driver".to_string(),
        participants: vec!["driver".to_string(), "helper".to_string()],
        reason: "contract".to_string(),
        proposed_by: "driver".to_string(),
        member_intros: "driver/helper".to_string(),
        confirm_url: "https://example.invalid/confirm".to_string(),
        created_at: now_ms(),
    };

    assert_eq!(repo.store(proposal.clone()).await, proposal.token);
    assert_eq!(
        repo.get(&proposal.token).await.map(|stored| stored.reason),
        Some("contract".to_string())
    );
    assert!(repo.take(&proposal.token).await.is_some());
    assert!(repo.get(&proposal.token).await.is_none());
}

pub async fn relation_repo_contract_tests<T: RelationRepoPort + ?Sized>(repo: &T) {
    let edge = RelationEdge {
        from_id: "repo-human".to_string(),
        to_id: "repo-bot".to_string(),
        env: "repo-env".to_string(),
        kinds: 0,
        allow: 0,
        deny: 0,
        is_creator: true,
    };

    repo.upsert_edge(edge.clone()).await.expect("upsert edge");
    let stored = repo
        .get_edge(&edge.from_id, &edge.to_id, &edge.env)
        .await
        .expect("get edge")
        .expect("stored edge");
    assert!(stored.is_creator);

    repo.delete_edge(&edge.from_id, &edge.to_id, &edge.env)
        .await
        .expect("delete edge");
    assert!(
        repo.get_edge(&edge.from_id, &edge.to_id, &edge.env)
            .await
            .expect("get after delete")
            .is_none()
    );
}

pub async fn relation_repo_port_contract_tests<T: RelationRepoPort + ?Sized>(repo: &T) {
    relation_repo_contract_tests(repo).await;
}

pub async fn session_repo_contract_tests<T: SessionRepoPort + ?Sized>(repo: &T) {
    let group_id = "repo-contract-session-group";
    let participants = vec![Participant::bot("bot1", ParticipantRole::Driver)];

    // create — chat session, auto-generated id
    let s: Session = repo
        .create(
            group_id,
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: participants.clone(),
                ..Default::default()
            },
        )
        .await
        .expect("create chat session");
    assert!(s.id.starts_with("repo-contract-session-group:"));
    assert_eq!(s.status, SessionStatus::Running);

    // get / belongs_to_group
    let fetched = repo.get(&s.id).await.expect("get session");
    assert_eq!(fetched.id, s.id);
    assert!(repo.belongs_to_group(&s.id, group_id).await);
    assert!(!repo.belongs_to_group(&s.id, "other-group").await);

    // list_by_group / latest_running
    let listed = repo
        .list_by_group(group_id, Some(SessionStatus::Running), 0, 10, None, None)
        .await;
    assert_eq!(listed.len(), 1);
    let latest = repo.latest_running(group_id).await.expect("latest running");
    assert_eq!(latest.id, s.id);

    // complete_if_running — first call succeeds
    let completed = repo
        .complete_if_running(&s.id, Some(serde_json::json!({"ok": 1})), None)
        .await
        .expect("complete_if_running first");
    assert!(completed.is_some(), "first complete returns Some");
    assert_eq!(
        completed.expect("completed session").status,
        SessionStatus::Completed
    );

    // complete_if_running — second call is a no-op
    let again = repo
        .complete_if_running(&s.id, None, None)
        .await
        .expect("complete_if_running second");
    assert!(again.is_none(), "CAS short-circuits on already-completed");

    // service_invocation session starts with callback_status="pending"
    let svc: Session = repo
        .create(
            group_id,
            NewSessionParams {
                session_kind: SessionKind::ServiceInvocation,
                participants: participants.clone(),
                ..Default::default()
            },
        )
        .await
        .expect("create service_invocation session");
    assert_eq!(svc.callback_status.as_deref(), Some("pending"));
    repo.complete_if_running(&svc.id, None, None)
        .await
        .expect("complete svc session");

    // reactivate must fail while callback is still pending
    let r = repo.reactivate(&svc.id, None).await;
    assert!(r.is_err(), "reactivate must reject when callback pending");

    // write terminal callback status, then reactivate succeeds
    repo.update_callback_status(&svc.id, "succeeded")
        .await
        .expect("update_callback_status");
    let reacted = repo
        .reactivate(&svc.id, None)
        .await
        .expect("reactivate after terminal callback");
    assert_eq!(reacted.status, SessionStatus::Running);
    assert_eq!(reacted.activation_count, 2);

    // count_running_service / list_running_service
    assert_eq!(repo.count_running_service(group_id).await, 1);
    let svc_running = repo.list_running_service(0, 10).await;
    assert!(svc_running.iter().any(|s| s.id == svc.id));

    // add_participant / update_participant_mode / remove_participant
    let extra = Participant::bot("bot2", ParticipantRole::Consultant);
    let added = repo
        .add_participant(&svc.id, extra.clone())
        .await
        .expect("add_participant");
    assert_eq!(added.participants.len(), 2);
    let modded = repo
        .update_participant_mode(&svc.id, "bot2", ParticipantMode::Muted)
        .await
        .expect("update_participant_mode");
    let bot2 = modded
        .participants
        .iter()
        .find(|p| p.bot_uuid == "bot2")
        .expect("bot2 participant");
    assert_eq!(bot2.mode, Some(ParticipantMode::Muted));
    let removed = repo
        .remove_participant(&svc.id, "bot2")
        .await
        .expect("remove_participant");
    assert_eq!(removed.participants.len(), 1);

    // update_title
    let titled = repo
        .update_title(&svc.id, Some("hello".to_string()))
        .await
        .expect("update_title");
    assert_eq!(titled.session_title.as_deref(), Some("hello"));

    // list_group_ids_by_session_participant
    let groups = repo.list_group_ids_by_session_participant("bot1").await;
    assert!(groups.contains(&group_id.to_string()));

    // ── session collection (收藏) contract ───────────────────
    // Create a second participant so we can assert per-bot isolation.
    let collect_session: Session = repo
        .create(
            &svc.group_id,
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: vec![
                    Participant::bot("bot-collector", ParticipantRole::Driver),
                    Participant::bot("bot-other", ParticipantRole::Consultant),
                ],
                ..Default::default()
            },
        )
        .await
        .expect("create session for collection contract");

    // Not collected yet
    assert!(repo
        .list_collected_by_group(&svc.group_id, "bot-collector", None, None, 0, 10)
        .await
        .is_empty());

    // collect by a participant
    repo.collect(&collect_session.id, "bot-collector")
        .await
        .expect("collect by participant");
    let collected = repo
        .list_collected_by_group(&svc.group_id, "bot-collector", None, None, 0, 10)
        .await;
    assert_eq!(collected.len(), 1);
    assert_eq!(collected[0].id, collect_session.id);
    // collected_at is surfaced on the collected-list context (COALESCE fallback
    // to created_at guarantees Some here).
    assert!(
        collected[0].collected_at.is_some(),
        "collected list must surface collected_at"
    );
    // Batch map lookup matches the collected-list view: only the collected
    // session appears, with a collected_at timestamp.
    let map = repo
        .collected_at_map(&[collect_session.id.as_str()], "bot-collector")
        .await;
    assert_eq!(map.len(), 1);
    assert_eq!(map[0].0, collect_session.id);
    assert!(map[0].1 > 0, "collected_at_map must return a timestamp");
    // A bot that has not collected gets an empty map.
    let empty = repo
        .collected_at_map(&[collect_session.id.as_str()], "bot-other")
        .await;
    assert!(empty.is_empty(), "collected_at_map must be empty for non-collector");

    // per-bot isolation: other participant sees nothing
    assert!(repo
        .list_collected_by_group(&svc.group_id, "bot-other", None, None, 0, 10)
        .await
        .is_empty());

    // collect by non-participant errors
    let err = repo.collect(&collect_session.id, "bot-stranger").await;
    assert!(err.is_err(), "collect by non-participant must error");

    // repeat collect is idempotent (no error)
    repo.collect(&collect_session.id, "bot-collector")
        .await
        .expect("repeat collect idempotent");

    // uncollect removes it
    repo.uncollect(&collect_session.id, "bot-collector")
        .await
        .expect("uncollect");
    assert!(repo
        .list_collected_by_group(&svc.group_id, "bot-collector", None, None, 0, 10)
        .await
        .is_empty());

    // uncollect of a never-collected / non-participant is idempotent Ok
    repo.uncollect(&collect_session.id, "bot-collector")
        .await
        .expect("uncollect not-collected idempotent");
    repo.uncollect(&collect_session.id, "bot-stranger")
        .await
        .expect("uncollect non-participant idempotent");

    // count_by_group — mirrors list_by_group filters, returns total (no pagination).
    // Group now has 3 sessions:
    //   s               — Completed, title=None,    bot1
    //   svc             — Running,   title="hello", bot1
    //   collect_session — Running,   title=None,    bot-collector + bot-other
    assert_eq!(
        repo.count_by_group(group_id, None, None, None).await.expect("count_by_group none"),
        3
    );
    assert_eq!(
        repo.count_by_group(group_id, Some(SessionStatus::Running), None, None)
            .await
            .expect("count_by_group running"),
        2
    );
    assert_eq!(
        repo.count_by_group(group_id, Some(SessionStatus::Completed), None, None)
            .await
            .expect("count_by_group completed"),
        1
    );
    assert_eq!(
        repo.count_by_group(group_id, None, Some("hello"), None)
            .await
            .expect("count_by_group hello"),
        1
    );
    assert_eq!(
        repo.count_by_group(group_id, None, None, Some("bot1"))
            .await
            .expect("count_by_group bot1"),
        2
    );
    assert_eq!(
        repo.count_by_group(group_id, None, None, Some("bot-collector"))
            .await
            .expect("count_by_group bot-collector"),
        1
    );
    // count_by_group must equal list_by_group total (large limit) — consistency.
    let listed_all = repo.list_by_group(group_id, None, 0, 1000, None, None).await;
    assert_eq!(
        listed_all.len() as u64,
        repo.count_by_group(group_id, None, None, None)
            .await
            .expect("count_by_group consistency")
    );
    // count != paginated subset
    let listed_page = repo.list_by_group(group_id, None, 0, 1, None, None).await;
    assert_eq!(listed_page.len(), 1);
    assert_eq!(
        repo.count_by_group(group_id, None, None, None)
            .await
            .expect("count_by_group after page"),
        3
    );
}

pub async fn session_repo_port_contract_tests<T: SessionRepoPort + ?Sized>(repo: &T) {
    session_repo_contract_tests(repo).await;
}

pub async fn message_repo_contract_tests<T: MessageRepoPort + ?Sized>(repo: &T) {
    let group_id = "contract-group";
    let session_id = "contract-group:abcd1234";

    // append_message
    let msg = NewMessage {
        group_id: group_id.to_string(),
        session_id: session_id.to_string(),
        sender_id: "bot-a".to_string(),
        sender_type: SenderType::Bot,
        message_type: "text".to_string(),
        content: serde_json::json!({"text": "hello world"}),
        client_msg_id: Some("client-msg-1".to_string()),
        owner_bot_id: None,
        created_at: 1000,
        run_id: String::new(),
    };
    let persisted = repo.append_message(msg).await.expect("append_message");
    assert_eq!(persisted.session_id, session_id);
    assert_eq!(persisted.session_seq, 1);
    assert_eq!(persisted.sender_id, "bot-a");
    assert!(!persisted.message_id.is_empty());

    // Idempotency
    let dup_msg = NewMessage {
        group_id: group_id.to_string(),
        session_id: session_id.to_string(),
        sender_id: "bot-a".to_string(),
        sender_type: SenderType::Bot,
        message_type: "text".to_string(),
        content: serde_json::json!({"text": "duplicate"}),
        client_msg_id: Some("client-msg-1".to_string()),
        owner_bot_id: None,
        created_at: 2000,
        run_id: String::new(),
    };
    let dup = repo.append_message(dup_msg).await.expect("idempotent append");
    assert_eq!(dup.message_id, persisted.message_id);
    assert_eq!(dup.session_seq, 1);

    // Append more messages
    for i in 0..5u64 {
        let m = NewMessage {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            sender_id: if i % 2 == 0 { "bot-a".to_string() } else { "bot-b".to_string() },
            sender_type: SenderType::Bot,
            message_type: if i < 3 { "text".to_string() } else { "system_event".to_string() },
            content: serde_json::json!({"seq": i}),
            client_msg_id: None,
            owner_bot_id: None,
            created_at: 2000 + i * 100,
            run_id: String::new(),
        };
        repo.append_message(m).await.expect("append");
    }

    // get_current_seq
    let seq = repo.get_current_seq(session_id).await.expect("get_current_seq");
    assert_eq!(seq, 6);

    // query_messages — pagination
    let page = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: None,
            limit: 3,
            keyword: None,
            sender_id: None,
            message_type: None,
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: None,
        })
        .await
        .expect("query_messages");
    assert_eq!(page.messages.len(), 3);
    assert!(page.has_more);
    assert!(page.next_cursor.is_some());

    // query_messages — cursor. The repo surfaces a composite
    // `(created_at, session_seq)` next_cursor; the legacy created_at-only
    // cursor param extracts `.0` to preserve the legacy created_at-only
    // predicate (the seed messages have distinct created_at values, so the
    // composite and created_at-only cursors behave identically here).
    let page2 = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: page.next_cursor.map(|c| c.0),
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: None,
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: None,
        })
        .await
        .expect("query_messages cursor");
    assert_eq!(page2.messages.len(), 3);
    assert!(!page2.has_more);

    // query_messages — sender filter
    let page3 = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: Some("bot-b".to_string()),
            message_type: None,
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: None,
        })
        .await
        .expect("query by sender");
    assert!(page3.messages.iter().all(|m| m.sender_id == "bot-b"));

    // query_messages — message_type filter
    let page4 = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: Some("system_event".to_string()),
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: None,
        })
        .await
        .expect("query by type");
    assert!(page4.messages.iter().all(|m| m.message_type == "system_event"));

    // query_messages — keyword search
    let page5 = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: None,
            limit: 10,
            keyword: Some("hello".to_string()),
            sender_id: None,
            message_type: None,
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: None,
        })
        .await
        .expect("keyword search");
    assert_eq!(page5.messages.len(), 1);

    // query_messages — visible_from_seq
    let page6 = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: None,
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: Some(4),
        })
        .await
        .expect("visible_from_seq");
    assert!(page6.messages.iter().all(|m| m.session_seq >= 4));

    // get_message_by_id
    let found = repo
        .get_message_by_id(session_id, &persisted.message_id)
        .await
        .expect("get_message_by_id");
    assert!(found.is_some());
    assert_eq!(found.unwrap().message_id, persisted.message_id);

    // get_message_by_id — missing
    let missing = repo
        .get_message_by_id(session_id, "nonexistent")
        .await
        .expect("get_message_by_id missing");
    assert!(missing.is_none());

    // Empty session
    let empty_seq = repo.get_current_seq("empty-session").await.expect("empty seq");
    assert_eq!(empty_seq, 0);
    let empty_page = repo
        .query_messages(MessageQuery {
            group_id: "empty".to_string(),
            session_id: "empty-session".to_string(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: None,
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: None,
        })
        .await
        .expect("empty query");
    assert!(empty_page.messages.is_empty());
    assert!(!empty_page.has_more);

    // owner_bot_id round-trip and filtering
    let mgr = repo
        .append_message(NewMessage {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            sender_id: "mgr".to_string(),
            sender_type: SenderType::Bot,
            message_type: "text".to_string(),
            content: serde_json::json!({"text": "manager"}),
            client_msg_id: None,
            owner_bot_id: Some("mgr".to_string()),
            created_at: 5000,
            run_id: String::new(),
        })
        .await
        .expect("append owner manager message");
    let worker_a = repo
        .append_message(NewMessage {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            sender_id: "workerA".to_string(),
            sender_type: SenderType::Bot,
            message_type: "text".to_string(),
            content: serde_json::json!({"text": "workerA"}),
            client_msg_id: None,
            owner_bot_id: Some("workerA".to_string()),
            created_at: 5100,
            run_id: String::new(),
        })
        .await
        .expect("append owner worker message");
    let sys = repo
        .append_message(NewMessage {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            sender_id: "system".to_string(),
            sender_type: SenderType::System,
            message_type: "system_event".to_string(),
            content: serde_json::json!({"text": "system"}),
            client_msg_id: None,
            owner_bot_id: None,
            created_at: 5200,
            run_id: String::new(),
        })
        .await
        .expect("append system ownerless message");
    assert_eq!(mgr.owner_bot_id.as_deref(), Some("mgr"));
    assert_eq!(worker_a.owner_bot_id.as_deref(), Some("workerA"));
    assert_eq!(sys.owner_bot_id, None);

    let owner_page = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: None,
            owner_filter: MessageOwnerFilter::Eq("workerA".to_string()),
            time_range: Some((5000, 5200)),
            visible_from_seq: None,
        })
        .await
        .expect("query by owner_bot_id");
    assert_eq!(owner_page.messages.len(), 1);
    assert_eq!(owner_page.messages[0].owner_bot_id.as_deref(), Some("workerA"));

    let unfiltered_owner_page = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: None,
            owner_filter: MessageOwnerFilter::Any,
            time_range: Some((5000, 5200)),
            visible_from_seq: None,
        })
        .await
        .expect("query without owner filter");
    assert_eq!(unfiltered_owner_page.messages.len(), 3);

    let public_owner_page = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: None,
            owner_filter: MessageOwnerFilter::IsNull,
            time_range: Some((5000, 5200)),
            visible_from_seq: None,
        })
        .await
        .expect("query public owner rows");
    assert_eq!(public_owner_page.messages.len(), 1);
    assert_eq!(public_owner_page.messages[0].owner_bot_id, None);

    // PublicOrOwner → 公共(owner=None) + 命中 viewer 的副本；他人 owner 不返回。
    let public_or_mgr = repo
        .query_messages(MessageQuery {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: None,
            owner_filter: MessageOwnerFilter::PublicOrOwner("mgr".to_string()),
            time_range: Some((5000, 5200)),
            visible_from_seq: None,
        })
        .await
        .expect("query public-or-mgr");
    // sys(owner=None) + mgr(owner=mgr) 命中；workerA(owner=workerA) 不返回。
    assert_eq!(public_or_mgr.messages.len(), 2);
    assert!(public_or_mgr
        .messages
        .iter()
        .all(|m| m.owner_bot_id.is_none() || m.owner_bot_id.as_deref() == Some("mgr")));
    assert!(public_or_mgr
        .messages
        .iter()
        .any(|m| m.owner_bot_id.is_none()));
    assert!(public_or_mgr
        .messages
        .iter()
        .any(|m| m.owner_bot_id.as_deref() == Some("mgr")));

    // list_session_history — legacy direct-read contract: `created_at DESC,
    // session_seq DESC` with composite `(created_at, session_seq)` cursor
    // pagination + full `MessageOwnerFilter`. env isolation (VUlao) is the
    // store's responsibility: the MySQL/SQLite store filters reads by its own
    // `env`; the memory store does not track env.
    let history = repo
        .list_session_history(session_id, MessageOwnerFilter::Any, None, None, 3)
        .await
        .expect("list_session_history first page");
    assert!(history.has_more);
    assert!(history.next_cursor.is_some());
    assert_eq!(
        history.next_cursor,
        Some((5000, 7)),
        "next_cursor is the composite (created_at, session_seq) of the last row"
    );
    assert_eq!(
        history.messages.iter().map(|m| m.session_seq).collect::<Vec<_>>(),
        vec![9, 8, 7],
        "must be created_at DESC, session_seq DESC"
    );

    // follow the cursor: before=(5000,7) excludes seq 7 (5000,7) and anything
    // newer, so the next page is seqs 6,5,4 (still has_more). Verifies the
    // VYQHI composite-cursor fix — a bare created_at cursor would skip seq 7.
    let history_next = repo
        .list_session_history(
            session_id,
            MessageOwnerFilter::Any,
            None,
            history.next_cursor,
            3,
        )
        .await
        .expect("list_session_history next page");
    assert!(history_next.has_more);
    assert_eq!(
        history_next.next_cursor,
        Some((2200, 4)),
        "next page cursor is the composite (created_at, session_seq) of seq 4"
    );
    assert_eq!(
        history_next
            .messages
            .iter()
            .map(|m| m.session_seq)
            .collect::<Vec<_>>(),
        vec![6, 5, 4]
    );

    // IsNull → only NULL-owned messages (seqs 9,6,5,4,3,2,1) in DESC order.
    let public_only = repo
        .list_session_history(session_id, MessageOwnerFilter::IsNull, None, None, 100)
        .await
        .expect("list_session_history IsNull");
    assert_eq!(
        public_only
            .messages
            .iter()
            .map(|m| m.session_seq)
            .collect::<Vec<_>>(),
        vec![9, 6, 5, 4, 3, 2, 1]
    );
    assert!(!public_only.has_more);

    // Eq → only the given owner's messages (seq 8 is workerA).
    let worker_only = repo
        .list_session_history(
            session_id,
            MessageOwnerFilter::Eq("workerA".to_string()),
            None,
            None,
            100,
        )
        .await
        .expect("list_session_history Eq");
    assert_eq!(
        worker_only
            .messages
            .iter()
            .map(|m| m.session_seq)
            .collect::<Vec<_>>(),
        vec![8]
    );

    // PublicOrOwner("workerA") → 公共(NULL seqs 9,6,5,4,3,2,1) + workerA(seq 8)，DESC；
    // seq 7(mgr) 被排除。
    let public_or_wa = repo
        .list_session_history(
            session_id,
            MessageOwnerFilter::PublicOrOwner("workerA".to_string()),
            None,
            None,
            100,
        )
        .await
        .expect("list_session_history PublicOrOwner");
    assert_eq!(
        public_or_wa
            .messages
            .iter()
            .map(|m| m.session_seq)
            .collect::<Vec<_>>(),
        vec![9, 8, 6, 5, 4, 3, 2, 1]
    );
    assert!(public_or_wa.messages.iter().all(|m| {
        m.owner_bot_id.is_none() || m.owner_bot_id.as_deref() == Some("workerA")
    }));
    assert!(
        !public_or_wa.messages.iter().any(|m| m.session_seq == 7),
        "mgr-owned seq 7 must NOT appear under PublicOrOwner(workerA)"
    );

    // visible_from_seq cutoff: only seqs >= 4 survive, DESC.
    let cutoff = repo
        .list_session_history(session_id, MessageOwnerFilter::Any, Some(4), None, 100)
        .await
        .expect("list_session_history visible_from_seq");
    assert_eq!(
        cutoff.messages.iter().map(|m| m.session_seq).collect::<Vec<_>>(),
        vec![9, 8, 7, 6, 5, 4]
    );

    // unknown session → empty page, no more.
    let empty_history = repo
        .list_session_history("no-such-session", MessageOwnerFilter::Any, None, None, 10)
        .await
        .expect("list_session_history unknown session");
    assert!(empty_history.messages.is_empty());
    assert!(!empty_history.has_more);
    assert!(empty_history.next_cursor.is_none());
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}
