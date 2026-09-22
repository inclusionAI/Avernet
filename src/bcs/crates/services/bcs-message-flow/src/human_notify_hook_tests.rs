//! Tests for the human mention notification hook: trigger resolution and
//! the per-Group notify-policy gate.

use super::*;

fn overlay_entry(
    bot_uuid: &str,
    bot_name: Option<&str>,
    actor_kind: ActorKind,
    status: ActorStatus,
) -> RouteParticipantOverlay {
    RouteParticipantOverlay {
        bot_uuid: bot_uuid.to_string(),
        bot_name: bot_name.map(str::to_string),
        actor_kind,
        mode: None,
        status,
        is_driver: false,
    }
}

fn overlay() -> Vec<RouteParticipantOverlay> {
    vec![
        overlay_entry("bot-driver", Some("Driver"), ActorKind::Bot, ActorStatus::Online),
        overlay_entry("human_1", Some("Human One"), ActorKind::Human, ActorStatus::Online),
        overlay_entry("human_2", Some("Hidden Human"), ActorKind::Human, ActorStatus::Hidden),
        overlay_entry("human_3", Some("Human Three"), ActorKind::Human, ActorStatus::Online),
    ]
}

fn online_humans_overlay(count: u8) -> Vec<RouteParticipantOverlay> {
    let mut overlay = vec![overlay_entry(
        "bot-driver",
        Some("Driver"),
        ActorKind::Bot,
        ActorStatus::Online,
    )];
    for n in 1..=count {
        overlay.push(overlay_entry(
            &format!("human_{n}"),
            Some(&format!("Human {n}")),
            ActorKind::Human,
            ActorStatus::Online,
        ));
    }
    overlay
}

#[test]
fn trigger_resolves_human_mentions() {
    let ids = vec!["human_1".to_string(), "bot-driver".to_string()];
    let trigger = build_mention_trigger(&ids, &overlay(), "bot-driver").expect("trigger");
    assert_eq!(trigger.len(), 1);
    assert_eq!(trigger[0].actor_id, "human_1");
    assert_eq!(trigger[0].display_name, "Human One");
}

#[test]
fn trigger_excludes_self_mention() {
    let ids = vec!["human_1".to_string()];
    assert!(build_mention_trigger(&ids, &overlay(), "human_1").is_none());
}

#[test]
fn trigger_skips_hidden_humans() {
    let ids = vec!["human_2".to_string()];
    assert!(build_mention_trigger(&ids, &overlay(), "bot-driver").is_none());
}

#[test]
fn trigger_none_for_bot_only_or_unknown_mentions() {
    let overlay = overlay();
    assert!(build_mention_trigger(&["bot-driver".to_string()], &overlay, "human_1").is_none());
    assert!(build_mention_trigger(&["unknown".to_string()], &overlay, "bot-driver").is_none());
    assert!(build_mention_trigger(&[], &overlay, "bot-driver").is_none());
}

#[test]
fn trigger_deduplicates_repeated_mentions() {
    let ids = vec!["human_1".to_string(), "human_1".to_string()];
    let trigger = build_mention_trigger(&ids, &overlay(), "bot-driver").expect("trigger");
    assert_eq!(trigger.len(), 1);
}

// --- policy gate fixtures ------------------------------------------------
//
// The policy-aware Group Core double implements `read_human_notify_policy`
// itself: the trait's fail-closed default must never make an `all` case
// pass, and the default-error path is exercised separately (Ok(None) and
// injected read failure both suppress).


use bcs_service_api::{GroupCoreService, GroupHumanNotifyPolicy, ServiceError, ServiceResult};
use bcs_test_support::NoopGroupCoreService;

struct PolicyGroupCore {
    fallback: NoopGroupCoreService,
    state: std::sync::Mutex<PolicyState>,
    reads: tokio::sync::watch::Sender<usize>,
    reads_rx: tokio::sync::watch::Receiver<usize>,
    gate: tokio::sync::Mutex<Option<tokio::sync::mpsc::Receiver<()>>>,
    observed: tokio::sync::Mutex<Option<GroupHumanNotifyPolicy>>,
}

#[derive(Clone)]
struct PolicyState {
    policy: Option<GroupHumanNotifyPolicy>,
    fail_reads: bool,
}

