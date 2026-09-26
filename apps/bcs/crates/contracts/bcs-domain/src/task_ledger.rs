//! Task ledger summary shared by task coordination callers.

use serde::{Deserialize, Serialize};

#[derive(Debug, Default, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LedgerSummary {
    pub pending: Vec<String>,
    pub replied: Vec<String>,
    pub failed: Vec<String>,
    #[serde(default)]
    pub cancelled: Vec<String>,
    pub timed_out: Vec<String>,
}

#[cfg(test)]
mod tests {
    #[test]
    fn old_summary_defaults_cancelled_without_merging_it_into_failed() {
        let mut summary: super::LedgerSummary = serde_json::from_value(serde_json::json!({
            "pending":[], "replied":[], "failed":[], "timed_out":[]
        })).unwrap();
        assert!(summary.cancelled.is_empty());
        summary.cancelled.push("Worker".into());
        assert!(summary.failed.is_empty());
        assert_eq!(serde_json::to_value(summary).unwrap()["cancelled"], serde_json::json!(["Worker"]));
    }
}
