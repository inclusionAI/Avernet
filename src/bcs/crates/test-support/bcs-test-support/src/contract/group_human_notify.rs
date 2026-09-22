//! Shared Group human-mention notify persistence contract.
//!
//! Concrete Group repositories invoke [`group_human_notify_contract`] from
//! their `tests/conformance_*.rs` suites:
//!
//! - the Memory repo factory returns a clone of the same shared `Arc`;
//! - SQL stores return a NEW store over the same database on every call
//!   (including after every Patch) so every read is a cold read that proves
//!   persistence instead of cache-only behavior.
//!
//! A single reused reader would retain its own stale cache and is not a
//! cold-read proof.

use std::sync::Arc;

use bcs_domain::{Participant, ParticipantRole};
use bcs_service_api::port::repo::{
    CommitGroupEventfulMutation, GroupEventfulMutation, GroupRepoPort,
};
use bcs_service_api::types::GroupMutableFieldsPatch;
use bcs_service_api::{Group, HumanMentionNotifyMode};

/// Exercise every Store-visible behavior of `human_mention_notify_mode`:
/// upsert round-trip with cold reads and scoped policy reads, omitted and
/// ordinary direct Patches, eventful versioned Patches, unrelated-field
/// isolation, and missing-Group behavior.
pub async fn group_human_notify_contract(
    writer: &dyn GroupRepoPort,
    fresh_reader: impl Fn() -> Arc<dyn GroupRepoPort>,
) {
    let group = Group::new(
        "notify-contract",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    assert_eq!(
        group.human_mention_notify_mode,
        HumanMentionNotifyMode::All,
        "Group::new must default the human mention notify mode to All"
    );

    for mode in [
        HumanMentionNotifyMode::All,
        HumanMentionNotifyMode::DriverBotOnly,
        HumanMentionNotifyMode::None,
    ] {
        let mut configured = group.clone();
        configured.human_mention_notify_mode = mode;
        writer.upsert(configured).await.expect("persist mode");

        let reader = fresh_reader();
        let loaded = reader
            .try_get(&group.id)
            .await
            .expect("cold read")
            .expect("group");
        assert_eq!(loaded.human_mention_notify_mode, mode);

        let policy = reader
            .read_human_notify_policy(&group.id)
            .await
            .expect("read policy")
            .expect("policy");
        assert_eq!(policy.mode, mode);
        assert_eq!(policy.driver_bot_id, "driver");
    }

    // Omitted Patch: `GroupMutableFieldsPatch::default()` leaves the stored
    // mode untouched.
    writer
        .patch_mutable_fields(&group.id, GroupMutableFieldsPatch::default())
        .await
        .expect("omitted patch is a no-op");
    let reader = fresh_reader();
    let loaded = reader
        .try_get(&group.id)
        .await
        .expect("cold read after omitted patch")
        .expect("group");
    assert_eq!(loaded.human_mention_notify_mode, HumanMentionNotifyMode::None);

    // Ordinary Patch that changes only the mode.
    writer
        .patch_mutable_fields(
            &group.id,
            GroupMutableFieldsPatch {
                human_mention_notify_mode: Some(HumanMentionNotifyMode::DriverBotOnly),
                ..Default::default()
            },
        )
        .await
        .expect("mode-only patch");
    let loaded = fresh_reader()
        .try_get(&group.id)
        .await
        .expect("cold read after mode patch")
        .expect("group");
    assert_eq!(
        loaded.human_mention_notify_mode,
        HumanMentionNotifyMode::DriverBotOnly
    );

    // Unrelated-field isolation: patching an unrelated mutable field must not
    // disturb the stored mode.
    writer
        .patch_mutable_fields(
            &group.id,
            GroupMutableFieldsPatch {
                label: Some("renamed-by-contract".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("label patch");
    let loaded = fresh_reader()
        .try_get(&group.id)
        .await
        .expect("cold read after label patch")
        .expect("group");
    assert_eq!(loaded.label.as_deref(), Some("renamed-by-contract"));
    assert_eq!(
        loaded.human_mention_notify_mode,
        HumanMentionNotifyMode::DriverBotOnly
    );

    // Eventful versioned Patch of the mode via the raw Repo primitive.
    let current = fresh_reader()
        .try_get(&group.id)
        .await
        .expect("cold read before eventful patch")
        .expect("group");
    let updated = writer
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: group.id.clone(),
            expected_version: current.version,
            mutated_at_ms: current.updated_at.max(1),
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                human_mention_notify_mode: Some(HumanMentionNotifyMode::None),
                ..Default::default()
            }),
            event: None,
        })
        .await
        .expect("eventful mode patch");
    assert_eq!(updated.human_mention_notify_mode, HumanMentionNotifyMode::None);
    assert_eq!(
        updated.version,
        current.version.checked_add(1).expect("version overflow")
    );
    let reloaded = fresh_reader()
        .try_get(&group.id)
        .await
        .expect("cold read after eventful patch")
        .expect("group");
    assert_eq!(reloaded.human_mention_notify_mode, HumanMentionNotifyMode::None);
    assert_eq!(reloaded.version, updated.version);

    // Stale expected versions must conflict instead of bypassing optimistic
    // versioning.
    let stale = writer
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: group.id.clone(),
            expected_version: current.version,
            mutated_at_ms: current.updated_at.max(1),
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                human_mention_notify_mode: Some(HumanMentionNotifyMode::DriverBotOnly),
                ..Default::default()
            }),
            event: None,
        })
        .await;
    assert!(
        stale.is_err(),
        "stale expected_version must be rejected by the eventful primitive"
    );
    let still_none = fresh_reader()
        .try_get(&group.id)
        .await
        .expect("cold read after stale eventful patch")
        .expect("group");
    assert_eq!(
        still_none.human_mention_notify_mode,
        HumanMentionNotifyMode::None
    );

    // Missing Groups read as None instead of an error.
    assert_eq!(
        fresh_reader()
            .read_human_notify_policy("notify-contract-missing")
            .await
            .expect("missing Group policy read is None, not an error"),
        None
    );
}