impl PolicyGroupCore {
    fn core(mode: bcs_domain::HumanMentionNotifyMode, driver_bot_id: &str) -> Self {
        Self::from_state(PolicyState {
            policy: Some(GroupHumanNotifyPolicy {
                mode,
                driver_bot_id: driver_bot_id.to_string(),
            }),
            fail_reads: false,
        })
    }

    fn group_missing() -> Self {
        Self::from_state(PolicyState {
            policy: None,
            fail_reads: false,
        })
    }

    fn from_state(state: PolicyState) -> Self {
        let (reads, reads_rx) = tokio::sync::watch::channel(0usize);
        Self {
            fallback: NoopGroupCoreService,
            state: std::sync::Mutex::new(state),
            reads,
            reads_rx,
            gate: tokio::sync::Mutex::new(None),
            observed: tokio::sync::Mutex::new(None),
        }
    }

    fn set_fail_reads(&self, fail: bool) {
        self.state.lock().unwrap().fail_reads = fail;
    }

    fn replace_policy(&self, policy: GroupHumanNotifyPolicy) {
        self.state.lock().unwrap().policy = Some(policy);
    }

    async fn read_count(&self) -> usize {
        *self.reads_rx.borrow()
    }

    /// Deterministic entry wait: resolves once the helper issued at least
    /// `minimum` policy reads. No sleeps.
    async fn wait_read_count(&self, minimum: usize) {
        let mut receiver = self.reads.subscribe();
        while *receiver.borrow_and_update() < minimum {
            if receiver.changed().await.is_err() {
                panic!("policy-read watch closed before reaching {minimum}");
            }
        }
    }

    /// Pause the next policy read before it observes state; the returned
    /// sender releases it. Used for barrier tests only.
    async fn hold_next_read(&self) -> tokio::sync::mpsc::Sender<()> {
        let (sender, receiver) = tokio::sync::mpsc::channel(1);
        *self.gate.lock().await = Some(receiver);
        sender
    }

    async fn observed_policy(&self) -> Option<GroupHumanNotifyPolicy> {
        self.observed.lock().await.clone()
    }
}

#[async_trait::async_trait]
impl GroupCoreService for PolicyGroupCore {
    async fn upsert(&self, group: bcs_domain::Group) -> ServiceResult<()> {
        self.fallback.upsert(group).await
    }

    async fn get(&self, id: &str) -> Option<bcs_domain::Group> {
        self.fallback.get(id).await
    }

    async fn add_message(&self, id: &str, message: bcs_domain::GroupMessage) -> ServiceResult<()> {
        self.fallback.add_message(id, message).await
    }

    async fn add_participant(&self, id: &str, participant: bcs_domain::Participant) -> ServiceResult<()> {
        self.fallback.add_participant(id, participant).await
    }

    async fn remove_participant(&self, group_id: &str, bot_uuid: &str) -> ServiceResult<()> {
        self.fallback.remove_participant(group_id, bot_uuid).await
    }

    async fn update_participant_mode(
        &self,
        id: &str,
        actor_id: &str,
        mode: bcs_domain::ParticipantMode,
    ) -> ServiceResult<()> {
        self.fallback.update_participant_mode(id, actor_id, mode).await
    }

    async fn update_workspace(&self, id: &str, workspace: bcs_domain::Workspace) -> ServiceResult<()> {
        self.fallback.update_workspace(id, workspace).await
    }

    async fn update_label(&self, id: &str, label: Option<String>) -> ServiceResult<()> {
        self.fallback.update_label(id, label).await
    }

    async fn update_status(&self, id: &str, status: bcs_domain::GroupStatus) -> ServiceResult<()> {
        self.fallback.update_status(id, status).await
    }

    async fn update_service_spec(
        &self,
        id: &str,
        service_spec: Option<bcs_service_api::ServiceSpec>,
    ) -> ServiceResult<()> {
        self.fallback.update_service_spec(id, service_spec).await
    }

    async fn terminate(&self, id: &str, caller_bot_id: &str) -> ServiceResult<bcs_domain::Group> {
        self.fallback.terminate(id, caller_bot_id).await
    }

    async fn delete(&self, id: &str) -> ServiceResult<Option<bcs_domain::Group>> {
        self.fallback.delete(id).await
    }

    async fn list(&self) -> Vec<bcs_domain::Group> {
        self.fallback.list().await
    }

    async fn list_paginated(&self, offset: u64, limit: u64) -> Vec<bcs_domain::Group> {
        self.fallback.list_paginated(offset, limit).await
    }

