//! Conformance harness for `HumanMentionNotifier` implementations.

use bcs_human_notify_api::{
    HumanMentionNotifier, HumanNotifyError, MentionNotification, MentionedHuman,
};

/// Harness inputs describing how the host environment behaves for a specific
/// implementation. Implementations without failure injection leave the
/// failure cases as `None` and those assertions are skipped.
#[derive(Debug, Clone)]
pub struct HumanNotifyContractHarness {
    /// Backend name the implementation must report.
    pub backend_name: &'static str,
    /// A notification whose recipients all deliver successfully.
    pub success_case: MentionNotification,
    /// A notification whose recipients all fail (optional).
    pub failure_case: Option<MentionNotification>,
    /// A notification mixing one successful and one failing recipient
    /// (optional).
    pub partial_case: Option<MentionNotification>,
}

/// Runs the shared conformance assertions against a notifier implementation.
pub async fn human_mention_notifier_contract_tests<T>(
    notifier: &T,
    harness: HumanNotifyContractHarness,
) where
    T: HumanMentionNotifier + ?Sized,
{
    assert_eq!(
        notifier.backend_name(),
        harness.backend_name,
        "backend_name must match the registered factory name"
    );

    let mut empty = harness.success_case.clone();
    empty.mentioned.clear();
    notifier
        .notify(&empty)
        .await
        .expect("empty recipient list must succeed without delivery");

    notifier
        .notify(&harness.success_case)
        .await
        .expect("all-success notification must return Ok");

    if let Some(failure_case) = &harness.failure_case {
        let error = notifier
            .notify(failure_case)
            .await
            .expect_err("all-failure notification must return Err");
        assert!(
            matches!(error, HumanNotifyError::Delivery(_)),
            "all-failure notification must classify as Delivery, got: {error}"
        );
    }

    if let Some(partial_case) = &harness.partial_case {
        notifier
            .notify(partial_case)
            .await
            .expect("partial-failure notification must return Ok");
    }

    let mut invalid = harness.success_case.clone();
    invalid.mentioned = vec![MentionedHuman {
        actor_id: "not-a-human-prefix".to_string(),
        display_name: "Invalid".to_string(),
    }];
    let _ = notifier.notify(&invalid).await;
}
