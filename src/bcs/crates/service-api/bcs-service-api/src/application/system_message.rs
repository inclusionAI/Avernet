//! Application-level system-message service.

use async_trait::async_trait;
use bcs_domain::{Participant, SystemMessageEvent};

use crate::{ServiceResult, SystemMessageDispatchOutcome};

#[async_trait]
pub trait SystemMessageService: Send + Sync {
    async fn notify(
        &self,
        group_id: &str,
        event: SystemMessageEvent,
        session_id: &str,
        session_participants: &[Participant],
    ) -> ServiceResult<usize>;

    async fn notify_with_outcome(
        &self,
        group_id: &str,
        event: SystemMessageEvent,
        session_id: &str,
        session_participants: &[Participant],
    ) -> ServiceResult<SystemMessageDispatchOutcome> {
        let successful_deliveries = self
            .notify(group_id, event, session_id, session_participants)
            .await?;
        Ok(SystemMessageDispatchOutcome {
            total_recipients: successful_deliveries,
            successful_deliveries,
            failed_deliveries: 0,
            recipient_results: Vec::new(),
        })
    }
}

/// Resolves the `目标` (topic/reason) line for a session-context system message
/// from the first non-empty of: the session `input` (when it is a text value),
/// the group `context`, or the group `label`. Returns `None` when all are
/// empty/absent, in which case the `目标` line is omitted. Non-string input
/// values (objects/arrays) are ignored, falling through to `context`/`label`.
pub fn resolve_session_topic(
    input: Option<&serde_json::Value>,
    context: Option<&str>,
    label: Option<&str>,
) -> Option<String> {
    input
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .or_else(|| context.filter(|s| !s.is_empty()))
        .or_else(|| label.filter(|s| !s.is_empty()))
        .map(str::to_string)
}

#[cfg(test)]
mod tests {
    use super::resolve_session_topic;
    use serde_json::json;

    #[test]
    fn resolve_session_topic_prefers_input() {
        assert_eq!(
            resolve_session_topic(Some(&json!("input goal")), Some("ctx"), Some("lbl")),
            Some("input goal".to_string())
        );
    }

    #[test]
    fn resolve_session_topic_falls_back_to_context() {
        assert_eq!(
            resolve_session_topic(None, Some("ctx"), Some("lbl")),
            Some("ctx".to_string())
        );
    }

    #[test]
    fn resolve_session_topic_falls_back_to_label() {
        assert_eq!(
            resolve_session_topic(None, None, Some("lbl")),
            Some("lbl".to_string())
        );
    }

    #[test]
    fn resolve_session_topic_none_when_all_empty() {
        assert_eq!(resolve_session_topic(None, None, None), None);
        // empty strings are treated as absent
        assert_eq!(
            resolve_session_topic(Some(&json!("")), Some(""), Some("")),
            None
        );
        // non-string input value is ignored, falls through to label
        assert_eq!(
            resolve_session_topic(Some(&json!({"q": 1})), None, Some("lbl")),
            Some("lbl".to_string())
        );
    }
}