    async fn find_by_participant(&self, bot_uuid: &str) -> Vec<bcs_domain::Group> {
        self.fallback.find_by_participant(bot_uuid).await
    }

    async fn count(&self) -> u64 {
        self.fallback.count().await
    }

    async fn count_by_participant(&self, bot_uuid: &str) -> u64 {
        self.fallback.count_by_participant(bot_uuid).await
    }

    async fn find_by_participant_paginated(
        &self,
        bot_uuid: &str,
        offset: u64,
        limit: u64,
    ) -> Vec<bcs_domain::Group> {
        self.fallback
            .find_by_participant_paginated(bot_uuid, offset, limit)
            .await
    }

    async fn message_count(&self, id: &str) -> ServiceResult<usize> {
        self.fallback.message_count(id).await
    }

    async fn increment_message_count(&self, id: &str) -> ServiceResult<()> {
        self.fallback.increment_message_count(id).await
    }

    async fn reset_message_count(&self, id: &str) -> ServiceResult<()> {
        self.fallback.reset_message_count(id).await
    }

    async fn create_or_reuse_actor_dm_group(
        &self,
        id: &str,
        actor_a: bcs_service_api::DmActorSpec,
        actor_b: bcs_service_api::DmActorSpec,
        legacy_driver_bot: &str,
        originator_actor_id: &str,
        label: Option<String>,
        context: Option<String>,
    ) -> ServiceResult<(bcs_domain::Group, bool)> {
        self.fallback
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

    async fn read_human_notify_policy(
        &self,
        _group_id: &str,
    ) -> ServiceResult<Option<GroupHumanNotifyPolicy>> {
        self.reads.send_modify(|count| *count += 1);
        if let Some(mut receiver) = self.gate.lock().await.take() {
            let _ = receiver.recv().await;
        }
        let state = self.state.lock().unwrap().clone();
        if state.fail_reads {
            return Err(ServiceError::InternalError(
                "injected policy read failure".to_string(),
            ));
        }
        *self.observed.lock().await = state.policy.clone();
        Ok(state.policy)
    }
}

#[derive(Default)]
struct RecordingNotify {
    notifications: tokio::sync::Mutex<Vec<MentionNotification>>,
}

impl RecordingNotify {
    async fn notifications(
        &self,
    ) -> Vec<MentionNotification> {
        self.notifications.lock().await.clone()
    }

    async fn wait_for(
        &self,
        expected: usize,
    ) -> Vec<MentionNotification> {
        let deadline = tokio::time::Instant::now() + tokio::time::Duration::from_secs(2);
        loop {
            let count = self.notifications.lock().await.len();
            if count >= expected || tokio::time::Instant::now() >= deadline {
                return self.notifications.lock().await.clone();
            }
            tokio::time::sleep(tokio::time::Duration::from_millis(10)).await;
        }
    }
}

#[async_trait::async_trait]
impl HumanMentionNotifyPort for RecordingNotify {
    fn is_available(&self) -> bool {
        true
    }

