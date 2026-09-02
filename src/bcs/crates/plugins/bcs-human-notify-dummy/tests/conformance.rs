//! Conformance tests for the dummy human mention notifier.

use std::sync::Arc;

use bcs_human_notify_api::{MentionNotification, MentionedHuman};
use bcs_human_notify_dummy::DummyHumanMentionNotifier;
use bcs_test_support::contract::port::{
    human_mention_notifier_contract_tests, HumanNotifyContractHarness,
};

fn sample_notification() -> MentionNotification {
    MentionNotification {
        session_id: "group-1:abcdef12".to_string(),
        group_id: "group-1".to_string(),
        sender_actor_id: "bot-driver".to_string(),
        sender_label: "Driver".to_string(),
        mentioned: vec![MentionedHuman {
            actor_id: "human_1".to_string(),
            display_name: "Human One".to_string(),
        }],
        message_text: "hello @Human One".to_string(),
        timestamp_ms: 1_700_000_000_000,
    }
}

#[tokio::test]
async fn dummy_conforms() {
    let notifier: Arc<dyn bcs_human_notify_api::HumanMentionNotifier> =
        Arc::new(DummyHumanMentionNotifier::new());
    human_mention_notifier_contract_tests(
        notifier.as_ref(),
        HumanNotifyContractHarness {
            backend_name: "dummy",
            success_case: sample_notification(),
            failure_case: None,
            partial_case: None,
        },
    )
    .await;
}