    async fn notify_mentioned_humans(
        &self,
        notification: MentionNotification,
    ) -> ServiceResult<()> {
        self.notifications.lock().await.push(notification);
        Ok(())
    }
}

fn gate_policy(
    mode: bcs_domain::HumanMentionNotifyMode,
    driver_bot_id: &str,
) -> PolicyGroupCore {
    PolicyGroupCore::core(mode, driver_bot_id)
}

fn mention_ids() -> Vec<String> {
    vec!["human_1".to_string()]
}

fn gate_context(sender_actor_id: &str) -> MentionNotifyContext {
    MentionNotifyContext {
        session_id: "group-1:s1".to_string(),
        group_id: "group-1".to_string(),
        group_name: None,
        sender_actor_id: sender_actor_id.to_string(),
        sender_label: sender_actor_id.to_string(),
        message_text: "please review".to_string(),
        timestamp_ms: 1,
    }
}

async fn run_gate(
    core: &PolicyGroupCore,
    port: &Option<Arc<dyn HumanMentionNotifyPort>>,
    sender_actor_id: &str,
    ids: Option<Vec<String>>,
) {
    spawn_human_mention_notify(
        core,
        port,
        &None,
        ids.as_deref(),
        &overlay(),
        gate_context(sender_actor_id),
    )
    .await;
}

async fn gate_port(
    recorder: &Arc<RecordingNotify>,
) -> Option<Arc<dyn HumanMentionNotifyPort>> {
    Some(recorder.clone() as Arc<dyn HumanMentionNotifyPort>)
}

/// Spec §8.5 helper matrix. The mention target stays a Human participant
/// of the overlay in every row — the gate is only about the sender.
#[tokio::test]
async fn policy_matrix_notifies_exactly_per_mode_and_sender() {
    use bcs_domain::HumanMentionNotifyMode;

    let matrix: Vec<(HumanMentionNotifyMode, &str, usize)> = vec![
        (HumanMentionNotifyMode::All, "bot-driver", 1),
        (HumanMentionNotifyMode::All, "bot-worker", 1),
        (HumanMentionNotifyMode::All, "human_3", 1),
        (HumanMentionNotifyMode::DriverBotOnly, "bot-driver", 1),
        (HumanMentionNotifyMode::DriverBotOnly, "bot-worker", 0),
        (HumanMentionNotifyMode::DriverBotOnly, "human_3", 0),
        (HumanMentionNotifyMode::None, "bot-driver", 0),
        (HumanMentionNotifyMode::None, "bot-worker", 0),
        (HumanMentionNotifyMode::None, "human_3", 0),
    ];
    for (row, (mode, sender, expected)) in matrix.into_iter().enumerate() {
        let core = gate_policy(mode, "bot-driver");
        let recorder = Arc::new(RecordingNotify::default());
        run_gate(&core, &gate_port(&recorder).await, sender, Some(mention_ids())).await;
        let notifications = if expected == 0 {
            // Suppression happens synchronously before any spawn, so no
            // notification can ever arrive once the helper has returned.
            recorder.notifications().await
        } else {
            recorder.wait_for(expected).await
        };
        assert_eq!(
            notifications.len(),
            expected,
            "row {row}: mode={mode:?} sender={sender}"
        );
        if expected == 1 {
            assert_eq!(notifications[0].mentioned.len(), 1);
            assert_eq!(notifications[0].mentioned[0].actor_id, "human_1");
            assert_eq!(notifications[0].mentioned[0].display_name, "Human One");
        }
    }
}

/// Fail-closed: a policy read resolving to a missing Group (Ok(None))
/// suppresses even though the sender would be allowed under `all`.
#[tokio::test]
async fn missing_policy_group_suppresses_notification() {
    let core = PolicyGroupCore::group_missing();
    let recorder = Arc::new(RecordingNotify::default());
    run_gate(
        &core,
        &gate_port(&recorder).await,
        "bot-driver",
        Some(mention_ids()),
    )
    .await;
    assert_eq!(core.read_count().await, 1);
    assert!(
        recorder.notifications().await.is_empty(),
        "missing policy must fail closed"
    );
}

/// Fail-closed: an errored policy read suppresses, one read attempt, no
/// retry.
#[tokio::test]
async fn failed_policy_read_suppresses_notification_without_retry() {
    let core = gate_policy(bcs_domain::HumanMentionNotifyMode::All, "bot-driver");
    core.set_fail_reads(true);
    let recorder = Arc::new(RecordingNotify::default());
    run_gate(
        &core,
        &gate_port(&recorder).await,
        "bot-driver",
        Some(mention_ids()),
    )
    .await;
    assert_eq!(core.read_count().await, 1, "exactly one read attempt");
    assert!(
        recorder.notifications().await.is_empty(),
        "failed policy read must fail closed"
    );
}

/// Zero new policy reads when the Port is unavailable, the mention ids are
/// missing, or no valid Human resolves; exactly one scoped read for an
/// eligible candidate.
#[tokio::test]
async fn policy_read_boundary_counts() {
    use bcs_domain::HumanMentionNotifyMode;

    async fn unavailable_port_probe(mode: HumanMentionNotifyMode) -> usize {
        let core = gate_policy(mode, "bot-driver");
        let disabled: Option<Arc<dyn HumanMentionNotifyPort>> = None;
        run_gate(&core, &disabled, "bot-driver", Some(mention_ids())).await;
        core.read_count().await
    }
    assert_eq!(
        unavailable_port_probe(HumanMentionNotifyMode::None).await,
        0,
        "absent Port must not read policy"
    );

    async fn unavailable_flag_probe() -> usize {
        struct Unavailable;
        #[async_trait::async_trait]
        impl HumanMentionNotifyPort for Unavailable {
            fn is_available(&self) -> bool {
                false
            }
            async fn notify_mentioned_humans(
                &self,
                _: MentionNotification,
            ) -> ServiceResult<()> {
                Ok(())
            }
        }
        let core = gate_policy(HumanMentionNotifyMode::None, "bot-driver");
        let port: Option<Arc<dyn HumanMentionNotifyPort>> =
            Some(Arc::new(Unavailable));
        run_gate(&core, &port, "bot-driver", Some(mention_ids())).await;
        core.read_count().await
    }
    assert_eq!(
        unavailable_flag_probe().await,
        0,
        "unavailable Port must not read policy"
    );

    let no_mentions = gate_policy(HumanMentionNotifyMode::None, "bot-driver");
    let recorder = Arc::new(RecordingNotify::default());
    run_gate(
        &no_mentions,
        &gate_port(&recorder).await,
        "bot-driver",
        None,
    )
    .await;
    assert_eq!(no_mentions.read_count().await, 0, "no mention ids: 0 reads");

    let no_valid_human = gate_policy(HumanMentionNotifyMode::All, "bot-driver");
    run_gate(
        &no_valid_human,
        &gate_port(&recorder).await,
        "bot-driver",
        Some(vec!["bot-worker".to_string(), "unknown".to_string(), "human_2".to_string()]),
    )
    .await;
    assert_eq!(
        no_valid_human.read_count().await,
        0,
        "bot-only/unknown/hidden mentions must not read policy"
    );

    let eligible = gate_policy(HumanMentionNotifyMode::All, "bot-driver");
    run_gate(
        &eligible,
        &gate_port(&recorder).await,
        "bot-driver",
        Some(mention_ids()),
    )
    .await;
    assert_eq!(
        eligible.read_count().await,
        1,
        "eligible candidate: exactly one scoped read"
    );
    assert_eq!(recorder.wait_for(1).await.len(), 1);
}

/// Repeated policy-read failures: one attempt per candidate, no retry,
/// no notification.
#[tokio::test]
async fn repeated_policy_failures_do_not_retry() {
    let core = gate_policy(bcs_domain::HumanMentionNotifyMode::All, "bot-driver");
    core.set_fail_reads(true);
    let recorder = Arc::new(RecordingNotify::default());
    let port = gate_port(&recorder).await;
    for candidate in 0..3 {
        run_gate(&core, &port, "bot-driver", Some(mention_ids())).await;
        assert!(
            recorder.notifications().await.is_empty(),
            "candidate {candidate} must not notify while reads fail"
        );
    }
    assert_eq!(core.read_count().await, 3, "one attempt per candidate");
    assert_eq!(recorder.notifications().await.len(), 0);
}

/// Concurrent eligible candidates: each performs exactly one scoped policy
/// read, and each notification completes; reads never scale per Human.
#[tokio::test]
async fn concurrent_candidates_read_once_each() {
    let core = Arc::new(gate_policy(
        bcs_domain::HumanMentionNotifyMode::All,
        "bot-driver",
    ));
    let recorder = Arc::new(RecordingNotify::default());
    let port = gate_port(&recorder).await;
    let candidates: Vec<_> = (0..8)
        .map(|_| {
            let core = core.clone();
            let port = port.clone();
            async move {
                spawn_human_mention_notify(
                    core.as_ref(),
                    &port,
                    &None,
                    Some(mention_ids().as_slice()),
                    &overlay(),
                    gate_context("bot-driver"),
                )
                .await;
            }
        })
        .collect();
    futures::future::join_all(candidates).await;
    assert_eq!(core.read_count().await, 8, "one read per candidate");
    let notifications = recorder.wait_for(8).await;
    assert_eq!(notifications.len(), 8, "one notification per candidate");
}

/// Every Human participant mentioned at once still costs exactly one
/// policy read — reads never scale per mentioned Human.
#[tokio::test]
async fn many_mentions_still_read_policy_once() {
    let core = gate_policy(bcs_domain::HumanMentionNotifyMode::All, "bot-driver");
    let recorder = Arc::new(RecordingNotify::default());
    let humans: Vec<String> = (1..=8).map(|n| format!("human_{n}")).collect();
    spawn_human_mention_notify(
        &core,
        &gate_port(&recorder).await,
        &None,
        Some(humans.as_slice()),
        &online_humans_overlay(8),
        gate_context("bot-driver"),
    )
    .await;
    assert_eq!(core.read_count().await, 1);
    let notifications = recorder.wait_for(1).await;
    assert_eq!(notifications.len(), 1);
    assert_eq!(notifications[0].mentioned.len(), 8);
}

/// Barrier: a Group patch committed while the message holds between its
/// early snapshot and the policy read is observed by the dedicated read —
/// the notification decision uses the committed value, and the patched
/// (mode, driver) pair arrives as one consistent snapshot.
#[tokio::test]
async fn held_candidate_uses_policy_committed_during_barrier() {
    use bcs_domain::HumanMentionNotifyMode;

    let core = Arc::new(gate_policy(HumanMentionNotifyMode::All, "bot-driver"));
    let recorder = Arc::new(RecordingNotify::default());
    let port = gate_port(&recorder).await;
    let release = core.hold_next_read().await;

    let task_core = core.clone();
    let task_port = port.clone();
    let task = tokio::spawn(async move {
        spawn_human_mention_notify(
            task_core.as_ref(),
            &task_port,
            &None,
            Some(mention_ids().as_slice()),
            &overlay(),
            gate_context("bot-driver"),
        )
        .await;
    });

    // The read is in flight before the patch is committed.
    core.wait_read_count(1).await;
    core.replace_policy(GroupHumanNotifyPolicy {
        mode: HumanMentionNotifyMode::None,
        driver_bot_id: "bot-worker".to_string(),
    });
    let _ = release.send(()).await;
    task.await.expect("helper task completes normally");

    let observed = core.observed_policy().await.expect("policy observed");
    assert_eq!(
        observed.mode,
        HumanMentionNotifyMode::None,
        "the dedicated read must observe the committed mode"
    );
    assert_eq!(observed.driver_bot_id, "bot-worker");
    assert_eq!(core.read_count().await, 1);
    assert!(
        recorder.notifications().await.is_empty(),
        "committed `none` must suppress"
    );
}

/// Barrier with a driver-only change: the read consumed during the hold
/// accepts exactly one consistent committed (mode, driver) snapshot —
/// never a mix of the pre- and post-patch pairs.
#[tokio::test]
async fn held_candidate_observes_one_consistent_committed_snapshot() {
    use bcs_domain::HumanMentionNotifyMode;

    let core = Arc::new(gate_policy(
        HumanMentionNotifyMode::DriverBotOnly,
        "bot-driver",
    ));
    let recorder = Arc::new(RecordingNotify::default());
    let port = gate_port(&recorder).await;
    let release = core.hold_next_read().await;

    let task_core = core.clone();
    let task_port = port.clone();
    let task = tokio::spawn(async move {
        spawn_human_mention_notify(
            task_core.as_ref(),
            &task_port,
            &None,
            Some(mention_ids().as_slice()),
            &overlay(),
            gate_context("bot-driver"),
        )
        .await;
    });

    core.wait_read_count(1).await;
    // Patch commits (mode unchanged, driver moved) while the read is held.
    core.replace_policy(GroupHumanNotifyPolicy {
        mode: HumanMentionNotifyMode::DriverBotOnly,
        driver_bot_id: "bot-worker".to_string(),
    });
    let _ = release.send(()).await;
    task.await.expect("helper task completes normally");

    // The read consumed after the patch must be exactly the committed
    // snapshot, so the pre-patch driver is no longer eligible.
    let observed = core.observed_policy().await.expect("policy observed");
    assert_eq!(observed.mode, HumanMentionNotifyMode::DriverBotOnly);
    assert_eq!(observed.driver_bot_id, "bot-worker");
    assert!(recorder.notifications().await.is_empty());
    assert_eq!(core.read_count().await, 1);
}

/// A notification task created by the helper is never retracted by a later
/// policy patch (there is no retract path and none is added).
#[tokio::test]
async fn created_notification_task_is_not_retracted_by_policy_patch() {
    use bcs_domain::HumanMentionNotifyMode;

    let core = gate_policy(HumanMentionNotifyMode::All, "bot-driver");
    let recorder = Arc::new(RecordingNotify::default());
    let port = gate_port(&recorder).await;
    run_gate(&core, &port, "bot-driver", Some(mention_ids())).await;
    core.replace_policy(GroupHumanNotifyPolicy {
        mode: HumanMentionNotifyMode::None,
        driver_bot_id: "bot-driver".to_string(),
    });
    let notifications = recorder.wait_for(1).await;
    assert_eq!(notifications.len(), 1, "already-created task completes");
    assert_eq!(core.read_count().await, 1);
}
